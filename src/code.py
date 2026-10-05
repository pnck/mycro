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
from lib.macro import Macro
from lib.runtime import Runtime

RAW_PORT = 7373       # raw TCP message channel (docs/protocol.md)
MAX_CLIENTS = 2       # authed raw clients; extras are rejected at auth time
AUTH_DEADLINE_S = 5   # a connection must complete the auth prologue within this
OUTBOX_CAP = 32       # per-client queued push frames; beyond this the peer is not reading

# WiFi credentials come from OUR OWN settings.toml keys, NOT the built-in
# CIRCUITPY_WIFI_* ones: the built-in ones make the supervisor connect before
# USB MSC is up, so a router outage would hang boot and take away even the
# CIRCUITPY drive. Connecting here at runtime keeps USB access no matter what.
WIFI_SSID = os.getenv("MYCRO_WIFI_SSID") or ""
WIFI_PASSWORD = os.getenv("MYCRO_WIFI_PASSWORD") or ""

# Bearer token from settings.toml; unset = auth disabled (dev mode, warns on serial)
TOKEN = os.getenv("MYCRO_TOKEN")

# Initialize HID + macro interpreter with a runtime registry
# (sys + net namespaces; img lands in Phase 3)
hid = AdafruitHIDProvider()
runtime = Runtime()
runtime.register_ns("sys", {
    "free": ([], lambda: gc.mem_free()),
})
# net: host event.push wakes \wait{net.msg} and publishes $net.* slots;
# \call{net.send}{tag} broadcasts a net.msg push, $ret = clients reached
runtime.register_signal("net.msg")
runtime.register_ns("net", {
    "send": (["str"], lambda tag: broadcast(proto.push("net.msg", {"tag": tag}))),
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

    def device_info(self):
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


def _recv(conn, nbytes):
    """Read up to nbytes from a non-blocking socket.

    CircuitPython's socketpool.Socket has recv_into, not recv; on-device
    probe: no data raises EAGAIN, a closed peer raises ENOTCONN. Returns
    bytes, b"" when the peer is gone, None when nothing is available."""
    buf = bytearray(nbytes)
    try:
        n = conn.recv_into(buf)
    except OSError as e:
        if e.args and e.args[0] == 11:
            return None  # EAGAIN: no data right now
        return b""  # ENOTCONN (128) et al: peer is gone
    if not n:
        return b""
    return bytes(buf[:n])


def _service(c, now):
    conn = c.conn
    if not c.authed:
        if now > c.deadline:
            c.alive = False
            return
        chunk = _recv(conn, codec.AUTH_LEN - len(c.inbuf))
        if chunk is None:
            return  # no data yet
        if chunk == b"":
            c.alive = False
            return
        c.inbuf += chunk
        if len(c.inbuf) >= codec.AUTH_LEN:
            authed = sum(1 for x in _raw_clients if x.authed)
            if codec.check_auth_header(bytes(c.inbuf), TOKEN) and authed < MAX_CLIENTS:
                c.authed = True
                print("raw client authed:", c.addr)
            else:
                print("raw auth rejected:", c.addr)
                c.alive = False
        return
    for _ in range(4):  # bounded reads per tick so one client can't starve others
        data = _recv(conn, 1024)
        if data is None:
            break  # no more data right now
        if data == b"":
            c.alive = False
            return
        try:
            frames = c.reasm.feed(data)
        except codec.CodecError as e:
            print("raw frame error:", c.addr, e)
            c.alive = False
            return
        print(f"raw rx {len(data)}B -> {len(frames)} frame(s)")
        for kind, payload in frames:
            if kind != codec.KIND_JSON:
                resp = proto.error_response(
                    None, "unsupported_kind", "no binary consumer registered")
            else:
                try:
                    env = codec.decode_envelope(payload)
                except codec.CodecError as e:
                    resp = proto.error_response(None, "bad_envelope", str(e))
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
        off += sent
        if off >= len(frame):
            c.outbox.pop(0)
        else:
            c.outbox[0] = (frame, off)


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
            while True:
                try:
                    conn, addr = s.accept()
                except OSError:
                    break  # no pending connection
                conn.settimeout(0)
                _raw_clients.append(_Client(conn, addr))
                print("raw conn from", addr)
            now = time.monotonic()
            for c in _raw_clients[:]:
                try:
                    _service(c, now)
                except Exception as e:
                    print("raw client error:", c.addr, e)
                    traceback.print_exception(e)  # full traceback to serial
                    c.alive = False
                if not c.alive:
                    _raw_clients.remove(c)
                    try:
                        c.conn.close()
                    except Exception:
                        pass
            await asyncio.sleep(0.01)
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
