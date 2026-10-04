import asyncio
import gc
import os
import traceback

import socketpool
import wifi
from adafruit_httpserver import Request, Response, Server, FileResponse

from lib.hid_adafruit import AdafruitHIDProvider
from lib.macro import Macro
from lib.runtime import Runtime

RAW_PORT = 7373  # raw TCP listener stub; message protocol lands in Phase 2

# WiFi credentials come from OUR OWN settings.toml keys, NOT the built-in
# CIRCUITPY_WIFI_* ones: the built-in ones make the supervisor connect before
# USB MSC is up, so a router outage would hang boot and take away even the
# CIRCUITPY drive. Connecting here at runtime keeps USB access no matter what.
WIFI_SSID = os.getenv("MYCRO_WIFI_SSID") or ""
WIFI_PASSWORD = os.getenv("MYCRO_WIFI_PASSWORD") or ""

# Bearer token from settings.toml; unset = auth disabled (dev mode, warns on serial)
TOKEN = os.getenv("MYCRO_TOKEN")

# Initialize HID + macro interpreter with a runtime registry
# (sys namespace now; net/img land in Phase 2/3)
hid = AdafruitHIDProvider()
runtime = Runtime()
runtime.register_ns("sys", {
    "free": ([], lambda: gc.mem_free()),
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


async def _run(bytecode):
    """Macro execution wrapper: keeps `state` as the source of truth."""
    try:
        await macro.execute(bytecode, click_hold_ms=click_hold_ms)
        state["state"] = "done"
    except asyncio.CancelledError:
        state["state"] = "aborted"
        raise  # cancellation must propagate; keys are released by execute()'s finally
    except Exception as e:
        state["state"] = "error"
        state["detail"] = str(e)
        print("Macro runtime error:", e)


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


async def _http_loop():
    while True:
        try:
            server.poll()
        except Exception as e:
            # A handler bug must kill the response, never the device
            print("http handler error:", e)
            traceback.print_exception(e)
        await asyncio.sleep(0)


async def _raw_stub():
    """Raw TCP listener stub — Phase 2 fills in the message protocol here.

    CircuitPython/espressif asyncio + raw TCP has known stability risk
    (adafruit/circuitpython#10775): this task is fully exception-isolated so
    a failure here can never take down the HTTP/HID service.
    """
    try:
        s = pool.socket(pool.AF_INET, pool.SOCK_STREAM)
        s.settimeout(0)
        s.bind(("0.0.0.0", RAW_PORT))
        s.listen(2)
        print(f"Raw socket stub on :{RAW_PORT} (protocol TBD in Phase 2)")
        while True:
            try:
                conn, addr = s.accept()
                print("raw conn from", addr)
                try:
                    conn.send(b"MYCRO/0.1 stub\n")
                finally:
                    conn.close()
            except OSError:
                pass  # no pending connection
            await asyncio.sleep(0.05)
    except Exception as e:
        print("raw socket stub disabled:", e)


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
        await asyncio.gather(_http_loop(), _raw_stub())
    else:
        # Offline: keep the VM alive and idle. Edit settings.toml via the
        # CIRCUITPY drive and reset to retry.
        print("Edit settings.toml via CIRCUITPY drive, then reset to retry network services.")
        while True:
            await asyncio.sleep(3600)


asyncio.run(_main())
