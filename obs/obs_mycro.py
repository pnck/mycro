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

RESAMPLE = Image.Resampling.BILINEAR

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

# --- runtime state ---
_stop = threading.Event()
_reconnect = threading.Event()  # connect button / settings change
_thread = None
_sock = None           # closed from script_unload to wake the worker
_shot = None           # {"event": threading.Event} while capturing
_cap_lock = threading.Lock()
_stream_id = 0


# ---------- OBS script boilerplate ----------

def script_description():
    return ("MYCRO capture provider: serves img.frame extraction requests "
            "from the device. See Script Log for connection state.")


def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "port", DEFAULT_PORT)


def script_update(settings):
    global source_name, device_host, device_port, device_token
    new = (obs.obs_data_get_string(settings, "source"),
           obs.obs_data_get_string(settings, "host"),
           obs.obs_data_get_int(settings, "port"),
           obs.obs_data_get_string(settings, "token"))
    changed = (source_name, device_host, device_port, device_token) != new
    source_name, device_host, device_port, device_token = new
    if changed and _thread is not None:
        request_reconnect()  # editing the IP must not wait for a timeout


def request_reconnect():
    """Drop any live connection and reconnect immediately."""
    _reconnect.set()
    s = _sock
    if s is not None:
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def on_connect_clicked(props, prop):
    request_reconnect()
    return True


def script_properties():
    props = obs.obs_properties_create()
    lst = obs.obs_properties_add_list(
        props, "source", "Capture source",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING)
    sources = obs.obs_enum_sources()
    if sources:
        for s in sources:
            name = obs.obs_source_get_name(s)
            obs.obs_property_list_add_string(lst, name, name)
        obs.source_list_release(sources)
    obs.obs_properties_add_text(props, "host", "Device IP", obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "port", "Device port", 1, 65535, 1)
    obs.obs_properties_add_text(props, "token", "Device token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_button(props, "connect", "Connect / Reconnect",
                                  on_connect_clicked)
    return props


def script_load(settings):
    global _thread
    obs.obs_frontend_add_event_callback(on_frontend_event)
    _stop.clear()
    _thread = threading.Thread(target=worker, daemon=True)
    _thread.start()


def script_unload():
    global _sock, _shot
    _stop.set()
    _reconnect.set()  # wake a worker sleeping in backoff
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


def capture():
    """Fresh screenshot of the configured source as a PIL RGB image."""
    global _shot
    if not source_name:
        raise ImgFail("no capture source configured")
    with _cap_lock:
        src = obs.obs_get_source_by_name(source_name)
        if src is None:
            raise ImgFail("source not found: %r" % source_name)
        _shot = {"event": threading.Event()}
        obs.obs_frontend_take_source_screenshot(src)
        obs.obs_source_release(src)
        if not _shot["event"].wait(SHOT_TIMEOUT_S):
            _shot = None
            raise ImgFail("screenshot timed out")
        _shot = None
        path = obs.obs_frontend_get_last_screenshot()
        try:
            with Image.open(path) as im:
                return im.convert("RGB").copy()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


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
    meta = dict(meta, stream_id=sid, chunks=chunks)
    send_env(s, ok_type, rid, meta)
    for i in range(chunks):
        part = data[i * CHUNK_RAW:(i + 1) * CHUNK_RAW]
        flags = FLAG_STREAM_END if i == chunks - 1 else 0
        payload = struct.pack("<HH", sid, flags) + part
        s.sendall(struct.pack("<I", 0x80000000 | len(payload)) + payload)


def recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        if _stop.is_set():
            raise ConnectionError("stopping")
        try:
            chunk = s.recv(n - len(buf))
        except (socket.timeout, TimeoutError):
            continue  # poll _stop between 1s recv timeouts
        if not chunk:
            raise ConnectionError("device closed the connection")
        buf += chunk
    return bytes(buf)


def handle(s, env):
    t = env.get("type")
    rid = env.get("id", 0)
    p = env.get("payload", {})
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
    if "w" in p and "h" in p:
        return _scaled(s, rid, int(p["w"]), int(p["h"]),
                       str(p.get("format", "")))
    raise ImgFail("img.frame needs one of: rect / points / w+h")


def _rect(s, rid, rect):
    """Lossless native-pixel crop; never resampled."""
    x, y, w, h = (int(rect[k]) for k in ("x", "y", "w", "h"))
    if not (1 <= w <= MAX_RECT_EDGE and 1 <= h <= MAX_RECT_EDGE):
        raise ImgFail("rect edge capped at %d" % MAX_RECT_EDGE)
    im = capture()
    W, H = im.size
    box = (max(x, 0), max(y, 0), min(x + w, W), min(y + h, H))
    out = im.crop(box)
    send_stream(s, "img.frame.ok", rid,
                {"width": W, "height": H,
                 "crop_w": box[2] - box[0], "crop_h": box[3] - box[1],
                 "format": "rgb8"}, out.tobytes())


def _points(s, rid, points):
    """Exact colors at native pixels."""
    if not (1 <= len(points) <= MAX_POINTS):
        raise ImgFail("points capped at %d" % MAX_POINTS)
    im = capture()
    W, H = im.size
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
    out = im.resize((w, h), RESAMPLE).tobytes()
    if fmt == "gray8":
        out = gray_of(out)
    send_stream(s, "img.frame.ok", rid,
                {"width": W, "height": H, "frame_w": w, "frame_h": h,
                 "format": fmt}, out)


# ---------- worker ----------

def serve(s):
    """One connection's request loop (blocking)."""
    global _sock
    _sock = s
    digest = hashlib.sha256(device_token.encode("utf-8")).digest()
    s.sendall(AUTH_MAGIC + bytes((PROTO_VERSION,)) + digest)
    send_env(s, "img.hello", 0, {"caps": ["frame"]})
    obs.script_log(obs.LOG_INFO, "mycro: connected to %s:%d"
                   % (device_host, device_port))
    while not _stop.is_set():
        hdr = recv_exact(s, 4)
        (h,) = struct.unpack("<I", hdr)
        kind, length = h >> 31, h & 0x7FFFFFFF
        if length > 8192:
            raise ConnectionError("oversize frame (%d B)" % length)
        payload = recv_exact(s, length)
        if kind == 0:
            handle(s, json.loads(payload.decode("utf-8")))
        # kind 1 (binary) never flows device->provider; ignore


def worker():
    global _sock
    while not _stop.is_set():
        if not device_host:
            obs.script_log(obs.LOG_WARNING, "mycro: no device IP configured")
            _reconnect.wait(RECONNECT_S)
            _reconnect.clear()
            continue
        s = None
        try:
            s = socket.create_connection((device_host, device_port),
                                         timeout=5.0)
            s.settimeout(1.0)  # lets recv_exact notice _stop
            serve(s)
        except (OSError, ConnectionError) as e:
            obs.script_log(obs.LOG_WARNING, "mycro: link down: %s" % e)
        except Exception as e:
            obs.script_log(obs.LOG_WARNING, "mycro: worker error: %s" % e)
        finally:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
            _sock = None
        if not _stop.is_set():
            # backoff sleeps here; the Connect button / settings edits
            # wake it immediately
            obs.script_log(obs.LOG_INFO, "mycro: reconnecting in %ds (or on demand)"
                           % int(RECONNECT_S))
            _reconnect.wait(RECONNECT_S)
            _reconnect.clear()
