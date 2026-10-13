"""MYCRO OBS self-test suite.

Validates, inside the real OBS-embedded Python, every capability the
MYCRO capture plugin will depend on:

    T1  environment info (OBS version, Python, platform)
    T2  frontend API exposure (screenshot, events, timers, source enum)
    T3  stdlib availability (socket/json/struct/hashlib/zlib) + Pillow
    T4  screenshot chain: take_source_screenshot -> SCREENSHOT_TAKEN
        event -> last-screenshot file -> PNG header parse (dims/format)
    T5  device link: MYCRO auth prologue + device.info round trip
        (protocol v1, docs/protocol.md)
    T6  frame pipeline benchmark: Pillow decode cost, then all three
        consumption paths — lossless pixel probes, lossless region crops,
        and lossy grayscale downscale for on-device template matching —
        with raw byte counts vs the 8KB binary-chunk cap

Load: OBS -> Tools -> Scripts -> "+" -> select this file. Pick a video
source, fill in the device address, press "Run all tests". Results go
to the Script Log as [PASS]/[FAIL]/[SKIP]/[INFO] lines plus a final
[RESULT] summary. T5 blocks the UI for up to ~3s when the device is
unreachable; that is expected.
"""

import obspython as obs

import hashlib
import json
import os
import platform
import socket
import struct
import sys
import time

PROTO_VERSION = 1
AUTH_MAGIC = b"MYCR"
DEFAULT_PORT = 7373
SHOT_TIMEOUT_MS = 5000
BENCH_SIZES = ((160, 120), (320, 240))
BINARY_CHUNK_RAW = 8192 - 4  # protocol cap minus u16 stream_id + u16 flags

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
COLOR_TYPES = {0: "gray", 2: "rgb", 3: "palette", 4: "gray+alpha", 6: "rgba"}

REQUIRED_APIS = (
    "obs_frontend_take_source_screenshot",
    "obs_frontend_get_last_screenshot",
    "obs_frontend_add_event_callback",
    "obs_frontend_remove_event_callback",
    "timer_add",
    "timer_remove",
    "obs_enum_sources",
    "source_list_release",
    "obs_get_source_by_name",
    "obs_source_release",
    "obs_source_get_width",
    "obs_source_get_height",
    "obs_data_get_string",
    "obs_properties_add_list",
    "obs_property_list_add_string",
)

# --- settings (filled by script_update) ---
source_name = ""
device_host = ""
device_port = DEFAULT_PORT
device_token = ""
run_bench = True

# --- async screenshot state ---
_shot = None  # {"t0": float, "done": bool} while a screenshot is in flight
_shot_timer = False
_results = []


# ---------- result helpers ----------

def _log(level, msg):
    obs.script_log(level, msg)


def ok(tid, msg):
    _results.append(True)
    _log(obs.LOG_INFO, "[PASS] %s: %s" % (tid, msg))


def fail(tid, msg):
    _results.append(False)
    _log(obs.LOG_WARNING, "[FAIL] %s: %s" % (tid, msg))


def skip(tid, msg):
    _results.append(None)
    _log(obs.LOG_INFO, "[SKIP] %s: %s" % (tid, msg))


def info(msg):
    _log(obs.LOG_INFO, "[INFO] %s" % msg)


# ---------- OBS script boilerplate ----------

def script_description():
    return (
        "MYCRO self-test suite: validates the OBS-side capabilities the "
        "capture plugin needs (screenshot chain, device link, frame "
        "pipeline). Results land in the Script Log."
    )


def script_defaults(settings):
    obs.obs_data_set_default_int(settings, "port", DEFAULT_PORT)
    obs.obs_data_set_default_bool(settings, "bench", True)


def script_update(settings):
    global source_name, device_host, device_port, device_token, run_bench
    source_name = obs.obs_data_get_string(settings, "source")
    device_host = obs.obs_data_get_string(settings, "host")
    device_port = obs.obs_data_get_int(settings, "port")
    device_token = obs.obs_data_get_string(settings, "token")
    run_bench = obs.obs_data_get_bool(settings, "bench")


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
    obs.obs_properties_add_text(props, "host", "Device IP (empty = skip link test)",
                                obs.OBS_TEXT_DEFAULT)
    obs.obs_properties_add_int(props, "port", "Device port", 1, 65535, 1)
    obs.obs_properties_add_text(props, "token", "Device token", obs.OBS_TEXT_PASSWORD)
    obs.obs_properties_add_bool(props, "bench", "Benchmark frame pipeline (needs Pillow)")
    obs.obs_properties_add_button(props, "run", "Run all tests", on_run)
    return props


def script_load(settings):
    obs.obs_frontend_add_event_callback(on_frontend_event)


def script_unload():
    global _shot_timer
    obs.obs_frontend_remove_event_callback(on_frontend_event)
    if _shot_timer:
        obs.timer_remove(poll_shot)
        _shot_timer = False


# ---------- T1/T2/T3: synchronous environment checks ----------

def t1_env():
    try:
        ver = obs.obs_get_version_string()
    except AttributeError:
        ver = "unknown"
    info("T1 env: OBS %s | Python %s | %s/%s" % (
        ver, sys.version.split()[0], platform.system(), platform.machine()))
    _results.append(True)


def t2_apis():
    missing = [name for name in REQUIRED_APIS if not hasattr(obs, name)]
    if missing:
        fail("T2 api", "missing: %s" % ", ".join(missing))
    else:
        ok("T2 api", "%d frontend/libobs bindings exposed" % len(REQUIRED_APIS))


def t3_stdlib():
    missing = []
    for mod in ("socket", "json", "struct", "hashlib", "zlib", "os", "time"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        fail("T3 stdlib", "missing: %s" % ", ".join(missing))
    else:
        ok("T3 stdlib", "socket/json/struct/hashlib/zlib all importable")
    try:
        import PIL
        info("T3: Pillow %s installed (frame pipeline available)"
             % getattr(PIL, "__version__", "?"))
    except ImportError:
        info("T3: Pillow NOT installed — T6 will skip; install into the "
             "same Python OBS loads, e.g. python3.10 -m pip install Pillow")


# ---------- T5: device link (synchronous, ~3s worst case) ----------

def _recv_exact(s, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("device closed the connection (wrong token?)")
        buf += chunk
    return bytes(buf)


def t5_device():
    if not device_host:
        skip("T5 device", "no device IP configured")
        return
    t0 = time.monotonic()
    s = None
    try:
        s = socket.create_connection((device_host, device_port), timeout=3.0)
        digest = hashlib.sha256(device_token.encode("utf-8")).digest()
        s.sendall(AUTH_MAGIC + bytes((PROTO_VERSION,)) + digest)
        body = json.dumps({"v": PROTO_VERSION, "type": "device.info",
                           "id": 1, "ts": 0, "payload": {}}).encode("utf-8")
        s.sendall(struct.pack("<I", len(body)) + body)
        (hdr,) = struct.unpack("<I", _recv_exact(s, 4))
        payload = _recv_exact(s, hdr & 0x7FFFFFFF)
        rtt = (time.monotonic() - t0) * 1000.0
        if hdr >> 31:
            fail("T5 device", "unexpected binary frame (%d B)" % len(payload))
            return
        resp = json.loads(payload.decode("utf-8"))
        if resp.get("type") == "device.info.ok":
            ok("T5 device", "rtt %.0f ms, info: %s" % (
                rtt, json.dumps(resp.get("payload", {}), sort_keys=True)))
        else:
            fail("T5 device", "unexpected response: %s" % json.dumps(resp))
    except (socket.timeout, TimeoutError):
        fail("T5 device", "timeout — device unreachable or silent auth close")
    except (OSError, ConnectionError, ValueError) as e:
        fail("T5 device", "%s" % e)
    finally:
        if s is not None:
            s.close()


# ---------- T4: screenshot chain (async, event-driven) ----------

def on_frontend_event(event):
    if event == obs.OBS_FRONTEND_EVENT_SCREENSHOT_TAKEN and _shot is not None:
        _shot["done"] = True


def t4_begin():
    global _shot, _shot_timer
    src = obs.obs_get_source_by_name(source_name)
    if src is None:
        fail("T4 screenshot", "source not found: %r" % source_name)
        finish(None)
        return
    _shot = {"t0": time.monotonic(), "done": False}
    obs.obs_frontend_take_source_screenshot(src)
    obs.obs_source_release(src)
    obs.timer_add(poll_shot, 100)
    _shot_timer = True


def poll_shot():
    global _shot, _shot_timer
    if _shot is None:
        obs.timer_remove(poll_shot)
        _shot_timer = False
        return
    elapsed_ms = (time.monotonic() - _shot["t0"]) * 1000.0
    if _shot["done"]:
        t4_check_file(elapsed_ms)
    elif elapsed_ms > SHOT_TIMEOUT_MS:
        fail("T4 screenshot", "no SCREENSHOT_TAKEN event within %d ms "
             "(does the source have video output?)" % SHOT_TIMEOUT_MS)
        _shot = None
        obs.timer_remove(poll_shot)
        _shot_timer = False
        finish(None)


def t4_check_file(elapsed_ms):
    global _shot, _shot_timer
    _shot = None
    obs.timer_remove(poll_shot)
    _shot_timer = False
    path = obs.obs_frontend_get_last_screenshot()
    if not path or not os.path.exists(path):
        fail("T4 screenshot", "event fired but file missing: %r" % path)
        finish(None)
        return
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(26)
    if head[:8] == PNG_MAGIC and head[12:16] == b"IHDR":
        w, h = struct.unpack(">II", head[16:24])
        ok("T4 screenshot", "%dx%d %s/%d-bit PNG, %d B, %.0f ms -> %s" % (
            w, h, COLOR_TYPES.get(head[25], "?"), head[24], size, elapsed_ms, path))
    else:
        ok("T4 screenshot", "non-PNG output (%s), %d B, %.0f ms -> %s" % (
            head[:8].hex(), size, elapsed_ms, path))
    finish(path, elapsed_ms)


# ---------- T6: frame pipeline benchmark ----------

def _chunks(n):
    return (n + BINARY_CHUNK_RAW - 1) // BINARY_CHUNK_RAW


def t6_bench(path, shot_ms=None):
    if not run_bench:
        skip("T6 bench", "disabled in properties")
        return
    if not path:
        skip("T6 bench", "needs a screenshot from T4")
        return
    try:
        from PIL import Image
    except ImportError:
        skip("T6 bench", "Pillow not installed")
        return
    t0 = time.monotonic()
    with Image.open(path) as im:
        rgb = im.convert("RGB")
    decode_ms = (time.monotonic() - t0) * 1000.0
    w, h = rgb.size
    full = w * h * 3
    info("T6 bench: decode %dx%d RGB %.0f ms; full-frame lossless = %d B "
         "(%d chunks) — why probes/regions, never whole frames"
         % (w, h, decode_ms, full, _chunks(full)))
    if shot_ms is not None:
        info("T6 bench: stateless per-request estimate ~ %.0f ms "
             "(capture %.0f + decode %.0f + extraction)"
             % (shot_ms + decode_ms, shot_ms, decode_ms))

    # lossless path 1: pixel probes (device asks for exact points)
    px = rgb.load()
    pts = [(i * w // 16, i * h // 16) for i in range(16)]
    t0 = time.monotonic()
    colors = [list(px[x, y]) for x, y in pts]
    probe_ms = (time.monotonic() - t0) * 1000.0
    body = json.dumps({"colors": colors})
    info("T6 bench: probe 16 px = %d B JSON, %.1f ms extraction "
         "(unbuffered: every request pays capture+decode on top)"
         % (len(body), probe_ms))

    # lossless path 2: region crops (raw RGB via binary frames)
    for rw, rh in ((64, 64), (256, 256)):
        cw, ch = min(rw, w), min(rh, h)
        t0 = time.monotonic()
        raw = rgb.crop((0, 0, cw, ch)).tobytes()
        crop_ms = (time.monotonic() - t0) * 1000.0
        info("T6 bench: region %dx%d RGB = %d B (%d chunks), %.1f ms"
             % (cw, ch, len(raw), _chunks(len(raw)), crop_ms))

    # lossy path: grayscale downscale for on-device template matching
    for bw, bh in BENCH_SIZES:
        t0 = time.monotonic()
        raw = rgb.resize((bw, bh), Image.BILINEAR).convert("L").tobytes()
        ms = (time.monotonic() - t0) * 1000.0
        info("T6 bench: match frame %dx%d gray = %d B (%d chunks), %.0f ms"
             % (bw, bh, len(raw), _chunks(len(raw)), ms))
    ok("T6 bench", "pipeline timings above")


# ---------- run orchestration ----------

def finish(shot_path, shot_ms=None):
    t6_bench(shot_path, shot_ms)
    if shot_path:
        try:
            os.remove(shot_path)
            info("removed test screenshot %s" % shot_path)
        except OSError as e:
            info("could not remove %s: %s" % (shot_path, e))
    passed = sum(1 for r in _results if r is True)
    failed = sum(1 for r in _results if r is False)
    skipped = sum(1 for r in _results if r is None)
    _log(obs.LOG_WARNING if failed else obs.LOG_INFO,
         "[RESULT] %d passed, %d failed, %d skipped" % (passed, failed, skipped))


def on_run(props, prop):
    if _shot is not None:
        info("a test run is already in progress")
        return True
    del _results[:]
    info("--- MYCRO self-test run ---")
    t1_env()
    t2_apis()
    t3_stdlib()
    t5_device()
    if source_name:
        t4_begin()  # async: finish() runs when the screenshot resolves
    else:
        skip("T4 screenshot", "no capture source selected")
        finish(None)
    return True
