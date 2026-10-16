"""MYCRO capture provider plugin for OBS.

A persistent CLIENT of the MYCRO device (:7373, protocol v1 — see
docs/protocol.md ADR-2). After the auth prologue it announces itself
with an img.hello push, then serves reverse requests from the device.

The provider has exactly one verb — "current frame" — with extraction
selectors (stateless: every request takes a fresh screenshot):

    img.frame {rect:{x,y,w,h}}           -> native-pixel lossless RGB8 stream
    img.frame {points:[[x,y],...]}       -> native-pixel exact RGB colors
    img.frame {w,h, format:"rgb8"|"gray8"} -> whole source, resampled to
                                            w,h in the requested format
                                            (format is explicit; nothing
                                            is ever silently downgraded)

All coordinates are native source pixels. Responses carry the source's
native width/height (plus crop_w/crop_h for clamped rects). OBS rejects
concurrent screenshots, so captures are serialized on a lock.

gray8 uses integer BT.601 luma (r*77 + g*150 + b*29) >> 8 — bit-exact
with src/lib/imgmatch.py and tests/device/reference_provider.py
(Pillow's convert("L") does NOT match it).

Requires Pillow installed into the same Python OBS loads
(tests/obs/obs_selftest.py T3 verifies). Load: Tools -> Scripts -> "+".
"""

import obspython as obs

import hashlib
import json
import os
import socket
import struct
import threading
import time

from PIL import Image  # Pillow, into the same Python OBS loads (selftest T3)

# Pillow compatibility: Image.Resampling.BILINEAR on Pillow >=9.1, Image.BILINEAR on older
RESAMPLE = getattr(getattr(Image, "Resampling", Image), "BILINEAR", Image.BILINEAR)

PROTO_VERSION = 1
AUTH_MAGIC = b"MYCR"
DEFAULT_PORT = 7373
CHUNK_RAW = 8192 - 4  # binary frame payload minus u16 stream_id + u16 flags
FLAG_STREAM_END = 1

MAX_POINTS = 64
MAX_RECT_EDGE = 512
MAX_FRAME_W = 160
MAX_FRAME_H = 120
SHOT_TIMEOUT_S = 5.0
RECONNECT_S = 3.0

# --- settings ---
source_name = ""
device_host = ""
device_port = DEFAULT_PORT
device_token = ""
auto_connect = False
_current_settings = None

# --- runtime state ---
_stop = threading.Event()            # script_unload: tear down entire worker
_active = threading.Event()          # user enable/connect: False = idle / disconnected
_wake = threading.Event()            # wake worker thread on state change / demand
_connected_event = threading.Event() # set when handshake completes; cleared on drop
_thread = None
_sock = None                         # closed from script_unload/disconnect to wake worker
_shot = None                         # {"event": threading.Event} while capturing
_cap_lock = threading.Lock()
_stream_id = 0
_status_text = "Disconnected"


# ---------- Status and UI helpers ----------

def is_connected():
    """True if actively connected and handshake complete."""
    return _active.is_set() and _connected_event.is_set() and _sock is not None


def get_status_summary():
    """Human-readable comprehensive status including source validation."""
    if not _active.is_set():
        return "Disconnected (idle)"

    if not is_connected():
        return _status_text

    # Connected: inspect the configured capture source
    base = "Connected to %s:%d" % (device_host, device_port)
    if not source_name:
        return base + " [WARNING: No capture source selected!]"

    src = obs.obs_get_source_by_name(source_name)
    if src is None:
        return base + " [WARNING: Source '%s' not found!]" % source_name

    try:
        w = obs.obs_source_get_width(src) if hasattr(obs, "obs_source_get_width") else 1
        h = obs.obs_source_get_height(src) if hasattr(obs, "obs_source_get_height") else 1
        if w <= 0 or h <= 0:
            return base + " [WARNING: Source '%s' has 0x0 size!]" % source_name
    finally:
        obs.obs_source_release(src)

    return base + " (Source: '%s' %dx%d)" % (source_name, w, h)


def update_status_property():
    """Update settings object with latest status so OBS widget always displays fresh text."""
    summary = get_status_summary()
    if _current_settings is not None:
        obs.obs_data_set_string(_current_settings, "status_info", summary)
    return summary


# ---------- OBS script boilerplate ----------

def script_description():
    return ("MYCRO capture provider: serves img.frame extraction requests "
            "from the device.\n"
            "Live connection and capture source status are displayed in Properties below.")


def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "port", DEFAULT_PORT)
    obs.obs_data_set_default_bool(settings, "auto_connect", False)


def script_update(settings):
    global source_name, device_host, device_port, device_token, auto_connect, _current_settings
    _current_settings = settings
    new = (obs.obs_data_get_string(settings, "source"),
           obs.obs_data_get_string(settings, "host"),
           obs.obs_data_get_int(settings, "port") or DEFAULT_PORT,
           obs.obs_data_get_string(settings, "token"),
           obs.obs_data_get_bool(settings, "auto_connect"))
    changed_net = (device_host, device_port, device_token) != (new[1], new[2], new[3])
    source_name, device_host, device_port, device_token, auto_connect = new
    update_status_property()
    # If network settings changed while actively connected, reconnect to the new target
    if changed_net and _active.is_set():
        disconnect()
        start_connect(wait_timeout=0)


def start_connect(wait_timeout=1.2):
    """Manually start connection. If already connected, refreshes state.
    Waits up to wait_timeout seconds so properties UI immediately shows Connected.
    """
    global _sock, _status_text
    if is_connected():
        _status_text = "Connected to %s:%d" % (device_host, device_port)
        obs.script_log(obs.LOG_INFO, "mycro: already connected to %s:%d" % (device_host, device_port))
        update_status_property()
        return True

    _connected_event.clear()
    if not device_host:
        _status_text = "Error: No device IP configured"
        obs.script_log(obs.LOG_WARNING, "mycro: no device IP configured")
        update_status_property()
        return False

    _status_text = "Connecting to %s:%d..." % (device_host, device_port)
    obs.script_log(obs.LOG_INFO, "mycro: connect requested")
    _active.set()
    _wake.set()

    # Wait briefly for local socket connection to establish so the button click
    # immediately updates UI to "Connected" on return
    if wait_timeout > 0:
        _connected_event.wait(wait_timeout)

    update_status_property()
    return is_connected()


def disconnect():
    """Manually disconnect and remain idle."""
    global _sock, _status_text
    _status_text = "Disconnected"
    obs.script_log(obs.LOG_INFO, "mycro: disconnect requested")
    _active.clear()
    _connected_event.clear()
    s = _sock
    if s is not None:
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass
        _sock = None
    _wake.set()
    update_status_property()


def on_toggle_connect_clicked(props, prop):
    if is_connected():
        disconnect()
    else:
        start_connect(wait_timeout=1.2)
    new_label = "Disconnect" if is_connected() else "Connect"
    if hasattr(obs, "obs_property_set_description"):
        obs.obs_property_set_description(prop, new_label)
    update_status_property()
    return True


def on_refresh_clicked(props, prop):
    summary = update_status_property()
    obs.script_log(obs.LOG_INFO, "mycro: status refresh -> " + summary)
    btn = obs.obs_properties_get(props, "btn_toggle_connect")
    if btn and hasattr(obs, "obs_property_set_description"):
        obs.obs_property_set_description(btn, "Disconnect" if is_connected() else "Connect")
    return True


def script_properties():
    props = obs.obs_properties_create()

    # Status summary display: static row label on the left, live dynamic status in the text widget on the right
    summary = update_status_property()
    p_status = obs.obs_properties_add_text(
        props, "status_info", "Connection status",
        getattr(obs, "OBS_TEXT_INFO", obs.OBS_TEXT_DEFAULT))
    if hasattr(obs, "obs_property_set_enabled"):
        obs.obs_property_set_enabled(p_status, False)
    if hasattr(obs, "obs_property_text_set_info_word_wrap"):
        obs.obs_property_text_set_info_word_wrap(p_status, True)
    if hasattr(obs, "obs_property_text_set_info_type"):
        if is_connected():
            itype = getattr(obs, "OBS_TEXT_INFO_WARNING", 2) if "WARNING" in summary else getattr(obs, "OBS_TEXT_INFO_NORMAL", 1)
        elif "Error" in summary or "link down" in summary:
            itype = getattr(obs, "OBS_TEXT_INFO_ERROR", 3)
        else:
            itype = getattr(obs, "OBS_TEXT_INFO_NORMAL", 1)
        obs.obs_property_text_set_info_type(p_status, itype)

    lst = obs.obs_properties_add_list(
        props, "source", "Capture source",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    obs.obs_property_list_add_string(lst, "--- Select a Capture Source ---", "")

    # Enumerate video-capable sources only (skip audio-only sources)
    sources = obs.obs_enum_sources()
    if sources:
        video_flag = getattr(obs, "OBS_SOURCE_VIDEO", 1)
        for s in sources:
            flags = obs.obs_source_get_output_flags(s) if hasattr(obs, "obs_source_get_output_flags") else video_flag
            if flags & video_flag:
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(lst, name, name)
        obs.source_list_release(sources)

    obs.obs_properties_add_text(props, "host", "Device IP", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "port", "Device port", 1, 65535, 1)
    obs.obs_properties_add_text(props, "token", "Device token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_bool(props, "auto_connect", "Auto-connect on OBS launch")

    btn_label = "Disconnect" if is_connected() else "Connect"
    obs.obs_properties_add_button(props, "btn_toggle_connect",
                                  btn_label,
                                  on_toggle_connect_clicked)
    obs.obs_properties_add_button(props, "refresh",
                                  "Refresh Status",
                                  on_refresh_clicked)
    return props


def script_load(settings):
    global _thread, _current_settings
    _current_settings = settings
    obs.obs_frontend_add_event_callback(on_frontend_event)
    _stop.clear()
    if obs.obs_data_get_bool(settings, "auto_connect"):
        obs.script_log(obs.LOG_INFO, "mycro: auto-connecting on load")
        _active.set()
    else:
        obs.script_log(obs.LOG_INFO, "mycro: loaded idle (manual connect required)")
        _active.clear()
    _thread = threading.Thread(target=worker, daemon=True)
    _thread.start()


def script_unload():
    global _sock, _shot
    _stop.set()
    _active.clear()
    _connected_event.clear()
    _wake.set()
    s = _sock
    if s is not None:
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        s.close()
    if _shot is not None:
        _shot["event"].set()  # wake a stuck capture wait
    obs.obs_frontend_remove_event_callback(on_frontend_event)
    if _thread is not None:
        _thread.join(timeout=3)


def on_frontend_event(event):
    if event == obs.OBS_FRONTEND_EVENT_SCREENSHOT_TAKEN and _shot is not None:
        _shot["event"].set()


# ---------- capture ----------

class ImgFail(Exception):
    pass


def _grab_file():
    """Fresh screenshot of the configured source; returns the file path.
    The caller reads and deletes the file."""
    global _shot
    if not source_name:
        raise ImgFail("no capture source configured in OBS script settings")
    with _cap_lock:
        src = obs.obs_get_source_by_name(source_name)
        if src is None:
            raise ImgFail("capture source %r not found (was it renamed or deleted?)" % source_name)
        try:
            w = obs.obs_source_get_width(src) if hasattr(obs, "obs_source_get_width") else 1
            h = obs.obs_source_get_height(src) if hasattr(obs, "obs_source_get_height") else 1
            if w <= 0 or h <= 0:
                raise ImgFail("source %r has no video output or dimensions are 0x0 (inactive/minimized?)" % source_name)
            _shot = {"event": threading.Event()}
            obs.obs_frontend_take_source_screenshot(src)
        finally:
            obs.obs_source_release(src)

        if not _shot["event"].wait(SHOT_TIMEOUT_S):
            _shot = None
            raise ImgFail("screenshot timed out after %.1fs (source not rendering?)" % SHOT_TIMEOUT_S)
        _shot = None
        path = obs.obs_frontend_get_last_screenshot()
        if not path or not os.path.exists(path):
            raise ImgFail("screenshot file not found on disk: %r" % path)
        return path


def capture():
    """Fresh screenshot as a PIL RGB image."""
    path = _grab_file()
    try:
        with Image.open(path) as im:
            return im.convert("RGB").copy()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def capture_png():
    """Fresh screenshot as (png_bytes, width, height) — the file's bytes
    pass through untouched (no decode, no re-encode)."""
    path = _grab_file()
    try:
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ImgFail("screenshot is not a valid PNG (HDR/JXR capture?)")
    w, h = struct.unpack(">II", data[16:24])
    return data, w, h


def gray_of(rgb_bytes):
    """Integer BT.601 luma, bit-exact with src/lib/imgmatch.py."""
    out = bytearray(len(rgb_bytes) // 3)
    j = 0
    for i in range(0, len(rgb_bytes), 3):
        out[j] = (rgb_bytes[i] * 77 + rgb_bytes[i + 1] * 150
                  + rgb_bytes[i + 2] * 29) >> 8
        j += 1
    return bytes(out)


# ---------- protocol ----------

def send_env(s, type_, rid, payload):
    body = json.dumps({"v": PROTO_VERSION, "type": type_, "id": rid,
                       "ts": int(time.monotonic() * 1000),
                       "payload": payload}).encode("utf-8")
    s.sendall(struct.pack("<I", len(body)) + body)


def send_error(s, rid, code, message):
    send_env(s, "error", rid, {"code": code, "message": message})


def send_stream(s, ok_type, rid, meta, data):
    """Metadata envelope (with stream_id) followed by kind-1 chunks."""
    global _stream_id
    _stream_id = (_stream_id + 1) & 0xFFFF
    sid = _stream_id
    chunks = (len(data) + CHUNK_RAW - 1) // CHUNK_RAW
    meta = dict(meta, stream_id=sid, chunks=chunks,
                bytes=len(data))
    send_env(s, ok_type, rid, meta)
    for i in range(chunks):
        part = data[i * CHUNK_RAW:(i + 1) * CHUNK_RAW]
        flags = FLAG_STREAM_END if i == chunks - 1 else 0
        payload = struct.pack("<HH", sid, flags) + part
        s.sendall(struct.pack("<I", 0x80000000 | len(payload)) + payload)


def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        if _stop.is_set() or not _active.is_set():
            raise ConnectionError("stopping")
        try:
            chunk = s.recv(n - len(buf))
        except (socket.timeout, TimeoutError):
            continue  # poll _stop and _active between 1s recv timeouts
        if not chunk:
            raise ConnectionError("device closed the connection")
        buf += chunk
    return bytes(buf)


def handle(s, env):
    if not isinstance(env, dict):
        return
    t = env.get("type")
    rid = env.get("id", 0)
    # Server-initiated pushes (macro.result, net.msg, etc.) use id == 0.
    # Terminal responses (.ok) and error notices must also never trigger error replies.
    # The capture provider only handles reverse requests (id != 0);
    # pushes, stray responses, and error notices must be ignored.
    if rid == 0 or t == "error" or str(t).endswith(".ok"):
        return
    p = env.get("payload", {})
    if not isinstance(p, dict):
        send_error(s, rid, "bad_payload", "payload must be an object")
        return
    try:
        if t == "img.frame":
            handle_frame(s, rid, p)
        else:
            send_error(s, rid, "unknown_type", "unknown type: %r" % t)
    except ImgFail as e:
        send_error(s, rid, "capture_failed", str(e))
    except (KeyError, TypeError, ValueError) as e:
        send_error(s, rid, "field", "%s: %s" % (type(e).__name__, e))


def handle_frame(s, rid, p):
    """The single current-frame verb, dispatched by extraction selector."""
    if "rect" in p:
        return _rect(s, rid, p["rect"])
    if "points" in p:
        return _points(s, rid, p["points"])
    if str(p.get("format", "")) == "png":
        return _png(s, rid)
    if "w" in p and "h" in p:
        return _scaled(s, rid, int(p["w"]), int(p["h"]),
                       str(p.get("format", "")))
    raise ImgFail("img.frame needs one of: rect / points / w+h / format:png")


def _png(s, rid):
    """Whole source, native resolution, as the untouched PNG file bytes
    (the Pixel Assist preview path — nothing is re-encoded anywhere)."""
    data, w, h = capture_png()
    send_stream(s, "img.frame.ok", rid,
                {"width": w, "height": h, "format": "png"}, data)


def _rect(s, rid, rect):
    """Lossless native-pixel crop; never resampled."""
    x, y, w, h = (int(rect[k]) for k in ("x", "y", "w", "h"))
    if not (1 <= w <= MAX_RECT_EDGE and 1 <= h <= MAX_RECT_EDGE):
        raise ImgFail("rect edge capped at %d" % MAX_RECT_EDGE)
    im = capture()
    W, H = im.size
    if W <= 0 or H <= 0:
        raise ImgFail("empty image from capture source (%dx%d)" % (W, H))
    x0 = max(x, 0)
    y0 = max(y, 0)
    x1 = min(x + w, W)
    y1 = min(y + h, H)
    if x1 <= x0 or y1 <= y0:
        raise ImgFail("crop rect [%d,%d,%d,%d] outside source bounds (%dx%d)"
                      % (x, y, w, h, W, H))
    out = im.crop((x0, y0, x1, y1))
    send_stream(s, "img.frame.ok", rid,
                {"width": W, "height": H,
                 "crop_w": x1 - x0, "crop_h": y1 - y0,
                 "format": "rgb8"}, out.tobytes())


def _points(s, rid, points):
    """Exact colors at native pixels."""
    if not (1 <= len(points) <= MAX_POINTS):
        raise ImgFail("points capped at %d" % MAX_POINTS)
    im = capture()
    W, H = im.size
    if W <= 0 or H <= 0:
        raise ImgFail("empty image from capture source (%dx%d)" % (W, H))
    px = im.load()
    colors = []
    for x, y in points:
        nx = min(max(int(x), 0), W - 1)
        ny = min(max(int(y), 0), H - 1)
        colors.append(list(px[nx, ny][:3]))
    send_env(s, "img.frame.ok", rid, {"width": W, "height": H,
                                      "format": "colors",
                                      "colors": colors})


def _scaled(s, rid, w, h, fmt):
    """Whole source resampled to w,h in an EXPLICIT format."""
    if fmt not in ("rgb8", "gray8"):
        raise ImgFail("w,h selector requires format: rgb8 | gray8")
    if not (1 <= w <= MAX_FRAME_W and 1 <= h <= MAX_FRAME_H):
        raise ImgFail("frame capped at %dx%d" % (MAX_FRAME_W, MAX_FRAME_H))
    im = capture()
    W, H = im.size
    if W <= 0 or H <= 0:
        raise ImgFail("empty image from capture source (%dx%d)" % (W, H))
    out = im.resize((w, h), RESAMPLE).tobytes()
    if fmt == "gray8":
        out = gray_of(out)
    send_stream(s, "img.frame.ok", rid,
                {"width": W, "height": H, "frame_w": w, "frame_h": h,
                 "format": fmt}, out)


# ---------- worker ----------

def serve(s):
    """One connection's request loop (blocking)."""
    global _sock, _status_text
    _sock = s
    digest = hashlib.sha256(device_token.encode("utf-8")).digest()
    s.sendall(AUTH_MAGIC + bytes((PROTO_VERSION,)) + digest)
    send_env(s, "img.hello", 0, {"caps": ["frame"]})
    _status_text = "Connected to %s:%d" % (device_host, device_port)
    _connected_event.set()
    obs.script_log(obs.LOG_INFO, "mycro: connected to %s:%d"
                   % (device_host, device_port))
    try:
        while not _stop.is_set() and _active.is_set():
            hdr = recv_exact(s, 4)
            (h,) = struct.unpack("<I", hdr)
            kind, length = h >> 31, h & 0x7FFFFFFF
            if length > 8192:
                raise ConnectionError("oversize frame (%d B)" % length)
            payload = recv_exact(s, length)
            if kind == 0:
                handle(s, json.loads(payload.decode("utf-8")))
            # kind 1 (binary) never flows device->provider; ignore
    finally:
        _connected_event.clear()


def worker():
    global _sock, _status_text
    while not _stop.is_set():
        if not _active.is_set():
            _wake.wait()
            _wake.clear()
            continue

        if not device_host:
            _status_text = "Error: No device IP configured"
            obs.script_log(obs.LOG_WARNING, "mycro: no device IP configured")
            _active.clear()
            continue

        _status_text = "Connecting to %s:%d..." % (device_host, device_port)
        obs.script_log(obs.LOG_INFO, "mycro: connecting to %s:%d..."
                       % (device_host, device_port))
        s = None
        try:
            s = socket.create_connection((device_host, device_port),
                                         timeout=5.0)
            s.settimeout(1.0)  # lets recv_exact notice _stop / _active
            serve(s)
        except (OSError, ConnectionError) as e:
            if _active.is_set() and not _stop.is_set():
                _status_text = "Disconnected (%s)" % e
                obs.script_log(obs.LOG_WARNING, "mycro: link down: %s" % e)
        except Exception as e:
            if _active.is_set() and not _stop.is_set():
                _status_text = "Error (%s)" % e
                obs.script_log(obs.LOG_WARNING, "mycro: worker error: %s" % e)
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
            _sock = None

        if _active.is_set() and not _stop.is_set():
            if auto_connect:
                _status_text = "Reconnecting in %ds..." % int(RECONNECT_S)
                _wake.wait(RECONNECT_S)
                _wake.clear()
            else:
                _active.clear()
                _status_text = "Disconnected"
                obs.script_log(obs.LOG_INFO, "mycro: link closed; remaining idle (click Connect to reconnect)")
        elif not _active.is_set() and not _status_text.startswith("Error"):
            _status_text = "Disconnected"
