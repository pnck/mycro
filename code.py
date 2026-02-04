import usb_hid
import socketpool
import wifi
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.mouse import Mouse
from adafruit_httpserver import Request, Response, Server, FileResponse

from lib.macro import Macro


def urldecode(s):
    """Decode URL-encoded string."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == "%" and i + 2 < len(s):
            try:
                result.append(chr(int(s[i+1:i+3], 16)))
                i += 3
                continue
            except:
                pass
        elif s[i] == "+":
            result.append(" ")
            i += 1
            continue
        result.append(s[i])
        i += 1
    return "".join(result)


def htmldecode(s):
    """Decode common HTML entities."""
    if not s:
        return s
    # Important: decode &amp; last
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&quot;", "\"")
    s = s.replace("&#39;", "'").replace("&apos;", "'")
    s = s.replace("&amp;", "&")
    return s


# Initialize HID devices
keyboard = Keyboard(usb_hid.devices)
layout = KeyboardLayoutUS(keyboard)
mouse = Mouse(usb_hid.devices)

# Initialize macro interpreter
macro = Macro(keyboard, layout, mouse)

# Global execution lock
macro_busy = False

# Minimum hold time for click (ms)
click_hold_ms = 10

# HTTP Server
pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=True)


@server.route("/")
def index(request: Request):
    try:
        return FileResponse(request, "/index.html", "/", content_type="text/html; charset=utf-8")
    except:
        return Response(request, "<h1>index.html not found</h1>", content_type="text/html; charset=utf-8")


@server.route("/macro", methods=["POST"])
def run_macro(request: Request):
    global macro_busy, click_hold_ms
    if macro_busy:
        return Response(request, "Busy: Macro is running", content_type="text/plain")
    macro_busy = True
    try:
        code = request.form_data.get("code", "")
        hold = request.form_data.get("hold_ms", "")
        if not code:
            return Response(request, "Error: No macro code", content_type="text/plain")
        if hold:
            try:
                click_hold_ms = max(0, min(1000, int(hold)))
            except:
                pass
        
        # URL + HTML decode the input (defensive against entity encoding)
        code = htmldecode(urldecode(code))
        
        # Compile (syntax check)
        bytecode, err = macro.compile(code)
        if err:
            return Response(request, f"Compile Error: {err}", content_type="text/plain; charset=utf-8")
        
        # Print bytecode to serial console
        print(f"\n=== Compiled Bytecode ({len(bytecode)} bytes) ===")
        print(macro.disassemble(bytecode))
        print("=" * 40)
        
        # Execute macro, then return result
        try:
            macro.execute(bytecode, click_hold_ms=click_hold_ms)
            return Response(request, f"OK: {len(bytecode)}B executed", content_type="text/plain; charset=utf-8")
        except Exception as e:
            return Response(request, f"Runtime Error: {e}", content_type="text/plain; charset=utf-8")
    finally:
        macro_busy = False


@server.route("/compile", methods=["POST"])
def compile_macro(request: Request):
    global macro_busy, click_hold_ms
    if macro_busy:
        return Response(request, "Busy: Macro is running", content_type="text/plain")
    macro_busy = True
    try:
        code = request.form_data.get("code", "")
        hold = request.form_data.get("hold_ms", "")
        if not code:
            return Response(request, "Error: No macro code", content_type="text/plain")
        if hold:
            try:
                click_hold_ms = max(0, min(1000, int(hold)))
            except:
                pass
        
        # URL + HTML decode the input (defensive against entity encoding)
        code = htmldecode(urldecode(code))
        
        # Compile (syntax check)
        bytecode, err = macro.compile(code)
        if err:
            return Response(request, f"Compile Error: {err}", content_type="text/plain; charset=utf-8")
        
        # Return disassembly without executing
        return Response(request, macro.disassemble(bytecode), content_type="text/plain; charset=utf-8")
    finally:
        macro_busy = False


@server.route("/mouse")
def mouse_control(request: Request):
    action = request.query_params.get("action", "click")
    p1 = request.query_params.get("p1", "L")
    p2 = request.query_params.get("p2", "1")
    
    if action == "click":
        btn_map = {"l": 1, "left": 1, "r": 2, "right": 2, "m": 4, "middle": 4}
        btn = btn_map.get(p1.lower(), 1)
        try:
            count = max(1, int(p2))
        except:
            count = 1
        for _ in range(count):
            mouse.click(btn)
        return Response(request, f"Clicked {p1} x{count}", content_type="text/plain; charset=utf-8")
    
    elif action == "move":
        try:
            x = int(p1)
        except:
            x = 0
        try:
            y = int(p2)
        except:
            y = 0
        # Move in chunks
        while x != 0 or y != 0:
            mx = max(-127, min(127, x))
            my = max(-127, min(127, y))
            mouse.move(x=mx, y=my)
            x -= mx
            y -= my
        return Response(request, f"Moved ({p1},{p2})", content_type="text/plain; charset=utf-8")
    
    return Response(request, f"Unknown action: {action}", content_type="text/plain; charset=utf-8")


print(f"Starting server at http://{wifi.radio.ipv4_address}")
server.serve_forever(str(wifi.radio.ipv4_address), 80)
