"""MYCRO Vision and Image Capture Provider Service.

Handles exact pixel matching, telemetry streaming, reverse request dispatch
to external companion/OBS capture providers, and direct preview coordination.
"""

import os
import time
try:
    import asyncio
except ImportError:
    import _asyncio as asyncio

try:
    _TimeoutError = asyncio.TimeoutError
except AttributeError:
    _TimeoutError = TimeoutError

import codec
import proto
from runtime import Runtime, ASYNCIO_WAIT_CHUNK_MS
from bytecode import (
    ERR_OK,
    ERR_GENERIC,
    ERR_INVALID_ARG,
    ERR_NO_PROVIDER,
    ERR_OUT_OF_BOUNDS,
    ERR_HANDLE_INVALID,
    ERR_RESOURCE_EXHAUSTED,
    ERR_TIMEOUT,
    EXT_TIMEOUT_MS,
    RECT_MAX_EDGE,
)

SNAP_MAX_EDGE = RECT_MAX_EDGE  # 128 (128x128 RGB = 48KB)
IMG_STREAM_MAX = SNAP_MAX_EDGE * SNAP_MAX_EDGE * 3  # 49152
IMG_TIMEOUT_S = EXT_TIMEOUT_MS / 1000.0


class ImgError(Exception):
    """Internal image failure; mapped to numeric $err on return."""
    pass


def _img_failure(code_str):
    err = {
        "no_provider": ERR_NO_PROVIDER,
        "provider_lost": ERR_NO_PROVIDER,
        "timeout": ERR_TIMEOUT,
        "field": ERR_INVALID_ARG,
        "out_of_bounds": ERR_OUT_OF_BOUNDS,
        "local_resource_exhausted": ERR_RESOURCE_EXHAUSTED,
    }.get(code_str, ERR_GENERIC)
    return 0, err


# Module-level state
runtime = Runtime()
_img_client = None
_img_pending = None
_img_req_id = 0
_active_watches = {}
_lease_wake = asyncio.Event()
_stream_buf = None
_img_ui = {"pending": False, "data": None, "err": None}


def _sys_const(cid):
    """Materialize a compile-time const resource as a handle."""
    entry = runtime.const_entry(cid)
    if entry is None:
        print("sys.const: bad const id", cid)
        return 0, ERR_INVALID_ARG
    hid = runtime.new_handle(entry["kind"], entry["data"])
    if hid <= 0:
        return 0, ERR_RESOURCE_EXHAUSTED
    return hid


def _img_register(c):
    global _img_client
    if _img_client is not c:
        if _img_client is not None:
            _img_drop(_img_client)
        _img_client = c
        print("img provider connected:", c.addr)


def _img_drop(c):
    global _img_client, _img_pending
    if _img_client is c:
        _img_client = None
        print("img provider lost:", c.addr)
        for name in list(_active_watches):
            runtime.unregister_signal("img." + name)
        _active_watches.clear()
        _lease_wake.set()

    ent = _img_pending
    if ent is not None and ent["client"] is c and not ent["done"]:
        ent["err"] = ent["err"] or "provider_lost"
        ent["done"] = True
        ent["event"].set()


def _img_remove_watch(name, expected=None):
    """Remove only the current generation; retain slot values and latches."""
    current = _active_watches.get(name)
    if expected is not None and current is not expected:
        return False
    if current is None:
        if expected is None:
            runtime.unregister_signal("img." + name)
        return False
    _active_watches.pop(name, None)
    runtime.unregister_signal("img." + name)
    _lease_wake.set()
    return True


def _img_expire_watches(now=None):
    """Synchronously retire due generations without provider network I/O."""
    if now is None:
        now = time.monotonic()
    expired = False
    for name, entry in list(_active_watches.items()):
        if entry["deadline"] <= now and _active_watches.get(name) is entry:
            _active_watches.pop(name, None)
            runtime.unregister_signal("img." + name)
            expired = True
    if expired:
        _lease_wake.set()


async def _img_lease_loop():
    """Sleep until the nearest lease deadline or a configuration change."""
    while True:
        _lease_wake.clear()
        _img_expire_watches()
        nearest = None
        for entry in _active_watches.values():
            deadline = entry["deadline"]
            if nearest is None or deadline < nearest:
                nearest = deadline
        if nearest is None:
            await _lease_wake.wait()
            continue
        delay = nearest - time.monotonic()
        if delay <= 0:
            continue
        try:
            await asyncio.wait_for(
                _lease_wake.wait(),
                min(delay, ASYNCIO_WAIT_CHUNK_MS / 1000.0),
            )
        except _TimeoutError:
            pass


async def _img_request(type_, payload):
    global _img_pending, _img_req_id
    c = _img_client
    if c is None or not c.alive or not c.authed:
        raise ImgError("no_provider")
    if _img_pending is not None:
        raise ImgError("busy")

    _img_req_id = (_img_req_id + 1) & 0x7FFFFFFF
    ent = {
        "id": _img_req_id,
        "event": asyncio.Event(),
        "client": c,
        "type": type_,
        "resp": None,
        "err": None,
        "sid": -1,
        "stream": None,
        "have": -1,
        "chunks": 0,
        "seen": 0,
        "done": False,
    }
    _img_pending = ent
    c.img_stream_seen = True

    env = {
        "v": codec.PROTO_VERSION,
        "type": type_,
        "id": ent["id"],
        "ts": int(time.monotonic() * 1000),
        "payload": payload,
    }
    c.enqueue(codec.pack_frame(codec.KIND_JSON, codec.encode_envelope(env)))

    try:
        try:
            await asyncio.wait_for(ent["event"].wait(), IMG_TIMEOUT_S)
        except _TimeoutError:
            if not ent["done"]:
                ent["err"] = "timeout"
                ent["done"] = True
                if ent["sid"] >= 0:
                    c.alive = False
        if ent["err"]:
            raise ImgError(ent["err"])
        resp = ent["resp"]
        if resp.get("type") == "error":
            raise ImgError(resp.get("payload", {}).get("code", "error"))
        return resp.get("payload", {}), ent["stream"]
    finally:
        _img_pending = None


def _img_stream_error(c, ent, code, message):
    print("img protocol error:", message)
    ent["err"] = code
    ent["done"] = True
    ent["event"].set()
    c.alive = False


def _img_resolve(c, env):
    global _stream_buf
    ent = _img_pending
    if ent is None or c is not ent["client"] or env.get("id") != ent["id"]:
        return
    if ent["done"]:
        return

    invalid = proto.validate_envelope(env)
    if invalid:
        _img_stream_error(c, ent, invalid[0], invalid[1])
        return

    response_type = env.get("type")
    if response_type == "error":
        if ent["sid"] >= 0:
            _img_stream_error(c, ent, "stream_incomplete", "provider error during active stream")
            return
        ent["resp"] = env
        ent["done"] = True
        ent["event"].set()
        return

    if response_type != ent["type"] + ".ok":
        print("img: unexpected response type:", response_type)
        return

    p = env.get("payload", {})
    if not isinstance(p, dict):
        _img_stream_error(c, ent, "field", "response payload is not an object")
        return

    ent["resp"] = env
    sid = p.get("stream_id")
    chunks = p.get("chunks")
    if sid is None and chunks is None:
        ent["done"] = True
        ent["event"].set()
        return

    if ent["sid"] >= 0:
        _img_stream_error(c, ent, "stream_overrun", "duplicate stream begin")
        return

    total = p.get("bytes")
    valid = (
        type(sid) is int and 0 <= sid <= 65535
        and type(chunks) is int and chunks > 0
        and type(total) is int and 0 < total <= IMG_STREAM_MAX
    )
    if not valid:
        _img_stream_error(c, ent, "stream_overrun", f"invalid stream metadata: sid={sid} chunks={chunks} bytes={total}")
        return

    if _stream_buf is None:
        try:
            _stream_buf = bytearray(IMG_STREAM_MAX)
        except MemoryError:
            ent["err"] = "local_resource_exhausted"
            ent["done"] = True
            ent["event"].set()
            return

    ent["sid"] = sid
    ent["chunks"] = chunks
    ent["stream"] = memoryview(_stream_buf)[:total]
    ent["have"] = 0


def _img_chunk(c, payload):
    ent = _img_pending
    if ent is None or c is not ent["client"]:
        if getattr(c, "img_stream_seen", False):
            print("img protocol error: chunk without active stream")
            c.alive = False
            return True
        return False

    if ent["sid"] < 0:
        _img_stream_error(c, ent, "stream_id", "chunk before stream metadata")
        return True

    sid, flags, data = codec.split_chunk(payload)
    if sid != ent["sid"]:
        _img_stream_error(c, ent, "stream_id", f"unknown stream id {sid}")
        return True
    if ent["done"]:
        _img_stream_error(c, ent, "stream_overrun", "chunk after completion")
        return True
    if flags != 0:
        _img_stream_error(c, ent, "stream_flags", f"nonzero flags {flags}")
        return True

    ent["seen"] += 1
    end = ent["have"] + len(data)
    if ent["seen"] > ent["chunks"] or end > len(ent["stream"]):
        _img_stream_error(c, ent, "stream_overrun", f"chunk overrun: seen={ent['seen']}/{ent['chunks']}")
        return True

    final = ent["seen"] == ent["chunks"]
    if final and end != len(ent["stream"]):
        _img_stream_error(c, ent, "stream_incomplete", f"final chunk byte mismatch: {end}/{len(ent['stream'])}")
        return True

    ent["stream"][ent["have"]:end] = data
    ent["have"] = end
    if final:
        ent["done"] = True
        ent["event"].set()
    return True


def _img_snap(x, y, w, h):
    async def run():
        if not (1 <= w <= SNAP_MAX_EDGE and 1 <= h <= SNAP_MAX_EDGE):
            print("img.snap: edge capped at", SNAP_MAX_EDGE)
            return 0, ERR_INVALID_ARG
        if x < 0 or y < 0:
            print("img.snap: negative origin")
            return 0, ERR_INVALID_ARG
        try:
            payload, stream = await _img_request("img.frame", {"rect": {"x": x, "y": y, "w": w, "h": h}})
        except ImgError as e:
            print("img.snap failed:", e)
            return _img_failure(str(e))
        if stream is None:
            return 0, ERR_GENERIC

        source_w = payload.get("width")
        source_h = payload.get("height")
        if (
            type(source_w) is not int or type(source_h) is not int
            or source_w <= 0 or source_h <= 0
            or payload.get("format") != "rgb8"
            or x + w > source_w or y + h > source_h
        ):
            print("img.snap: invalid source metadata or format")
            return 0, ERR_GENERIC

        cw = payload.get("w")
        ch = payload.get("h")
        if type(cw) is not int or type(ch) is not int or cw != w or ch != h:
            print("img.snap: response dimensions differ from request")
            return 0, ERR_GENERIC

        if len(stream) != w * h * 3 or len(stream) > runtime.snap_pool.slot_bytes:
            print("img.snap: bad stream length")
            return 0, ERR_GENERIC

        slot = runtime.snap_pool.alloc()
        if slot is None:
            print("img.snap: snapshot pool exhausted")
            return 0, ERR_RESOURCE_EXHAUSTED
        try:
            view = runtime.snap_pool.view(slot, len(stream))
            view[:] = stream
            hid = runtime.new_handle("snap", {
                "w": w, "h": h, "rgb": view,
                "_arena": runtime.snap_pool,
                "_slot": slot,
            })
            if hid <= 0:
                runtime.snap_pool.free(slot)
                return 0, ERR_RESOURCE_EXHAUSTED
            return hid
        except Exception as e:
            print("img.snap slot init error:", e)
            runtime.snap_pool.free(slot)
            return 0, ERR_GENERIC

    return run()


def _img_at(handle, x, y):
    data = runtime.handle_data(handle, "snap")
    if data is None:
        print("img.at: invalid snap handle", handle)
        return 0, ERR_HANDLE_INVALID
    if not (0 <= x < data["w"] and 0 <= y < data["h"]):
        print("img.at: point outside snapshot")
        return 0, ERR_OUT_OF_BOUNDS
    i = (y * data["w"] + x) * 3
    rgb = data["rgb"]
    return (rgb[i] << 16) | (rgb[i + 1] << 8) | rgb[i + 2]


def _img_diff(a, b, tol):
    da = runtime.handle_data(a, "snap")
    db = runtime.handle_data(b, "snap")
    if da is None or db is None:
        print("img.diff: invalid snap handle")
        return 0, ERR_HANDLE_INVALID
    if da["w"] != db["w"] or da["h"] != db["h"]:
        print("img.diff: dimension mismatch")
        return 0, ERR_INVALID_ARG

    tol = max(0, min(255, tol))
    ra, rb = da["rgb"], db["rgb"]
    n = 0
    for i in range(0, len(ra), 3):
        d = ra[i] - rb[i]
        if d < 0:
            d = -d
        if d <= tol:
            d = ra[i + 1] - rb[i + 1]
            if d < 0:
                d = -d
            if d <= tol:
                d = ra[i + 2] - rb[i + 2]
                if d < 0:
                    d = -d
                if d <= tol:
                    continue
        n += 1
    return n


def _img_watch(name, x, y, interval_ms, duration_ms):
    async def run():
        if x < 0 or y < 0 or interval_ms <= 0 or duration_ms <= 0:
            return 0, ERR_INVALID_ARG
        c = _img_client
        if c is None or not c.alive or not c.authed:
            return 0, ERR_NO_PROVIDER
        sig_name = "img." + name
        runtime.register_signal(sig_name)
        entry = {
            "x": x, "y": y, "interval_ms": interval_ms,
            "deadline": time.monotonic() + duration_ms / 1000.0,
            "client": c,
            "pending": True,
        }
        # One record covers both pending and active states. Replacement is
        # fail-closed: an ambiguous failed refresh cannot resurrect the old lease.
        _active_watches[name] = entry
        _lease_wake.set()
        try:
            payload, _ = await _img_request("img.watch", {
                "name": name,
                "x": x,
                "y": y,
                "interval_ms": interval_ms,
                "duration_ms": duration_ms,
            })
        except ImgError as e:
            _img_remove_watch(name, entry)
            return _img_failure(str(e))

        if _img_client is not c or not c.alive or not c.authed:
            _img_remove_watch(name, entry)
            return 0, ERR_NO_PROVIDER
        if _active_watches.get(name) is not entry:
            return 0, ERR_TIMEOUT
        if time.monotonic() >= entry["deadline"]:
            _img_remove_watch(name, entry)
            return 0, ERR_TIMEOUT
        entry["pending"] = False
        return 0, ERR_OK

    return run()


def _img_unwatch(name, expected=None):
    async def run():
        entry = _active_watches.get(name)
        if expected is not None and entry is not expected:
            return 0, ERR_OK
        c = _img_client
        err = ERR_OK
        try:
            if c is not None and c.alive and c.authed:
                try:
                    await _img_request("img.unwatch", {"name": name})
                except Exception as e:
                    print("img.unwatch warning:", e)
                    _, err = _img_failure(str(e))
        finally:
            # Local cleanup is mandatory after ACK, timeout, disconnect, or
            # cancellation. A failed remote stop remains bounded by its lease.
            _img_remove_watch(name, entry)
        return 0, err

    return run()


async def _img_unwatch_all():
    watches = list(_active_watches.items())
    for index, (name, entry) in enumerate(watches):
        _, err = await _img_unwatch(name, entry)
        if err in (ERR_TIMEOUT, ERR_NO_PROVIDER):
            # The shared request channel cannot make progress. Do not multiply
            # one network outage into one full RPC timeout per remaining name.
            for stale_name, stale_entry in watches[index + 1:]:
                _img_remove_watch(stale_name, stale_entry)
            break


async def arm_direct_preview():
    c = _img_client
    if c is None or not c.alive or not c.authed:
        raise ImgError("no_provider")

    nonce = "".join(f"{b:02x}" for b in os.urandom(16))
    payload, _ = await _img_request("img.arm_preview", {"nonce": nonce})
    port = payload.get("port")
    if not port or not isinstance(port, int):
        raise ImgError("invalid_preview_port")

    obs_ip = c.addr[0] if c.addr else None
    if not obs_ip:
        raise ImgError("no_provider_ip")

    return {
        "url": f"http://{obs_ip}:{port}/capture",
        "nonce": nonce,
    }


class ImgProvider:
    """OOP Facade for the Image Capture Provider service."""

    def __init__(self, rt=None):
        global runtime
        if rt is not None:
            runtime = rt
        self.runtime = runtime
        self.raw_server = None
        self.ui_preview = _img_ui

    @property
    def capture_client(self):
        return _img_client

    @property
    def pending(self):
        return _img_pending

    @property
    def active_watches(self):
        return _active_watches

    def bind_raw_server(self, raw_server):
        self.raw_server = raw_server
        raw_server.on_chunk = _img_chunk
        raw_server.on_envelope = self.handle_envelope
        raw_server.on_disconnect = _img_drop

    def handle_envelope(self, client, env):
        env_type = env.get("type")
        if env_type == "img.hello":
            _img_register(client)
            return True
        ent = _img_pending
        if ent is not None and client is ent["client"] and env.get("id") == ent["id"]:
            _img_resolve(client, env)
            return True
        return False

    def register_namespaces(self):
        self.runtime.register_ns("sys", {
            "const": (["int"], "snap", lambda cid: _sys_const(cid)),
        })
        self.runtime.register_ns("img", {
            "snap": (["rect"], "snap", lambda x, y, w, h: _img_snap(x, y, w, h)),
            "at": (["snap", "point"], "color", lambda s, x, y: _img_at(s, x, y)),
            "diff": (["snap", "snap", "int"], "int", lambda a, b, tol: _img_diff(a, b, tol)),
            "watch": (["str", "point", "span"], "int",
                      lambda name, x, y, interval, duration: _img_watch(name, x, y, interval, duration)),
            "unwatch": (["str"], "int", lambda name: _img_unwatch(name)),
        })

    async def unwatch_all(self):
        await _img_unwatch_all()

    async def lease_loop(self):
        await _img_lease_loop()

    async def arm_direct_preview(self):
        return await arm_direct_preview()
