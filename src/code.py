import asyncio
import gc
import os
import time
import traceback

import socketpool
import wifi
from adafruit_httpserver import Request, Response, Server, FileResponse

from lib import codec, proto
from lib.hid_adafruit import AdafruitHIDProvider
from lib.macro import Macro, RECT_MAX_EDGE
from lib.runtime import Runtime

RAW_PORT = 7373       # raw TCP message channel (docs/protocol.md)
MAX_CLIENTS = 2       # authed raw clients; extras are rejected at auth time
AUTH_DEADLINE_S = 5   # a connection must complete the auth prologue within this
OUTBOX_CAP = 32       # per-client queued push frames; beyond this the peer is not reading

# img namespace (exact pixel matching; spec/vm.md + docs/protocol.md)
IMG_TIMEOUT_S = 20    # reverse-request ceiling
IMG_STREAM_MAX = 4 * 1024 * 1024  # armed-stream total payload cap
# Static stream assembly arena (spec/vm.md Part II §6): reverse requests
# are single-in-flight, so one buffer serves every stream — zero heap
# churn no matter how much pixel traffic flows.
_stream_arena = bytearray(IMG_STREAM_MAX)
SNAP_MAX_EDGE = RECT_MAX_EDGE  # snapshot edge cap (128x128 RGB = 48KB)

try:
    _TimeoutError = asyncio.TimeoutError
except AttributeError:
    _TimeoutError = TimeoutError

# WiFi credentials come from OUR OWN settings.toml keys, NOT the built-in
# CIRCUITPY_WIFI_* ones: the built-in ones make the supervisor connect before
# USB MSC is up, so a router outage would hang boot and take away even the
# CIRCUITPY drive. Connecting here at runtime keeps USB access no matter what.
WIFI_SSID = os.getenv("MYCRO_WIFI_SSID") or ""
WIFI_PASSWORD = os.getenv("MYCRO_WIFI_PASSWORD") or ""

# Bearer token from settings.toml; unset = auth disabled (dev mode, warns on serial)
TOKEN = os.getenv("MYCRO_TOKEN")

# Initialize HID + macro interpreter with a runtime registry
# (sys + net + img namespaces)
hid = AdafruitHIDProvider()
runtime = Runtime()
runtime.register_ns("sys", {
    "free": ([], "int", lambda: gc.mem_free()),
    # \rect literal data handoff: the compiler preprocesses pixels into the
    # runtime const store; the emitted sys.const call materializes a handle
    "const": (["int"], "snap", lambda cid: _sys_const(cid)),
})
# net: host event.push wakes \wait{net.msg} and publishes $net.* slots;
# \call{net.send}{tag} broadcasts a net.msg push, $ret = clients reached
runtime.register_signal("net.msg")
runtime.register_ns("net", {
    "send": (["str"], "int",
             lambda tag: broadcast(proto.push("net.msg", {"tag": tag}))),
})
# img (exact pixel matching): the snapshot is the only entity — a named
# capture of a native-resolution, lossless RGB rect of the current frame.
# \call{img.snap}{x,y,w,h} -> $ret = snap handle; \call{img.at}{$s}{x,y}
# -> $ret = packed RGB color (snapshot-local point); \call{img.diff}{$a}
# {$b}{tol} -> $ret = number of pixels whose channels differ by > tol.
# Handles obey the ownership rules of spec/vm.md Part II section 4:
# overwrite releases, \set moves, macro exit frees everything.
runtime.register_ns("img", {
    "snap": (["rect"], "snap", lambda x, y, w, h: _img_snap(x, y, w, h)),
    "at": (["snap", "point"], "color", lambda s, x, y: _img_at(s, x, y)),
    "diff": (["snap", "snap", "int"], "int",
             lambda a, b, tol: _img_diff(a, b, tol)),
})
macro = Macro(hid, runtime)

# Minimum hold time for click (ms)
click_hold_ms = 10

# Macro execution state (the dict is the source of truth for /macro/status)
macro_task = None
state = {"state": "idle", "detail": "", "bytes": 0}

# HTTP Server
pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=False)


def urldecode(s):
    """Decode URL-encoded string.

    NOTE: adafruit_httpserver's form_data does NOT URL-decode values (verified
    against the pinned library version), so we decode here. Re-verify if the
    library is ever upgraded.
    """
    result = []
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                result.append(chr(int(s[i + 1 : i + 3], 16)))
                i += 3
                continue
            except Exception:
                pass
        elif s[i] == "+":
            result.append(" ")
            i += 1
            continue
        result.append(s[i])
        i += 1
    return "".join(result)


def _authorized(request):
    if not TOKEN:
        return True
    return request.headers.get("Authorization") == "Bearer " + TOKEN


def _unauth(request):
    # status takes a Status or (code, text) tuple -- a bare int crashes
    # Response.__init__ (Status(*status) unpacking) on adafruit_httpserver 4.x
    return Response(request, "Unauthorized", status=(401, "Unauthorized"),
                    content_type="text/plain")


@server.route("/")
def index(request: Request):
    try:
        return FileResponse(request, "/index.html", "/", content_type="text/html; charset=utf-8")
    except Exception:
        return Response(request, "<h1>index.html not found</h1>", content_type="text/html; charset=utf-8")


def _macro_form(request):
    """Extract and decode macro form fields. Returns (code, hold_ms_or_none)."""
    code = request.form_data.get("code", "")
    hold = request.form_data.get("hold_ms", "")
    if not code:
        return None, None
    hold_ms = None
    if hold:
        try:
            hold_ms = max(0, min(1000, int(hold)))
        except Exception:
            pass
    return urldecode(code), hold_ms


def _broadcast_result(t0):
    body = {"state": state["state"], "ms": _now_ms() - t0}
    if state["detail"]:
        body["detail"] = state["detail"]
    broadcast(proto.push("macro.result", body))


async def _run(bytecode):
    """Macro execution wrapper: keeps `state` as the source of truth."""
    t0 = _now_ms()
    try:
        await macro.execute(bytecode, click_hold_ms=click_hold_ms)
        state["state"] = "done"
    except asyncio.CancelledError:
        state["state"] = "aborted"
        _broadcast_result(t0)
        raise  # cancellation must propagate; keys are released by execute()'s finally
    except Exception as e:
        state["state"] = "error"
        state["detail"] = str(e)
        print("Macro runtime error:", e)
    _broadcast_result(t0)


@server.route("/macro", methods=["POST"])
def run_macro(request: Request):
    global macro_task, click_hold_ms
    if not _authorized(request):
        return _unauth(request)
    if state["state"] == "running":
        return Response(request, "Busy: Macro is running", content_type="text/plain")

    code, hold_ms = _macro_form(request)
    if code is None:
        return Response(request, "Error: No macro code", content_type="text/plain")
    if hold_ms is not None:
        click_hold_ms = hold_ms

    # Compile (syntax check) before touching the executor
    try:
        bytecode, err = macro.compile(code)
    except Exception as e:
        # Never let device resource errors (e.g. pystack exhausted) kill the
        # whole asyncio program from inside a request handler
        return Response(
            request,
            f"Compile failed: {type(e).__name__}: {e}",
            content_type="text/plain; charset=utf-8",
        )
    if err:
        return Response(request, f"Compile Error: {err}", content_type="text/plain; charset=utf-8")

    # Print bytecode to serial console
    print(f"\n=== Compiled Bytecode ({len(bytecode)} bytes) ===")
    print(macro.disassemble(bytecode))
    print("=" * 40)

    # Start execution as a background task and return immediately;
    # the HTTP server stays responsive while the macro runs.
    state.update(state="running", detail="", bytes=len(bytecode))
    macro_task = asyncio.create_task(_run(bytecode))
    return Response(request, f"Started: {len(bytecode)}B", content_type="text/plain; charset=utf-8")


@server.route("/macro/status")
def macro_status(request: Request):
    if not _authorized(request):
        return _unauth(request)
    body = state["state"]
    if state["detail"]:
        body += " " + state["detail"]
    return Response(request, body, content_type="text/plain; charset=utf-8")


@server.route("/macro/abort", methods=["POST"])
def macro_abort(request: Request):
    if not _authorized(request):
        return _unauth(request)
    if macro_task and state["state"] == "running":
        macro_task.cancel()
        return Response(request, "Abort requested", content_type="text/plain")
    return Response(request, "No macro running", content_type="text/plain")


# --- Pixel Assist (web UI): single-lossless-capture passthrough ---
# HTTP handlers are synchronous, so the capture runs as a task and the
# browser polls. The device never decodes the PNG — pure passthrough.

_img_ui = {"pending": False, "data": None, "err": None}


@server.route("/img/capture", methods=["POST"])
def img_capture_start(request: Request):
    if not _authorized(request):
        return _unauth(request)
    if _img_ui["pending"]:
        return Response(request, "busy", status=(409, "Conflict"),
                        content_type="text/plain")
    _img_ui.update(pending=True, data=None, err=None)
    asyncio.create_task(_img_capture_task())
    return Response(request, "started", content_type="text/plain")


async def _img_capture_task():
    try:
        _payload, stream = await _img_request("img.frame", {"format": "png"})
        if stream:
            # isolate from the shared stream arena: the UI holds this
            # across later img traffic until the browser fetches it.
            # Human-click frequency — one copy is fine here.
            _img_ui["data"] = bytes(stream)
        else:
            _img_ui["err"] = "empty response"
    except ImgError as e:
        _img_ui["err"] = str(e)
    except Exception as e:
        _img_ui["err"] = f"{type(e).__name__}: {e}"
    finally:
        _img_ui["pending"] = False


@server.route("/img/capture", methods=["GET"])
def img_capture_poll(request: Request):
    if not _authorized(request):
        return _unauth(request)
    if _img_ui["pending"]:
        return Response(request, "", status=(204, "No Content"),
                        content_type="text/plain")
    if _img_ui["err"]:
        return Response(request, _img_ui["err"], status=(502, "Bad Gateway"),
                        content_type="text/plain")
    if _img_ui["data"] is None:
        return Response(request, "none", status=(404, "Not Found"),
                        content_type="text/plain")
    # read-once: hand the bytes to the response and release the slot
    # immediately — a full-screen PNG is >1MB of heap we must not retain
    data = _img_ui["data"]
    _img_ui["data"] = None
    return Response(request, body=data, content_type="image/png")


@server.route("/compile", methods=["POST"])
def compile_macro(request: Request):
    global click_hold_ms
    if not _authorized(request):
        return _unauth(request)

    code, hold_ms = _macro_form(request)
    if code is None:
        return Response(request, "Error: No macro code", content_type="text/plain")
    if hold_ms is not None:
        click_hold_ms = hold_ms

    try:
        bytecode, err = macro.compile(code)
    except Exception as e:
        return Response(
            request,
            f"Compile failed: {type(e).__name__}: {e}",
            content_type="text/plain; charset=utf-8",
        )
    if err:
        return Response(request, f"Compile Error: {err}", content_type="text/plain; charset=utf-8")

    # Return disassembly without executing
    return Response(request, macro.disassemble(bytecode), content_type="text/plain; charset=utf-8")


def _now_ms():
    return int(time.monotonic() * 1000)


# --- Raw protocol plumbing (proto ctx, clients, broadcast) ---


class _ProtoCtx:
    """Platform wiring for proto.Dispatcher handlers."""

    runtime = runtime

    def submit(self, code):
        global macro_task
        try:
            bytecode, err = macro.compile(code)
        except Exception as e:
            # Device resource errors (e.g. pystack) must never escape a handler
            return None, f"{type(e).__name__}: {e}"
        if err:
            return None, err
        print(f"\n=== Compiled Bytecode ({len(bytecode)} bytes, via raw) ===")
        print(macro.disassemble(bytecode))
        print("=" * 40)
        state.update(state="running", detail="", bytes=len(bytecode))
        macro_task = asyncio.create_task(_run(bytecode))
        return len(bytecode), None

    def status(self):
        return state["state"], state["detail"] or None

    def abort(self):
        if macro_task and state["state"] == "running":
            macro_task.cancel()
            return True
        return False

    def device_info(self, gc_first=False):
        if gc_first:
            gc.collect()  # post-GC truth for memory forensics (devicetest F4)
        u = os.uname()
        return {"board": u.machine, "fw": u.release, "free_mem": gc.mem_free()}


dispatcher = proto.Dispatcher()
proto_ctx = _ProtoCtx()
_raw_clients = []


class _Client:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.inbuf = bytearray()       # auth prologue accumulator
        self.reasm = codec.Reassembler()
        self.outbox = []               # (frame bytes, send offset)
        self.authed = False
        self.alive = True
        self.deadline = time.monotonic() + AUTH_DEADLINE_S

    def enqueue(self, frame):
        if self.alive and len(self.outbox) < OUTBOX_CAP:
            self.outbox.append((frame, 0))


def broadcast(env):
    """Stamp and enqueue a server-initiated push to every authed client.
    Returns the number of clients the frame was queued for."""
    env["ts"] = _now_ms()
    frame = codec.pack_frame(codec.KIND_JSON, codec.encode_envelope(env))
    n = 0
    for c in _raw_clients:
        if c.authed:
            c.enqueue(frame)
            n += 1
    return n


# --- img capture provider (reverse requests over its connection) ---
# The capture provider (OBS plugin) is a persistent CLIENT that announces
# itself with an img.hello push; img.* DSL calls send request envelopes
# back over that connection and await the response. At most one request
# is in flight: the macro task is serialized by the busy guard.

_img_client = None   # the _Client that announced img.hello
_img_pending = None  # the single in-flight reverse request
_img_req_id = 0


class ImgError(Exception):
    """img.* call failures; the DSL surface sees $ret < 0 + a serial log."""


def _sys_const(cid):
    """Materialize a compile-time const resource as a handle (the runtime
    half of the \rect literal — spec/dsl.md §3)."""
    entry = runtime.const_entry(cid)
    if entry is None:
        print("sys.const: bad const id", cid)
        return -1
    return runtime.new_handle(entry["kind"], entry["data"])


def _img_register(c):
    global _img_client
    if _img_client is not c:
        _img_client = c
        print("img provider:", c.addr)


def _img_drop(c):
    global _img_client, _img_pending
    if _img_client is c:
        _img_client = None
        print("img provider lost:", c.addr)
    ent = _img_pending
    if ent is not None and ent["client"] is c:
        ent["err"] = "provider_lost"
        ent["event"].set()


async def _img_request(type_, payload):
    """Reverse request to the capture provider. Returns
    (response_payload, stream_bytes | None); raises ImgError."""
    global _img_pending, _img_req_id
    c = _img_client
    if c is None or not c.alive or not c.authed:
        raise ImgError("no_provider")
    _img_req_id = (_img_req_id + 1) & 0x7FFFFFFF
    ent = {"id": _img_req_id, "event": asyncio.Event(), "client": c,
           "resp": None, "err": None, "sid": -1, "stream": None,
           "have": -1}
    _img_pending = ent
    env = {"v": codec.PROTO_VERSION, "type": type_, "id": ent["id"],
           "ts": _now_ms(), "payload": payload}
    c.enqueue(codec.pack_frame(codec.KIND_JSON, codec.encode_envelope(env)))
    try:
        await asyncio.wait_for(ent["event"].wait(), IMG_TIMEOUT_S)
    except _TimeoutError:
        ent["err"] = "timeout"
    _img_pending = None
    if ent["err"]:
        raise ImgError(ent["err"])
    resp = ent["resp"]
    if resp.get("type") == "error":
        raise ImgError(resp.get("payload", {}).get("code", "error"))
    return resp.get("payload", {}), ent["stream"]


def _img_resolve(c, env):
    """Complete (or arm) the pending reverse request from a response
    envelope. A payload with stream_id arms the binary collector;
    completion then waits for the FLAG_STREAM_END chunk."""
    ent = _img_pending
    if ent is None or c is not ent["client"] or env.get("id") != ent["id"]:
        print("img: stray response dropped:", env.get("type"))
        return
    ent["resp"] = env
    p = env.get("payload", {})
    sid = p.get("stream_id")
    if env.get("type") != "error" and isinstance(sid, int) \
            and p.get("chunks", 0) > 0:
        ent["sid"] = sid
        total = p.get("bytes")
        if isinstance(total, int) and 0 < total <= IMG_STREAM_MAX:
            # static arena: each chunk lands by offset, no allocation,
            # no O(n^2) growth copies
            ent["stream"] = memoryview(_stream_arena)[:total]
            ent["have"] = 0
        else:
            ent["stream"] = bytearray()
            ent["have"] = -1  # unknown length: append path (heap fallback)
    else:
        ent["event"].set()


def _img_chunk(c, payload):
    """Route a kind-1 frame to the armed stream collector.
    Returns False for unexpected binary traffic (-> unsupported_kind)."""
    ent = _img_pending
    if ent is None or c is not ent["client"] or ent["sid"] < 0:
        return False
    sid, flags, data = codec.split_chunk(payload)
    if sid != ent["sid"]:
        return False
    if ent["have"] >= 0:
        end = ent["have"] + len(data)
        if end > len(ent["stream"]):
            ent["err"] = "stream_overrun"  # provider lied about its length
            ent["event"].set()
            return True
        ent["stream"][ent["have"]:end] = data
        ent["have"] = end
    else:
        if len(ent["stream"]) + len(data) > IMG_STREAM_MAX:
            ent["err"] = "stream_overrun"  # unannounced length over the cap
            ent["event"].set()
            return True
        ent["stream"] += data
    if flags & codec.FLAG_STREAM_END:
        if 0 <= ent["have"] < len(ent["stream"]):
            # provider ended short of its announced length: trim so the
            # consumer's length check rejects it instead of trusting zeros
            ent["stream"] = ent["stream"][:ent["have"]]
        ent["event"].set()
    return True


def _img_snap(x, y, w, h):
    async def run():
        if not (1 <= w <= SNAP_MAX_EDGE and 1 <= h <= SNAP_MAX_EDGE):
            print("img.snap: edge capped at", SNAP_MAX_EDGE)
            return -1
        if x < 0 or y < 0:
            print("img.snap: negative origin")
            return -1
        try:
            payload, stream = await _img_request(
                "img.frame", {"rect": {"x": x, "y": y, "w": w, "h": h}})
        except ImgError as e:
            print("img.snap failed:", e)
            return -1
        if stream is None:
            return -1
        cw = payload.get("crop_w", w)  # provider clamps at source borders
        ch = payload.get("crop_h", h)
        if len(stream) != cw * ch * 3:
            print("img.snap: bad stream")
            return -1
        slot = runtime.snap_pool.alloc()
        if slot is None:
            print("img.snap: snapshot pool exhausted (release a snapshot)")
            return -1
        view = runtime.snap_pool.view(slot, len(stream))
        view[:] = stream  # arena -> slot memcpy, no heap involved
        return runtime.new_handle("snap", {"w": cw, "h": ch, "rgb": view,
                                           "_arena": runtime.snap_pool,
                                           "_slot": slot})
    return run()


def _img_at(handle, x, y):
    data = runtime.handle_data(handle, "snap")
    if data is None:
        print("img.at: invalid snap handle", handle)
        return -1
    if not (0 <= x < data["w"] and 0 <= y < data["h"]):
        print("img.at: point outside snapshot")
        return -1
    i = (y * data["w"] + x) * 3
    rgb = data["rgb"]
    return (rgb[i] << 16) | (rgb[i + 1] << 8) | rgb[i + 2]


def _img_diff(a, b, tol):
    da = runtime.handle_data(a, "snap")
    db = runtime.handle_data(b, "snap")
    if da is None or db is None:
        print("img.diff: invalid snap handle")
        return -1
    if da["w"] != db["w"] or da["h"] != db["h"]:
        print("img.diff: dimension mismatch")
        return -1
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


def _recv(conn, nbytes):
    """Read up to nbytes from a non-blocking socket.

    CircuitPython's socketpool.Socket has recv_into, not recv; on-device
    probe: no data raises EAGAIN, a closed peer raises ENOTCONN. Returns
    bytes, b"" when the peer is gone, None when nothing is available.
    Cold paths only (auth prologue) — the hot loop uses _recv_into."""
    buf = bytearray(nbytes)
    r = _recv_into(conn, buf)
    if r is None:
        return None
    if r == 0:
        return b""
    return bytes(buf[:r])


def _recv_into(conn, view):
    """recv_into a caller-owned (static) buffer: zero allocation.

    Returns the byte count, 0 when the peer is gone, None on EAGAIN."""
    try:
        n = conn.recv_into(view)
    except OSError as e:
        if e.args and e.args[0] == 11:
            return None  # EAGAIN: no data right now
        return 0  # ENOTCONN (128) et al: peer is gone
    return n


def _service(c, now):
    """Service one client for this tick. Returns True when any bytes moved
    (the server loop yields instead of sleeping while traffic flows)."""
    active = False
    conn = c.conn
    if not c.authed:
        if now > c.deadline:
            c.alive = False
            return True
        chunk = _recv(conn, codec.AUTH_LEN - len(c.inbuf))
        if chunk is None:
            return False  # no data yet
        if chunk == b"":
            c.alive = False
            return True
        c.inbuf += chunk
        if len(c.inbuf) >= codec.AUTH_LEN:
            authed = sum(1 for x in _raw_clients if x.authed)
            if codec.check_auth_header(bytes(c.inbuf), TOKEN) and authed < MAX_CLIENTS:
                c.authed = True
                print("raw client authed:", c.addr)
            else:
                print("raw auth rejected:", c.addr)
                c.alive = False
        return True
    for _ in range(8):  # bounded reads per tick so one client can't starve others
        # recv straight into the reassembler's static buffer: the hot
        # path allocates nothing, each byte is copied exactly twice
        r = _recv_into(conn, c.reasm.write_view())
        if r is None:
            break  # no more data right now
        active = True
        if r == 0:
            c.alive = False
            return True
        try:
            frames = c.reasm.feed(r)
        except codec.CodecError as e:
            print("raw frame error:", c.addr, e)
            c.alive = False
            return
        print(f"raw rx {r}B -> {len(frames)} frame(s)")
        for kind, payload in frames:
            if kind != codec.KIND_JSON:
                resp = None if _img_chunk(c, payload) else proto.error_response(
                    None, "unsupported_kind", "no binary consumer registered")
            else:
                try:
                    env = codec.decode_envelope(payload)
                except codec.CodecError as e:
                    resp = proto.error_response(None, "bad_envelope", str(e))
                else:
                    if env.get("type") == "img.hello":
                        _img_register(c)
                        resp = None
                    elif _img_pending is not None \
                            and env.get("id") == _img_pending["id"]:
                        _img_resolve(c, env)
                        resp = None
                    else:
                        resp = dispatcher.dispatch(env, proto_ctx)
            if resp is not None:
                resp["ts"] = _now_ms()
                print("raw tx", resp.get("type"), "to", c.addr)
                c.enqueue(codec.pack_frame(codec.KIND_JSON, codec.encode_envelope(resp)))
    while c.outbox:
        frame, off = c.outbox[0]
        try:
            sent = conn.send(frame[off:])
        except OSError:
            break  # EAGAIN: retry next tick
        active = True
        off += sent
        if off >= len(frame):
            c.outbox.pop(0)
        else:
            c.outbox[0] = (frame, off)
    return active


async def _http_loop():
    while True:
        try:
            server.poll()
        except Exception as e:
            # A handler bug must kill the response, never the device
            print("http handler error:", e)
            traceback.print_exception(e)
        await asyncio.sleep(0)


async def _raw_server():
    """Raw TCP message channel (protocol v1, docs/protocol.md).

    Single round-robin task owns every connection: no per-connection tasks,
    no cross-task socket access. CircuitPython/espressif asyncio + raw TCP
    has known stability risk (adafruit/circuitpython#10775): this task is
    fully exception-isolated so a failure here can never take down the
    HTTP/HID service.
    """
    try:
        s = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
        # survive soft reboots: the previous run's listener lingers briefly
        s.setsockopt(pool.SOL_SOCKET, pool.SO_REUSEADDR, 1)
        s.settimeout(0)
        s.bind(("0.0.0.0", RAW_PORT))
        s.listen(MAX_CLIENTS)
        print(f"Raw protocol on :{RAW_PORT} (v{codec.PROTO_VERSION})")
        while True:
            busy = False
            while True:
                try:
                    conn, addr = s.accept()
                except OSError:
                    break  # no pending connection
                conn.settimeout(0)
                _raw_clients.append(_Client(conn, addr))
                print("raw conn from", addr)
                busy = True
            now = time.monotonic()
            for c in _raw_clients[:]:
                try:
                    busy = _service(c, now) or busy
                except Exception as e:
                    print("raw client error:", c.addr, e)
                    traceback.print_exception(e)  # full traceback to serial
                    c.alive = False
                if not c.alive:
                    _img_drop(c)
                    _raw_clients.remove(c)
                    try:
                        c.conn.close()
                    except Exception:
                        pass
                    busy = True
            # traffic flows -> just yield; idle -> sleep a tick
            await asyncio.sleep(0 if busy else 0.01)
    except Exception as e:
        print("raw server disabled:", e)
        traceback.print_exception(e)


async def _wifi_connect(attempts=3):
    """Connect WiFi at runtime. Returns True on success.

    Any failure leaves the device fully usable over USB (MSC / serial / HID) —
    just without the HTTP/raw services.
    """
    if not WIFI_SSID:
        print("MYCRO_WIFI_SSID not set — offline mode (USB/serial/HID only)")
        return False
    for attempt in range(1, attempts + 1):
        try:
            print(f"Connecting WiFi '{WIFI_SSID}' (attempt {attempt}/{attempts})...")
            try:
                wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD, timeout=15)
            except TypeError:
                wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)  # older firmware: no timeout kwarg
            try:
                # Modem sleep (default MIN) makes the AP buffer inbound
                # packets until the next DTIM wake: ~350ms connection setup
                # and sawtooth ping RTTs. This device is USB-powered; take
                # the responsiveness instead of the milliamps.
                wifi.radio.power_management = wifi.PowerManagement.NONE
            except Exception:
                pass  # older firmware without the enum
            print("WiFi connected:", wifi.radio.ipv4_address)
            return True
        except Exception as e:
            print("WiFi connect failed:", e)
            await asyncio.sleep(3)
    print("WiFi unavailable — offline mode (USB/serial/HID only)")
    return False


async def _main():
    if await _wifi_connect():
        ip = str(wifi.radio.ipv4_address)
        server.start(ip, 80)
        print(f"HTTP server at http://{ip}")
        if not TOKEN:
            print("WARNING: MYCRO_TOKEN not set in settings.toml — auth disabled (dev mode)")
        await asyncio.gather(_http_loop(), _raw_server())
    else:
        # Offline: keep the VM alive and idle. Edit settings.toml via the
        # CIRCUITPY drive and reset to retry.
        print("Edit settings.toml via CIRCUITPY drive, then reset to retry network services.")
        while True:
            await asyncio.sleep(3600)


asyncio.run(_main())
