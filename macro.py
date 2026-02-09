# Macro Interpreter for HID simulation
# LaTeX-style syntax: \enter \ctrl+c \delay{0.1} \rep{3}{abc}

import time
from adafruit_hid.keycode import Keycode
from adafruit_hid.mouse import Mouse

# Opcodes
OP_CHAR = 0x01  # <ascii>
OP_KEY = 0x02  # <keycode>
OP_COMBO = 0x03  # <mod_mask> <keycode>
OP_DELAY = 0x04  # <ms_lo> <ms_hi>
OP_MCLICK = 0x05  # <btn> <count_lo> <count_hi>
OP_MMOVE = 0x06  # <dx_lo> <dx_hi> <dy_lo> <dy_hi>
OP_LOOP = 0x10  # <count> <len_lo> <len_hi>
OP_LOOP_END = 0x11
OP_KEY_DOWN = 0x12  # <keycode>
OP_KEY_UP = 0x13  # <keycode> (0 = release all)
OP_MDOWN = 0x14  # <btn>
OP_MUP = 0x15  # <btn>
OP_END = 0xFF

MAX_BYTECODE = 4096
MAX_NEST = 2

# Character set helpers (CircuitPython str lacks isalnum/isalpha)
_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ALNUM = _ALPHA + "0123456789"


def _is_alpha(ch):
    return ch in _ALPHA


def _is_alnum(ch):
    return ch in _ALNUM


class MacroError(Exception):
    def __init__(self, msg, pos):
        self.msg = msg
        self.pos = pos
        super().__init__(f"@{pos}: {msg}")


class Macro:
    # Key mappings
    KEYS = {
        "enter": Keycode.ENTER,
        "return": Keycode.RETURN,
        "esc": Keycode.ESCAPE,
        "escape": Keycode.ESCAPE,
        "tab": Keycode.TAB,
        "space": Keycode.SPACE,
        "bs": Keycode.BACKSPACE,
        "backspace": Keycode.BACKSPACE,
        "del": Keycode.DELETE,
        "delete": Keycode.DELETE,
        "up": Keycode.UP_ARROW,
        "down": Keycode.DOWN_ARROW,
        "left": Keycode.LEFT_ARROW,
        "right": Keycode.RIGHT_ARROW,
        "home": Keycode.HOME,
        "end": Keycode.END,
        "pgup": Keycode.PAGE_UP,
        "pgdn": Keycode.PAGE_DOWN,
        "ins": Keycode.INSERT,
        "caps": Keycode.CAPS_LOCK,
        "f1": Keycode.F1,
        "f2": Keycode.F2,
        "f3": Keycode.F3,
        "f4": Keycode.F4,
        "f5": Keycode.F5,
        "f6": Keycode.F6,
        "f7": Keycode.F7,
        "f8": Keycode.F8,
        "f9": Keycode.F9,
        "f10": Keycode.F10,
        "f11": Keycode.F11,
        "f12": Keycode.F12,
        "ctrl": Keycode.CONTROL,
        "shift": Keycode.SHIFT,
        "alt": Keycode.ALT,
        "win": Keycode.GUI,
        "gui": Keycode.GUI,
    }

    MODS = {
        "ctrl": 0x01,
        "control": 0x01,
        "shift": 0x02,
        "alt": 0x04,
        "option": 0x04,
        "opt": 0x04,
        "win": 0x08,
        "gui": 0x08,
        "cmd": 0x08,
    }

    MOD_KEYS = [Keycode.CONTROL, Keycode.SHIFT, Keycode.ALT, Keycode.GUI]

    DIGIT_KEYS = {
        "0": Keycode.ZERO,
        "1": Keycode.ONE,
        "2": Keycode.TWO,
        "3": Keycode.THREE,
        "4": Keycode.FOUR,
        "5": Keycode.FIVE,
        "6": Keycode.SIX,
        "7": Keycode.SEVEN,
        "8": Keycode.EIGHT,
        "9": Keycode.NINE,
    }

    MOUSE_BTNS = {
        "l": Mouse.LEFT_BUTTON,
        "left": Mouse.LEFT_BUTTON,
        "r": Mouse.RIGHT_BUTTON,
        "right": Mouse.RIGHT_BUTTON,
        "m": Mouse.MIDDLE_BUTTON,
        "middle": Mouse.MIDDLE_BUTTON,
    }

    def __init__(self, keyboard, layout, mouse):
        self.keyboard = keyboard
        self.layout = layout
        self.mouse = mouse

    def compile(self, text):
        """Compile macro text to bytecode. Returns (bytecode, error_msg)."""
        self._bc = bytearray()
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._nest = []  # [(text_pos, bc_pos)]

        try:
            self._parse()
            self._emit(OP_END)
            return bytes(self._bc), None
        except MacroError as e:
            return None, str(e)

    def disassemble(self, bytecode):
        """Disassemble bytecode to human-readable format."""
        lines = []
        i = 0
        indent = 0

        while i < len(bytecode):
            op = bytecode[i]
            prefix = "  " * indent

            if op == OP_CHAR:
                ch = (
                    chr(bytecode[i + 1])
                    if bytecode[i + 1] < 128
                    else f"\\x{bytecode[i+1]:02x}"
                )
                lines.append(f"{prefix}CHAR '{ch}'")
                i += 2
            elif op == OP_KEY:
                key_name = self._keycode_name(bytecode[i + 1])
                lines.append(f"{prefix}KEY {key_name}")
                i += 2
            elif op == OP_COMBO:
                mods = self._mod_names(bytecode[i + 1])
                key_name = self._keycode_name(bytecode[i + 2])
                lines.append(f"{prefix}COMBO {mods}+{key_name}")
                i += 3
            elif op == OP_DELAY:
                ms = bytecode[i + 1] | (bytecode[i + 2] << 8)
                lines.append(f"{prefix}DELAY {ms}ms")
                i += 3
            elif op == OP_MCLICK:
                btn = (
                    "0LR3M"[bytecode[i + 1]]
                    if bytecode[i + 1] <= 4
                    else str(bytecode[i + 1])
                )
                count = bytecode[i + 2] | (bytecode[i + 3] << 8)
                lines.append(f"{prefix}MCLICK {btn} x{count}")
                i += 4
            elif op == OP_MMOVE:
                dx_raw = bytecode[i + 1] | (bytecode[i + 2] << 8)
                dy_raw = bytecode[i + 3] | (bytecode[i + 4] << 8)
                dx = dx_raw if dx_raw < 32768 else dx_raw - 65536
                dy = dy_raw if dy_raw < 32768 else dy_raw - 65536
                lines.append(f"{prefix}MMOVE ({dx},{dy})")
                i += 5
            elif op == OP_LOOP:
                count = bytecode[i + 1]
                length = bytecode[i + 2] | (bytecode[i + 3] << 8)
                lines.append(f"{prefix}LOOP x{count} ({length}B) " + "{{")
                indent += 1
                i += 4
            elif op == OP_LOOP_END:
                indent -= 1
                lines.append(f"{prefix}" + "}}LOOP_END")
                i += 1
            elif op == OP_KEY_DOWN:
                key_name = self._keycode_name(bytecode[i + 1])
                lines.append(f"{prefix}KEY_DOWN {key_name}")
                i += 2
            elif op == OP_KEY_UP:
                if bytecode[i + 1] == 0:
                    lines.append(f"{prefix}KEY_UP all")
                else:
                    key_name = self._keycode_name(bytecode[i + 1])
                    lines.append(f"{prefix}KEY_UP {key_name}")
                i += 2
            elif op == OP_MDOWN:
                btn = (
                    "0LR3M"[bytecode[i + 1]]
                    if bytecode[i + 1] <= 4
                    else str(bytecode[i + 1])
                )
                lines.append(f"{prefix}MDOWN {btn}")
                i += 2
            elif op == OP_MUP:
                btn = (
                    "0LR3M"[bytecode[i + 1]]
                    if bytecode[i + 1] <= 4
                    else str(bytecode[i + 1])
                )
                lines.append(f"{prefix}MUP {btn}")
                i += 2
            elif op == OP_END:
                lines.append(f"{prefix}END")
                break
            else:
                lines.append(f"{prefix}UNKNOWN 0x{op:02X}")
                i += 1

        return "\n".join(lines)

    def _keycode_name(self, code):
        """Reverse lookup keycode name."""
        for name, kc in self.KEYS.items():
            if kc == code:
                return name.upper()
        # Try common ASCII codes
        if code >= 0x04 and code <= 0x1D:  # A-Z
            return chr(ord("A") + code - 0x04)
        return f"0x{code:02X}"

    def _mod_names(self, mask):
        """Convert modifier mask to names."""
        mods = []
        if mask & 0x01:
            mods.append("CTRL")
        if mask & 0x02:
            mods.append("SHIFT")
        if mask & 0x04:
            mods.append("ALT")
        if mask & 0x08:
            mods.append("GUI")
        return "+".join(mods) if mods else "NONE"

    def _char_keycode(self, ch):
        if len(ch) != 1:
            return None
        if _is_alpha(ch):
            return getattr(Keycode, ch.upper())
        if ch in self.DIGIT_KEYS:
            return self.DIGIT_KEYS[ch]
        return None

    def _resolve_key(self, name, pos):
        if name in self.KEYS:
            return self.KEYS[name]
        if len(name) == 1:
            key = self._char_keycode(name)
            if key is None:
                raise MacroError(f"Unknown key: {name}", pos)
            return key
        raise MacroError(f"Unknown key: {name}", pos)

    def _emit(self, *args):
        if len(self._bc) >= MAX_BYTECODE - 10:
            raise MacroError("Bytecode limit exceeded", self._pos)
        for b in args:
            if isinstance(b, int):
                self._bc.append(b & 0xFF)
            else:
                self._bc.extend(b)

    def _emit_u16(self, v):
        self._emit(v & 0xFF, (v >> 8) & 0xFF)

    def _emit_i8(self, v):
        v = max(-127, min(127, v))
        self._emit(v & 0xFF)

    def _emit_i16(self, v):
        v = max(-32767, min(32767, v))
        v = v & 0xFFFF if v >= 0 else (v + 65536) & 0xFFFF
        self._emit(v & 0xFF, (v >> 8) & 0xFF)

    def _peek(self):
        return self._text[self._pos] if self._pos < self._len else None

    def _advance(self):
        ch = self._peek()
        self._pos += 1
        return ch

    def _read_name(self):
        """Read alphanumeric command name."""
        start = self._pos
        while self._pos < self._len:
            ch = self._text[self._pos]
            if _is_alnum(ch) or ch == "_":
                self._pos += 1
            else:
                break
        return self._text[start : self._pos].lower()

    def _read_braces(self):
        """Read content between { and }."""
        if self._peek() != "{":
            return None
        self._advance()  # skip {
        start = self._pos
        depth = 1
        while self._pos < self._len and depth > 0:
            ch = self._advance()
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        if depth != 0:
            raise MacroError("Unmatched {", start - 1)
        return self._text[start : self._pos - 1]

    def _parse_key_or_combo(self, name):
        """Parse key/combo starting with name, handle + chains."""
        mods = 0
        key = None
        pos = self._pos

        # First part
        if name in self.MODS:
            mods |= self.MODS[name]
        else:
            key = self._resolve_key(name, pos)

        # Handle + chains: \ctrl+\shift+c
        while self._peek() == "+":
            self._advance()  # skip +
            if self._peek() == "\\":
                self._advance()  # skip \
                next_name = self._read_name()
            elif self._peek() and _is_alnum(self._peek()):
                next_name = self._read_name()
            else:
                raise MacroError("Expected key after +", self._pos)

            if next_name in self.MODS:
                mods |= self.MODS[next_name]
            else:
                key = self._resolve_key(next_name, self._pos)

        # Skip empty {} separator
        if (
            self._peek() == "{"
            and self._pos + 1 < self._len
            and self._text[self._pos + 1] == "}"
        ):
            self._pos += 2

        # Emit
        if mods and key:
            self._emit(OP_COMBO, mods, key)
        elif mods:
            self._emit(OP_COMBO, mods, 0)
        elif key:
            self._emit(OP_KEY, key)

    def _parse_command(self):
        """Parse command after backslash."""
        pos = self._pos
        name = self._read_name()

        if not name:
            raise MacroError("Expected command name", pos)

        handler = getattr(self, "_cmd_" + name, None)
        if handler:
            handler(pos)
            return

        # Otherwise it's a key or combo: \enter, \ctrl+c
        self._parse_key_or_combo(name)

    def _cmd_delay(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("delay requires {value}", self._pos)
        try:
            ms = int(float(arg) * 1000)
            ms = max(1, min(65535, ms))
            self._emit(OP_DELAY)
            self._emit_u16(ms)
        except:
            raise MacroError("Invalid delay value", pos)

    def _cmd_rep(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("rep requires {count}", self._pos)
        try:
            count = int(arg)
            count = max(1, min(255, count))
        except:
            raise MacroError("Invalid rep count", pos)

        body = self._read_braces()
        if body is None:
            raise MacroError("rep requires {body}", self._pos)

        # Compile body
        sub = Macro(self.keyboard, self.layout, self.mouse)
        sub_bc, err = sub.compile(body)
        if err:
            raise MacroError(f"In rep body: {err}", pos)

        # Remove trailing OP_END
        sub_bc = sub_bc[:-1]
        body_len = len(sub_bc) + 1  # +1 for LOOP_END

        self._emit(OP_LOOP, count)
        self._emit_u16(body_len)
        self._emit(sub_bc)
        self._emit(OP_LOOP_END)

    def _cmd_kdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kdown requires {key}", self._pos)
        arg = arg.strip().lower()
        self._emit(OP_KEY_DOWN, self._resolve_key(arg, pos))

    def _cmd_kup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kup requires {key} or {}", self._pos)
        arg = arg.strip().lower()
        if not arg:
            self._emit(OP_KEY_UP, 0)  # release all
        else:
            self._emit(OP_KEY_UP, self._resolve_key(arg, pos))

    def _cmd_click(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("click requires {btn} or {btn,count}", self._pos)
        parts = arg.split(",")
        btn = self.MOUSE_BTNS.get(parts[0].strip().lower(), 1)
        count = 1
        if len(parts) >= 2:
            try:
                count = int(parts[1].strip())
                count = max(1, min(65535, count))
            except:
                raise MacroError("Invalid click count", pos)
        self._emit(OP_MCLICK, btn)
        self._emit_u16(count)

    def _cmd_move(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("move requires {dx,dy}", self._pos)
        parts = arg.split(",")
        try:
            dx = int(parts[0].strip())
        except:
            raise MacroError("Invalid move dx", pos)
        dy = 0
        if len(parts) >= 2:
            try:
                dy = int(parts[1].strip())
            except:
                raise MacroError("Invalid move dy", pos)

        # Emit in chunks of 32767
        while dx != 0 or dy != 0:
            mx = max(-32767, min(32767, dx))
            my = max(-32767, min(32767, dy))
            self._emit(OP_MMOVE)
            self._emit_i16(mx)
            self._emit_i16(my)
            dx -= mx
            dy -= my

    def _cmd_mdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("mdown requires {btn}", self._pos)
        btn = self.MOUSE_BTNS.get(arg.strip().lower(), 1)
        self._emit(OP_MDOWN, btn)

    def _cmd_mup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("mup requires {btn}", self._pos)
        btn = self.MOUSE_BTNS.get(arg.strip().lower(), 1)
        self._emit(OP_MUP, btn)

    def _parse(self):
        """Main parse loop."""
        while self._pos < self._len:
            ch = self._peek()

            # Command: \xxx
            if ch == "\\":
                self._advance()
                next_ch = self._peek()

                # Escape sequences
                if next_ch in "\\{}":
                    self._emit(OP_CHAR, ord(next_ch))
                    self._advance()
                elif next_ch and _is_alpha(next_ch):
                    self._parse_command()
                else:
                    raise MacroError("Invalid escape sequence", self._pos)
                continue

            # Regular character
            if ch == "\n":
                # Unix/Mac line ending - emit ENTER
                self._emit(OP_KEY, Keycode.ENTER)
            elif ch == "\r":
                # Windows CR or old Mac - check if followed by \n
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "\n":
                    # Windows \r\n - emit ENTER and skip both chars
                    self._emit(OP_KEY, Keycode.ENTER)
                    self._advance()  # skip \r
                    self._advance()  # skip \n
                    continue
                else:
                    # Standalone \r - emit ENTER
                    self._emit(OP_KEY, Keycode.ENTER)
            elif ch == "\t":
                self._emit(OP_KEY, Keycode.TAB)
            elif ord(ch) >= 32 and ord(ch) <= 126:
                self._emit(OP_CHAR, ord(ch))
            else:
                raise MacroError(f"Invalid char: 0x{ord(ch):02X}", self._pos)

            self._advance()

    def execute(self, bytecode, default_delay_ms=50, click_hold_ms=10):
        """Execute compiled bytecode."""
        bc = bytecode
        n = len(bc)
        ip = 0
        delay_ms = default_delay_ms
        loop_stack = []  # [(ip_start, remaining, saved_delay)]

        def read_u8():
            nonlocal ip
            v = bc[ip]
            ip += 1
            return v

        def read_u16():
            nonlocal ip
            v = bc[ip] | (bc[ip + 1] << 8)
            ip += 2
            return v

        def read_i8():
            v = read_u8()
            return v - 256 if v > 127 else v

        def read_i16():
            v = read_u16()
            return v - 65536 if v > 32767 else v

        def do_delay():
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

        def do_click_hold():
            if click_hold_ms > 0:
                time.sleep(click_hold_ms / 1000.0)

        while ip < n:
            op = read_u8()

            if op == OP_END:
                break

            elif op == OP_CHAR:
                ch = chr(read_u8())
                try:
                    codes = self.layout.keycodes(ch)
                except:
                    codes = None
                if codes:
                    for kc in codes:
                        self.keyboard.press(kc)
                    do_click_hold()
                    self.keyboard.release_all()
                else:
                    # Fallback: layout.write (no hold control)
                    self.layout.write(ch, delay=0)
                    do_click_hold()
                do_delay()

            elif op == OP_KEY:
                self.keyboard.press(read_u8())
                do_click_hold()
                self.keyboard.release_all()
                do_delay()

            elif op == OP_COMBO:
                mods = read_u8()
                key = read_u8()
                for i in range(4):
                    if mods & (1 << i):
                        self.keyboard.press(self.MOD_KEYS[i])
                if key:
                    self.keyboard.press(key)
                    do_click_hold()
                    self.keyboard.release(key)
                self.keyboard.release_all()
                do_delay()

            elif op == OP_DELAY:
                delay_ms = read_u16()
                # Immediately execute this delay
                time.sleep(delay_ms / 1000.0)

            elif op == OP_MCLICK:
                btn = read_u8()
                count = read_u16()
                for i in range(count):
                    self.mouse.press(btn)
                    do_click_hold()
                    self.mouse.release(btn)
                    if i < count - 1:
                        do_delay()
                do_delay()

            elif op == OP_MMOVE:
                dx = read_i16()
                dy = read_i16()
                self.mouse.move(x=dx, y=dy)
                do_delay()

            elif op == OP_LOOP:
                count = read_u8()
                body_len = read_u16()
                if count > 1:
                    loop_stack.append((ip, count - 1, delay_ms))

            elif op == OP_LOOP_END:
                if loop_stack:
                    start, remaining, saved_delay = loop_stack[-1]
                    if remaining > 0:
                        loop_stack[-1] = (start, remaining - 1, saved_delay)
                        ip = start
                    else:
                        loop_stack.pop()
                        delay_ms = saved_delay

            elif op == OP_KEY_DOWN:
                self.keyboard.press(read_u8())

            elif op == OP_KEY_UP:
                key = read_u8()
                if key == 0:
                    self.keyboard.release_all()
                else:
                    self.keyboard.release(key)

            elif op == OP_MDOWN:
                self.mouse.press(read_u8())

            elif op == OP_MUP:
                self.mouse.release(read_u8())

            else:
                break  # Unknown opcode

        return True

    def run(self, text, default_delay_ms=50):
        """Compile and execute macro. Returns (success, message)."""
        bytecode, err = self.compile(text)
        if err:
            return False, f"Compile: {err}"
        try:
            self.execute(bytecode, default_delay_ms)
            return True, f"OK: {len(bytecode)}B"
        except Exception as e:
            return False, f"Runtime: {e}"
