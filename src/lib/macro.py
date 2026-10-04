# Macro Interpreter for HID simulation — runtime-agnostic compiler + async VM
# DSL v2: plain text, \enter, \ctrl+c, \delay{0.1} (one-shot sleep),
#         \pace{0.05} (inter-action delay), \rep{3}{abc}, \kdown/\kup,
#         \click/\move/\mdown/\mup, line continuation (backslash + newline),
#         comments (\# to end of line)
#
# Hardware access goes through a HID provider object with this interface:
#   key_press(keycode) / key_release(keycode) / keys_release_all()
#   char_keycodes(ch) -> tuple of keycodes (incl. modifiers) or None
#   mouse_press(btn) / mouse_release(btn) / mouse_move(dx, dy)
# Implementations: lib/hid_adafruit.py (device), tests/mock_hid.py (tests).

import asyncio

from keymap import (
    ENTER, ESCAPE, BACKSPACE, TAB, SPACE, DELETE,
    RIGHT_ARROW, LEFT_ARROW, DOWN_ARROW, UP_ARROW,
    PAGE_UP, PAGE_DOWN, HOME, END, INSERT, CAPS_LOCK,
    F1, F2, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12,
    CONTROL, SHIFT, ALT, GUI,
    BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_BACK, BTN_FORWARD, BTN_ALL,
    letter_keycode, digit_keycode,
)

# Bytecode format header: MAGIC, VERSION, then opcodes
MAGIC = 0xA5
VERSION = 2

# Opcodes
OP_CHAR = 0x01  # <ascii>
OP_KEY = 0x02  # <keycode>
OP_COMBO = 0x03  # <mod_mask> <keycode>
OP_SLEEP = 0x04  # <ms_lo> <ms_hi> — one-shot sleep (DSL \delay)
OP_MCLICK = 0x05  # <btn> <count_lo> <count_hi>
OP_MMOVE = 0x06  # <dx_lo> <dx_hi> <dy_lo> <dy_hi>
OP_PACE = 0x07  # <ms_lo> <ms_hi> — set inter-action delay (DSL \pace)
OP_LOOP = 0x10  # <count> <len_lo> <len_hi>; len = body bytes incl. LOOP_END
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
    KEYS = {
        "enter": ENTER,
        "return": ENTER,
        "esc": ESCAPE,
        "escape": ESCAPE,
        "tab": TAB,
        "space": SPACE,
        "bs": BACKSPACE,
        "backspace": BACKSPACE,
        "del": DELETE,
        "delete": DELETE,
        "up": UP_ARROW,
        "down": DOWN_ARROW,
        "left": LEFT_ARROW,
        "right": RIGHT_ARROW,
        "home": HOME,
        "end": END,
        "pgup": PAGE_UP,
        "pgdn": PAGE_DOWN,
        "ins": INSERT,
        "caps": CAPS_LOCK,
        "f1": F1, "f2": F2, "f3": F3, "f4": F4, "f5": F5, "f6": F6,
        "f7": F7, "f8": F8, "f9": F9, "f10": F10, "f11": F11, "f12": F12,
        "ctrl": CONTROL,
        "shift": SHIFT,
        "alt": ALT,
        "win": GUI,
        "gui": GUI,
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

    MOD_KEYS = [CONTROL, SHIFT, ALT, GUI]

    MOUSE_BTNS = {
        "l": BTN_LEFT,
        "left": BTN_LEFT,
        "r": BTN_RIGHT,
        "right": BTN_RIGHT,
        "m": BTN_MIDDLE,
        "middle": BTN_MIDDLE,
        "b": BTN_BACK,
        "back": BTN_BACK,
        "x1": BTN_BACK,
        "f": BTN_FORWARD,
        "forward": BTN_FORWARD,
        "fwd": BTN_FORWARD,
        "x2": BTN_FORWARD,
    }

    _BTN_NAMES = {1: "L", 2: "R", 4: "M", 8: "B", 16: "F"}

    def __init__(self, hid):
        self.hid = hid

    def compile(self, text, _depth=0, _defs=None, _chain=()):
        """Compile macro text to bytecode. Returns (bytecode, error_msg).

        _defs/_chain carry user-macro state across recursive compiles
        (rep bodies and macro expansions share the same definition table).
        """
        self._bc = bytearray()
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._depth = _depth
        self._defs = _defs if _defs is not None else {}
        self._chain = _chain

        try:
            self._emit(MAGIC, VERSION)
            self._parse()
            self._emit(OP_END)
            return bytes(self._bc), None
        except MacroError as e:
            return None, str(e)

    def disassemble(self, bytecode):
        """Disassemble bytecode to human-readable format."""
        if len(bytecode) < 3 or bytecode[0] != MAGIC or bytecode[1] != VERSION:
            raise ValueError("Bytecode version mismatch")
        lines = [f"; bytecode v{VERSION}, {len(bytecode)}B"]
        i = 2
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
            elif op == OP_SLEEP:
                ms = bytecode[i + 1] | (bytecode[i + 2] << 8)
                lines.append(f"{prefix}SLEEP {ms}ms")
                i += 3
            elif op == OP_PACE:
                ms = bytecode[i + 1] | (bytecode[i + 2] << 8)
                lines.append(f"{prefix}PACE {ms}ms")
                i += 3
            elif op == OP_MCLICK:
                btn = self._BTN_NAMES.get(bytecode[i + 1], str(bytecode[i + 1]))
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
                lines.append(f"{prefix}LOOP x{count} ({length}B) " + "{")
                indent += 1
                i += 4
            elif op == OP_LOOP_END:
                indent -= 1
                prefix = "  " * indent
                lines.append(prefix + "}LOOP_END")
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
                btn = self._BTN_NAMES.get(bytecode[i + 1], str(bytecode[i + 1]))
                lines.append(f"{prefix}MDOWN {btn}")
                i += 2
            elif op == OP_MUP:
                btn = self._BTN_NAMES.get(bytecode[i + 1], str(bytecode[i + 1]))
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
        # sorted(): deterministic across runtimes -- CircuitPython dicts are
        # hash-ordered, not insertion-ordered like CPython (enter/return alias)
        for name in sorted(self.KEYS):
            if self.KEYS[name] == code:
                return name.upper()
        if 0x04 <= code <= 0x1D:  # A-Z
            return chr(ord("A") + code - 0x04)
        if 0x1E <= code <= 0x26:  # 1-9
            return chr(ord("1") + code - 0x1E)
        if code == 0x27:
            return "0"
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
            return letter_keycode(ch)
        if ch.isdigit():
            return digit_keycode(ch)
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
        r"""Read content between { and }. Escaped braces/backslashes (\{ \} \\)
        are not counted toward nesting depth."""
        if self._peek() != "{":
            return None
        self._advance()  # skip {
        start = self._pos
        depth = 1
        while self._pos < self._len and depth > 0:
            ch = self._advance()
            nxt = self._peek()
            if ch == "\\" and nxt is not None and nxt in "{}\\":
                self._advance()  # escaped char: skip, don't count
                continue
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

        # User-defined macro: \name{arg}... (compile-time expansion)
        if name in self._defs:
            self._expand_macro(name, pos)
            return

        # Otherwise it's a key or combo: \enter, \ctrl+c
        self._parse_key_or_combo(name)

    def _cmd_def(self, pos):
        r"""\def{name}[argc]{body} — define a compile-time user macro (top level only)."""
        if self._depth > 0:
            raise MacroError("def only allowed at top level", pos)
        name = self._read_braces()
        if name is None:
            raise MacroError("def requires {name}", self._pos)
        name = name.strip().lower()
        if (
            len(name) < 2
            or not _is_alpha(name[0])
            or not all(_is_alnum(c) or c == "_" for c in name)
        ):
            raise MacroError(
                "macro name must be >=2 chars, [a-z0-9_], starting with a letter", pos
            )
        if name in self._defs:
            raise MacroError(f"macro redefined: {name}", pos)
        if getattr(self, "_cmd_" + name, None) or name in self.KEYS or name in self.MODS:
            raise MacroError(f"macro name conflicts with builtin: {name}", pos)

        argc = 0
        if self._peek() == "[":
            self._advance()  # skip [
            start = self._pos
            while self._peek() is not None and self._peek() != "]":
                self._advance()
            if self._peek() != "]":
                raise MacroError("Unmatched [", start)
            try:
                argc = int(self._text[start : self._pos])
                if not 0 <= argc <= 9:
                    raise ValueError
            except ValueError:
                raise MacroError("def argc must be 0-9", start)
            self._advance()  # skip ]

        body = self._read_braces()
        if body is None:
            raise MacroError("def requires {body}", self._pos)
        self._defs[name] = (argc, body)

    def _expand_macro(self, name, pos):
        """Inline-expand a user macro at the call site (compile-time)."""
        if name in self._chain:
            raise MacroError(f"recursive macro: {name}", pos)
        if self._depth >= MAX_NEST:
            raise MacroError(f"macro expansion exceeds {MAX_NEST} levels", pos)
        argc, body = self._defs[name]
        args = []
        for _ in range(argc):
            a = self._read_braces()
            if a is None:
                raise MacroError(f"macro {name} expects {argc} arg(s)", self._pos)
            args.append(a)
        expanded = self._substitute(body, args, name, pos)
        sub = Macro(self.hid)
        sub_bc, err = sub.compile(
            expanded,
            _depth=self._depth + 1,
            _defs=self._defs,
            _chain=self._chain + (name,),
        )
        if err:
            raise MacroError(f"In macro {name}: {err}", pos)
        self._emit(sub_bc[2:-1])  # strip sub-compile header and trailing END

    @staticmethod
    def _substitute(body, args, name, pos):
        """Single-pass replacement of #1..#9 with args; ## -> literal '#'.
        Substituted text is NOT rescanned."""
        out = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "#" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt == "#":
                    out.append("#")
                    i += 2
                    continue
                if nxt.isdigit() and nxt != "0":
                    idx = int(nxt) - 1
                    if idx >= len(args):
                        raise MacroError(
                            f"macro {name}: #{nxt} used but only {len(args)} arg(s)", pos
                        )
                    out.append(args[idx])
                    i += 2
                    continue
            out.append(ch)
            i += 1
        return "".join(out)

    def _cmd_delay(self, pos):
        r"""\delay{t} — one-shot sleep of t seconds."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("delay requires {value}", self._pos)
        try:
            ms = int(float(arg) * 1000)
            ms = max(1, min(65535, ms))
            self._emit(OP_SLEEP)
            self._emit_u16(ms)
        except MacroError:
            raise
        except Exception:
            raise MacroError("Invalid delay value", pos)

    def _cmd_pace(self, pos):
        r"""\pace{t} — set the delay inserted after every following action."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("pace requires {value}", self._pos)
        try:
            ms = int(float(arg) * 1000)
            ms = max(0, min(65535, ms))
            self._emit(OP_PACE)
            self._emit_u16(ms)
        except MacroError:
            raise
        except Exception:
            raise MacroError("Invalid pace value", pos)

    def _cmd_rep(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("rep requires {count}", self._pos)
        try:
            count = int(arg)
            count = max(1, min(255, count))
        except Exception:
            raise MacroError("Invalid rep count", pos)

        body = self._read_braces()
        if body is None:
            raise MacroError("rep requires {body}", self._pos)

        # B1: enforce nesting limit (recursive compile of body)
        if self._depth >= MAX_NEST:
            raise MacroError(f"rep nesting exceeds {MAX_NEST} levels", pos)

        # Compile body (strip the sub-compile's 2-byte header and trailing OP_END)
        sub = Macro(self.hid)
        sub_bc, err = sub.compile(
            body, _depth=self._depth + 1, _defs=self._defs, _chain=self._chain
        )
        if err:
            raise MacroError(f"In rep body: {err}", pos)

        sub_bc = sub_bc[2:-1]
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
            except Exception:
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
        except Exception:
            raise MacroError("Invalid move dx", pos)
        dy = 0
        if len(parts) >= 2:
            try:
                dy = int(parts[1].strip())
            except Exception:
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

                if next_ch is None:
                    raise MacroError("Invalid escape sequence", self._pos)

                # Line continuation: backslash + newline is ignored entirely
                # (lets long macros be split into logical lines)
                if next_ch == "\n":
                    self._advance()
                    continue
                if next_ch == "\r":
                    self._advance()
                    if self._peek() == "\n":
                        self._advance()
                    continue

                # Comment: \# to end of line; the newline itself is consumed
                # (no ENTER emitted). Use \enter to type Enter explicitly.
                if next_ch == "#":
                    self._advance()  # skip #
                    while self._pos < self._len and self._text[self._pos] not in "\r\n":
                        self._pos += 1
                    if self._peek() == "\r":
                        self._advance()
                    if self._peek() == "\n":
                        self._advance()
                    continue

                # Escape sequences
                if next_ch in "\\{}":
                    self._emit(OP_CHAR, ord(next_ch))
                    self._advance()
                elif _is_alpha(next_ch):
                    self._parse_command()
                else:
                    raise MacroError("Invalid escape sequence", self._pos)
                continue

            # Regular character
            if ch == "\n":
                # Unix/Mac line ending - emit ENTER
                self._emit(OP_KEY, ENTER)
            elif ch == "\r":
                # Windows CR or old Mac - check if followed by \n
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "\n":
                    # Windows \r\n - emit ENTER and skip both chars
                    self._emit(OP_KEY, ENTER)
                    self._advance()  # skip \r
                    self._advance()  # skip \n
                    continue
                else:
                    # Standalone \r - emit ENTER
                    self._emit(OP_KEY, ENTER)
            elif ch == "\t":
                self._emit(OP_KEY, TAB)
            elif ord(ch) >= 32 and ord(ch) <= 126:
                self._emit(OP_CHAR, ord(ch))
            else:
                raise MacroError(f"Invalid char: 0x{ord(ch):02X}", self._pos)

            self._advance()

    async def execute(self, bytecode, default_delay_ms=50, click_hold_ms=10):
        """Execute compiled bytecode.

        Any exit path (normal end, unknown opcode, runtime error, cancellation)
        releases all keys and mouse buttons (B2).
        """
        bc = bytecode
        n = len(bc)
        if n < 3 or bc[0] != MAGIC or bc[1] != VERSION:
            raise ValueError(
                f"Bytecode version mismatch (want magic=0x{MAGIC:02X} v{VERSION})"
            )
        ip = 2
        delay_ms = default_delay_ms
        loop_stack = []  # [(ip_start, remaining, saved_delay)]
        hid = self.hid

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

        def read_i16():
            v = read_u16()
            return v - 65536 if v > 32767 else v

        async def sleep_ms(ms):
            if ms > 0:
                await asyncio.sleep(ms / 1000.0)

        try:
            while ip < n:
                op = read_u8()

                if op == OP_END:
                    break

                elif op == OP_CHAR:
                    ch = chr(read_u8())
                    codes = hid.char_keycodes(ch)
                    if not codes:
                        raise RuntimeError(f"Unmappable char: {ch!r}")
                    for kc in codes:
                        hid.key_press(kc)
                    await sleep_ms(click_hold_ms)
                    hid.keys_release_all()
                    await sleep_ms(delay_ms)

                elif op == OP_KEY:
                    hid.key_press(read_u8())
                    await sleep_ms(click_hold_ms)
                    hid.keys_release_all()
                    await sleep_ms(delay_ms)

                elif op == OP_COMBO:
                    mods = read_u8()
                    key = read_u8()
                    for i in range(4):
                        if mods & (1 << i):
                            hid.key_press(self.MOD_KEYS[i])
                    if key:
                        hid.key_press(key)
                    await sleep_ms(click_hold_ms)
                    hid.keys_release_all()
                    await sleep_ms(delay_ms)

                elif op == OP_SLEEP:
                    # One-shot sleep (DSL \delay)
                    await sleep_ms(read_u16())

                elif op == OP_PACE:
                    # Set inter-action delay for following ops (DSL \pace)
                    delay_ms = read_u16()

                elif op == OP_MCLICK:
                    btn = read_u8()
                    count = read_u16()
                    for i in range(count):
                        hid.mouse_press(btn)
                        await sleep_ms(click_hold_ms)
                        hid.mouse_release(btn)
                        if i < count - 1:
                            await sleep_ms(delay_ms)
                    await sleep_ms(delay_ms)

                elif op == OP_MMOVE:
                    dx = read_i16()
                    dy = read_i16()
                    hid.mouse_move(dx, dy)
                    await sleep_ms(delay_ms)

                elif op == OP_LOOP:
                    count = read_u8()
                    body_len = read_u16()
                    if ip + body_len > n:
                        raise RuntimeError("LOOP body overruns bytecode")
                    if count == 0:
                        ip += body_len  # skip body entirely
                    elif count > 1:
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
                    hid.key_press(read_u8())

                elif op == OP_KEY_UP:
                    key = read_u8()
                    if key == 0:
                        hid.keys_release_all()
                    else:
                        hid.key_release(key)

                elif op == OP_MDOWN:
                    hid.mouse_press(read_u8())

                elif op == OP_MUP:
                    hid.mouse_release(read_u8())

                else:
                    raise RuntimeError(f"Unknown opcode 0x{op:02X}")

            return True
        finally:
            # B2: never leave keys/buttons pressed, whatever the exit path
            hid.keys_release_all()
            hid.mouse_release(BTN_ALL)
