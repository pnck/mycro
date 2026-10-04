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

# Bytecode format header: MAGIC, VERSION, STR_COUNT, strings..., then opcodes
MAGIC = 0xA5
VERSION = 3

# Opcodes
OP_CHAR = 0x01  # <ascii>
OP_KEY = 0x02  # <keycode>
OP_COMBO = 0x03  # <mod_mask> <keycode>
OP_SLEEP = 0x04  # typed(u16) — one-shot sleep (DSL \delay)
OP_MCLICK = 0x05  # typed(u8) btn, typed(u16) count
OP_MMOVE = 0x06  # typed(i16) dx, typed(i16) dy
OP_PACE = 0x07  # typed(u16) — set inter-action delay (DSL \pace)
OP_LOOP = 0x10  # typed(u8) count, u16 len (body bytes incl. LOOP_END)
OP_LOOP_END = 0x11
OP_KEY_DOWN = 0x12  # typed(u8) keycode
OP_KEY_UP = 0x13  # typed(u8) keycode (0 = release all)
OP_MDOWN = 0x14  # typed(u8) btn
OP_MUP = 0x15  # typed(u8) btn
OP_SET = 0x20  # reg, typed(i32)
OP_ADD = 0x21  # reg, typed(i32)
OP_BRA = 0x22  # cc, reg, typed(i32) rhs, u16 off — jump if cond FALSE
OP_JMP = 0x23  # u16 off
OP_READSLOT = 0x28  # reg, sig_str, field_str — read signal slot into register
OP_WAIT = 0x29  # sig_str, typed(u32) timeout_ms (0 = forever)
OP_CALL_EXT = 0x2A  # ns_str, fn_str, argc, typed args...
OP_TYPEREG = 0x2B  # reg — render register as decimal keystrokes (DSL \val)
OP_END = 0xFF

# Typed-operand tags (physical arg encodings; semantic types never reach bytecode)
TAG_IMM = 0
TAG_REG = 1
TAG_STR = 2  # string table index (ext args only)

# Register space
MAX_REGS = 16  # user registers 0x00-0x0F
REG_SCRATCH = 0xFD  # reserved (special-register guard boundary)
REG_TIMEOUT = 0xFE  # $timeout
REG_RET = 0xFF  # $ret

# \ifnum condition codes
CC_OPS = {"=": 0, "!=": 1, "<": 2, "<=": 3, ">": 4, ">=": 5}

MAX_BYTECODE = 4096
MAX_NEST = 4

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

    def __init__(self, hid, runtime=None):
        self.hid = hid
        self.runtime = runtime  # Runtime registry; required for \use/\call/\wait/slots

    def compile(self, text):
        """Compile macro text to bytecode. Returns (bytecode, error_msg).

        Nesting (rep/ifnum/macro bodies) is parsed iteratively on an explicit
        heap stack (self._nest_stack), NOT by Python recursion: the device
        pystack only survives ~2 levels of the old recursive compile chain
        (each level cost 5 fat frames: compile/_parse/_parse_command/
        _cmd_*/_compile_sub) once the asyncio+httpserver baseline is on it.
        """
        self._bc = bytearray()
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._depth = 0
        self._defs = {}
        self._chain = ()
        self._vars = {}
        self._strs = []
        self._strmap = {}
        self._nest_stack = []

        try:
            self._emit(MAGIC, VERSION, 0)  # header placeholder, rebuilt below
            self._parse()
            self._emit(OP_END)
            # Rebuild the header with the collected string table
            hdr = bytearray([MAGIC, VERSION, len(self._strs)])
            for s in self._strs:
                hdr.append(len(s.encode("utf-8")))
                hdr.extend(s.encode("utf-8"))
            return bytes(hdr + self._bc[3:]), None
        except MacroError as e:
            # Rebuild the "In <context>: ..." chain from the nest stack,
            # innermost first — same message shape the recursive compiler made.
            msg = str(e)
            for fr in reversed(self._nest_stack):
                msg = "In {}: {}".format(fr[6], msg)
            return None, msg

    @staticmethod
    def _read_header(bc):
        """Validate header and return (strings, code_start_offset)."""
        if len(bc) < 3 or bc[0] != MAGIC or bc[1] != VERSION:
            raise ValueError(
                f"Bytecode version mismatch (want magic=0x{MAGIC:02X} v{VERSION})"
            )
        n = bc[2]
        i = 3
        strs = []
        for _ in range(n):
            ln = bc[i]
            i += 1
            strs.append(bytes(bc[i : i + ln]).decode("utf-8"))
            i += ln
        return strs, i

    def disassemble(self, bytecode):
        """Disassemble bytecode to human-readable format."""
        strs, i = self._read_header(bytecode)
        lines = [f"; bytecode v{VERSION}, {len(bytecode)}B, {len(strs)} str(s)"]
        indent = 0
        CC_NAMES = ["=", "!=", "<", "<=", ">", ">="]

        def fmt_arg(width, signed=False):
            nonlocal i
            tag = bytecode[i]
            i += 1
            if tag == TAG_REG:
                r = f"$r{bytecode[i]}"
                i += 1
                return r
            if width == 1:
                v = bytecode[i]
                i += 1
            elif width == 2:
                v = bytecode[i] | (bytecode[i + 1] << 8)
                i += 2
                if signed and v > 32767:
                    v -= 65536
            else:
                v = (
                    bytecode[i]
                    | (bytecode[i + 1] << 8)
                    | (bytecode[i + 2] << 16)
                    | (bytecode[i + 3] << 24)
                )
                i += 4
                if v > 2147483647:
                    v -= 4294967296
            return str(v)

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
                i += 1
                lines.append(f"{prefix}SLEEP {fmt_arg(2)}ms")
            elif op == OP_PACE:
                i += 1
                lines.append(f"{prefix}PACE {fmt_arg(2)}ms")
            elif op == OP_MCLICK:
                i += 1
                btn = fmt_arg(1)
                count = fmt_arg(2)
                lines.append(f"{prefix}MCLICK {btn} x{count}")
            elif op == OP_MMOVE:
                i += 1
                dx = fmt_arg(2, signed=True)
                dy = fmt_arg(2, signed=True)
                lines.append(f"{prefix}MMOVE ({dx},{dy})")
            elif op == OP_LOOP:
                i += 1
                count = fmt_arg(1)
                length = bytecode[i] | (bytecode[i + 1] << 8)
                i += 2
                lines.append(f"{prefix}LOOP x{count} ({length}B) " + "{")
                indent += 1
            elif op == OP_LOOP_END:
                indent -= 1
                prefix = "  " * indent
                lines.append(prefix + "}LOOP_END")
                i += 1
            elif op == OP_KEY_DOWN:
                i += 1
                lines.append(f"{prefix}KEY_DOWN {fmt_arg(1)}")
            elif op == OP_KEY_UP:
                i += 1
                lines.append(f"{prefix}KEY_UP {fmt_arg(1)}")
            elif op == OP_MDOWN:
                i += 1
                lines.append(f"{prefix}MDOWN {fmt_arg(1)}")
            elif op == OP_MUP:
                i += 1
                lines.append(f"{prefix}MUP {fmt_arg(1)}")
            elif op == OP_SET:
                reg = bytecode[i + 1]
                i += 2
                lines.append(f"{prefix}SET $r{reg}, {fmt_arg(4)}")
            elif op == OP_ADD:
                reg = bytecode[i + 1]
                i += 2
                lines.append(f"{prefix}ADD $r{reg}, {fmt_arg(4)}")
            elif op == OP_BRA:
                cc = bytecode[i + 1]
                reg = bytecode[i + 2]
                i += 3
                rhs = fmt_arg(4)
                off = bytecode[i] | (bytecode[i + 1] << 8)
                i += 2
                cc_name = CC_NAMES[cc] if cc < len(CC_NAMES) else f"0x{cc:02X}"
                lines.append(f"{prefix}BRA $r{reg} {cc_name} {rhs} +{off}")
            elif op == OP_JMP:
                off = bytecode[i + 1] | (bytecode[i + 2] << 8)
                lines.append(f"{prefix}JMP +{off}")
                i += 3
            elif op == OP_TYPEREG:
                lines.append(f"{prefix}TYPEREG $r{bytecode[i + 1]}")
                i += 2
            elif op == OP_READSLOT:
                reg = bytecode[i + 1]
                sig = strs[bytecode[i + 2]]
                field = strs[bytecode[i + 3]]
                lines.append(f"{prefix}READSLOT $r{reg}, {sig}.{field}")
                i += 4
            elif op == OP_WAIT:
                sig = strs[bytecode[i + 1]]
                i += 2
                lines.append(f"{prefix}WAIT {sig} {fmt_arg(4)}ms")
            elif op == OP_CALL_EXT:
                ns = strs[bytecode[i + 1]]
                fn = strs[bytecode[i + 2]]
                argc = bytecode[i + 3]
                i += 4
                args = []
                for _ in range(argc):
                    tag = bytecode[i]
                    i += 1
                    if tag == TAG_IMM:
                        v = (
                            bytecode[i]
                            | (bytecode[i + 1] << 8)
                            | (bytecode[i + 2] << 16)
                            | (bytecode[i + 3] << 24)
                        )
                        i += 4
                        if v > 2147483647:
                            v -= 4294967296
                        args.append(str(v))
                    elif tag == TAG_REG:
                        args.append(f"$r{bytecode[i]}")
                        i += 1
                    else:
                        args.append(repr(strs[bytecode[i]]))
                        i += 1
                lines.append(f"{prefix}CALL {ns}.{fn}({', '.join(args)})")
            elif op == OP_END:
                lines.append(f"{prefix}END")
                break
            else:
                lines.append(f"{prefix}UNKNOWN 0x{op:02X}")
                i += 1

        return "\n".join(lines)

    def _keycode_name(self, code):
        """Reverse lookup keycode name. sorted() keeps the result deterministic
        across runtimes: CircuitPython dicts are hash-ordered, unlike
        CPython's insertion-ordered dicts (aliases like enter/return)."""
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

    def _emit_i16_raw(self, v):
        v = max(-32767, min(32767, v))
        v = v & 0xFFFF if v >= 0 else (v + 65536) & 0xFFFF
        self._emit(v & 0xFF, (v >> 8) & 0xFF)

    def _emit_i32(self, v):
        v &= 0xFFFFFFFF
        self._emit(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF, (v >> 24) & 0xFF)

    def _emit_typed(self, val, width):
        """val = (TAG_IMM, int) or (TAG_REG, reg); width in bytes (1/2/4)."""
        tag, v = val
        if tag == TAG_REG:
            self._emit(TAG_REG, v)
        elif width == 1:
            self._emit(TAG_IMM, v & 0xFF)
        elif width == 2:
            self._emit(TAG_IMM)
            self._emit_u16(v & 0xFFFF)
        else:
            self._emit(TAG_IMM)
            self._emit_i32(v)

    def _patch_u16(self, pos, v):
        """Backpatch a u16 placeholder at pos."""
        self._bc[pos] = v & 0xFF
        self._bc[pos + 1] = (v >> 8) & 0xFF

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

    def _nest_into(self, body, what, cont):
        """Swap the parser into a nested body. Parent state goes onto the
        explicit heap stack; `what` labels the body for error messages and
        `cont` tells _pop_nest how to wrap the finished sub-buffer.
        Callers do their own depth check first (messages differ)."""
        self._nest_stack.append(
            (self._text, self._pos, self._len, self._bc,
             self._depth, self._chain, what, cont)
        )
        self._text, self._pos, self._len = body, 0, len(body)
        self._bc = bytearray()
        self._depth += 1

    def _pop_nest(self):
        """A nested body is finished: restore the parent frame and wrap the
        sub-buffer per the continuation."""
        sub = self._bc
        (self._text, self._pos, self._len, self._bc,
         self._depth, self._chain, what, cont) = self._nest_stack.pop()
        kind = cont[0]
        if kind == "inline":  # macro expansion: splice the bytes in
            self._emit(sub)
        elif kind == "rep":
            count = cont[1]
            self._emit(OP_LOOP)
            self._emit_typed(count, 1)
            self._emit_u16(len(sub) + 1)  # +1 for LOOP_END
            self._emit(sub)
            self._emit(OP_LOOP_END)
        elif kind == "ifnum_then":
            off_pos, else_body = cont[1], cont[2]
            self._emit(sub)
            if else_body is not None:
                self._emit(OP_JMP)
                jmp_pos = len(self._bc)
                self._emit_u16(0)  # backpatched when the else body completes
                # BRA target = else start (right after the JMP operand)
                self._patch_u16(off_pos, (jmp_pos + 2) - (off_pos + 2))
                self._nest_into(else_body, "ifnum else", ("ifnum_else", jmp_pos))
            else:
                self._patch_u16(off_pos, len(self._bc) - (off_pos + 2))
        elif kind == "ifnum_else":
            jmp_pos = cont[1]
            self._emit(sub)
            self._patch_u16(jmp_pos, len(self._bc) - (jmp_pos + 2))

    def _str_idx(self, s, pos):
        """Intern a string into the shared string table (dedup)."""
        if s in self._strmap:
            return self._strmap[s]
        if len(self._strs) >= 255:
            raise MacroError("String table full (max 255)", pos)
        if len(s.encode("utf-8")) > 255:
            raise MacroError("String too long (max 255 bytes)", pos)
        idx = len(self._strs)
        self._strs.append(s)
        self._strmap[s] = idx
        return idx

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
        self._nest_into(expanded, f"macro {name}", ("inline",))
        self._chain = self._chain + (name,)

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

    # --- value-position parsing (R1/R2/R3): int literals, $var, ${var} ---

    def _parse_int_literal(self, s, pos):
        """i32 literal: decimal or 0x hex, optional leading '-'."""
        s = s.strip()
        neg = s.startswith("-")
        if neg:
            s = s[1:].strip()
        try:
            if s[:2].lower() == "0x":
                v = int(s[2:], 16)
            else:
                v = int(s)
        except ValueError:
            raise MacroError(f"Invalid integer: {s}", pos)
        if neg:
            v = -v
        if not (-2147483648 <= v <= 4294967295):
            raise MacroError("integer out of i32 range", pos)
        return v

    def _parse_value(self, s, pos, reserved=()):
        """Value-position segment: int literal or $var/${var}.
        Returns (TAG_IMM, v) or (TAG_REG, reg). `reserved` holds registers
        already consumed by sibling operands of the same instruction."""
        s = s.strip()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        return (TAG_IMM, self._parse_int_literal(s, pos))

    def _parse_var_ref(self, s, pos, reserved=()):
        """$name or ${name} -> register index (must be defined)."""
        if s.startswith("${"):
            end = s.find("}")
            if end == -1:
                raise MacroError("Unmatched ${", pos)
            name = s[2:end]
            if s[end + 1 :].strip():
                raise MacroError(f"Unexpected text after ${{{name}}}", pos)
        else:
            name = s[1:]
        return self._var_reg(name, pos, reserved)

    def _var_reg(self, name, pos, reserved=()):
        """Resolve a bare variable name to a register (read access).
        Signal slots ($sig.field) emit a READSLOT into a temp register."""
        if name == "ret":
            return REG_RET
        if name == "timeout":
            return REG_TIMEOUT
        if "." in name:
            # $sig.field — signal names may themselves contain dots
            # (net.msg), so split at the LAST dot
            sig, sep, field = name.rpartition(".")
            if not sep or not sig or not field:
                raise MacroError(f"Invalid slot reference: {name}", pos)
            if self.runtime is None or not self.runtime.has_slot_ns(sig):
                raise MacroError(f"Unknown signal: {sig}", pos)
            reg = self._alloc_temp(reserved, pos)
            self._emit(
                OP_READSLOT, reg, self._str_idx(sig, pos), self._str_idx(field, pos)
            )
            return reg
        if name not in self._vars:
            raise MacroError(f"Undefined variable: {name}", pos)
        return self._vars[name]

    def _alloc_temp(self, reserved, pos):
        """Temp register for a slot desugar, live only within the current
        instruction: taken from beyond the named-var block, skipping
        registers reserved by sibling operands."""
        blocked = set(self._vars.values()) | set(reserved)
        for r in range(MAX_REGS):
            if r not in blocked:
                return r
        raise MacroError("no free register for slot operand", pos)

    def _alloc_var(self, name, pos):
        """Resolve a variable name for assignment, allocating a register if new."""
        if name in ("ret", "timeout"):
            raise MacroError(f"Reserved variable name: {name}", pos)
        if "." in name:
            raise MacroError("'.' not allowed in variable names", pos)
        if (
            not name
            or not _is_alpha(name[0])
            or not all(_is_alnum(c) or c == "_" for c in name)
        ):
            raise MacroError(f"Invalid variable name: {name}", pos)
        if name not in self._vars:
            if len(self._vars) >= MAX_REGS:
                raise MacroError(f"Too many variables (max {MAX_REGS})", pos)
            self._vars[name] = len(self._vars)
        return self._vars[name]

    def _parse_time(self, s, pos, reserved=()):
        """Time arg: float seconds (imm -> ms) or $var (register holds ms)."""
        s = s.strip()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        try:
            ms = int(float(s) * 1000)
        except ValueError:
            raise MacroError(f"Invalid time value: {s}", pos)
        return (TAG_IMM, max(1, min(65535, ms)))

    def _parse_btn_arg(self, s, pos, reserved=()):
        """Mouse button: name or $var (register holds the bitmask)."""
        s = s.strip().lower()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        return (TAG_IMM, self.MOUSE_BTNS.get(s, 1))

    def _parse_key_arg(self, s, pos, reserved=()):
        """Key: name or $var (register holds the keycode)."""
        s = s.strip().lower()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        return (TAG_IMM, self._resolve_key(s, pos))

    # --- commands ---

    def _cmd_delay(self, pos):
        r"""\delay{t} — one-shot sleep of t seconds ($var = ms)."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("delay requires {value}", self._pos)
        # Parse before emitting: a slot operand desugars to a READSLOT that
        # must precede the instruction, not land inside it
        v = self._parse_time(arg, pos)
        self._emit(OP_SLEEP)
        self._emit_typed(v, 2)

    def _cmd_pace(self, pos):
        r"""\pace{t} — set the delay inserted after every following action."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("pace requires {value}", self._pos)
        v = self._parse_time(arg, pos)
        if v[0] == TAG_IMM:
            v = (TAG_IMM, max(0, v[1]))
        self._emit(OP_PACE)
        self._emit_typed(v, 2)

    def _cmd_set(self, pos):
        r"""\set{x}{i32|$var} — assign an i32 variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("set requires {name}", self._pos)
        reg = self._alloc_var(name.strip().lower(), pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("set requires {value}", self._pos)
        v = self._parse_value(arg, pos)  # parse first: slot desugars emit READSLOT
        self._emit(OP_SET, reg)
        self._emit_typed(v, 4)

    def _cmd_add(self, pos):
        r"""\add{x}{i32|$var} — add to an existing variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("add requires {name}", self._pos)
        name = name.strip().lower()
        if "." in name:
            raise MacroError("add: cannot modify a signal slot", pos)
        reg = self._var_reg(name, pos)
        if reg >= REG_SCRATCH:
            raise MacroError("add: cannot modify a special register", pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("add requires {value}", self._pos)
        v = self._parse_value(arg, pos)  # parse first: slot desugars emit READSLOT
        self._emit(OP_ADD, reg)
        self._emit_typed(v, 4)

    def _cmd_ifnum(self, pos):
        r"""\ifnum{$a}{op}{b}{then}[{else}] — conditional branch."""
        if self._depth >= MAX_NEST:
            raise MacroError(f"ifnum nesting exceeds {MAX_NEST} levels", pos)
        left = self._read_braces()
        if left is None or not left.strip().startswith("$"):
            raise MacroError("ifnum left operand must be a variable ($name)", pos)
        reg = self._parse_var_ref(left.strip(), pos)

        op = self._read_braces()
        if op is None:
            raise MacroError("ifnum requires {op}", self._pos)
        cc = CC_OPS.get(op.strip())
        if cc is None:
            raise MacroError("ifnum op must be one of = != < <= > >=", pos)

        rhs = self._read_braces()
        if rhs is None:
            raise MacroError("ifnum requires {value}", self._pos)
        then_body = self._read_braces()
        if then_body is None:
            raise MacroError("ifnum requires {then}", self._pos)
        else_body = self._read_braces()  # optional

        # Parse rhs before emitting: a slot operand desugars to a READSLOT
        # that must precede the branch, and must not clobber a slot-temp
        # already holding the left operand
        rhs_val = self._parse_value(rhs, pos, reserved=(reg,))
        self._emit(OP_BRA, cc, reg)
        self._emit_typed(rhs_val, 4)
        off_pos = len(self._bc)
        self._emit_u16(0)  # backpatched by _pop_nest
        self._nest_into(then_body, "ifnum then", ("ifnum_then", off_pos, else_body))

    def _cmd_val(self, pos):
        r"""\val{name} — render a variable as decimal keystrokes (text position)."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("val requires {name}", self._pos)
        self._emit(OP_TYPEREG, self._var_reg(arg.strip().lower(), pos))

    def _cmd_use(self, pos):
        r"""\use{ns} — declare a dependency on a runtime namespace (compile-time check)."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("use requires {ns}", self._pos)
        ns = arg.strip().lower()
        if self.runtime is None:
            raise MacroError("use requires a runtime registry", pos)
        if not self.runtime.has_namespace(ns):
            raise MacroError(f"Unknown namespace: {ns}", pos)

    def _cmd_call(self, pos):
        r"""\call{ns.fn}{arg}... — invoke a runtime extension function."""
        target = self._read_braces()
        if target is None:
            raise MacroError("call requires {ns.fn}", self._pos)
        target = target.strip().lower()
        parts = target.split(".")
        if len(parts) != 2 or not all(parts):
            raise MacroError(f"call target must be ns.fn: {target}", pos)
        ns, fn = parts
        if self.runtime is None:
            raise MacroError("call requires a runtime registry", pos)
        sig = self.runtime.get_signature(ns, fn)
        if sig is None:
            raise MacroError(f"Unknown ext function: {target}", pos)

        args = []
        for typ in sig:
            a = self._read_braces()
            if a is None:
                raise MacroError(f"{target} expects {len(sig)} arg(s)", self._pos)
            # reserve the registers of already-parsed args so slot desugars
            # for later args cannot clobber them
            reserved = tuple(v for tag, v in args if tag == TAG_REG)
            args.append(self._parse_ext_arg(a, typ, target, pos, reserved))
        if self._peek() == "{":
            raise MacroError(f"{target} expects {len(sig)} arg(s)", self._pos)

        self._emit(OP_CALL_EXT, self._str_idx(ns, pos), self._str_idx(fn, pos), len(args))
        for tag, v in args:
            if tag == TAG_IMM:
                self._emit(TAG_IMM)
                self._emit_i32(v)
            elif tag == TAG_REG:
                self._emit(TAG_REG, v)
            else:
                self._emit(TAG_STR, v)

    def _parse_ext_arg(self, a, typ, target, pos, reserved=()):
        """Compile one ext arg per its signature type."""
        if typ == "str":
            if a.strip().startswith("$"):
                raise MacroError(f"{target}: str args are literal (no string variables)", pos)
            return (TAG_STR, self._str_idx(a, pos))
        if typ in ("int", "color"):
            return self._parse_value(a, pos, reserved)
        # handle types (tpl, conn, ...): only a variable holding a handle
        s = a.strip()
        if not s.startswith("$"):
            raise MacroError(f"{target}: {typ} arg must be a variable ($handle)", pos)
        return (TAG_REG, self._parse_var_ref(s, pos, reserved))

    def _cmd_wait(self, pos):
        r"""\wait{signal}[{sec|$var}] — suspend until a runtime signal fires."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("wait requires {signal}", self._pos)
        sig = arg.strip().lower()
        if self.runtime is None:
            raise MacroError("wait requires a runtime registry", pos)
        if not self.runtime.has_signal(sig):
            raise MacroError(f"Unknown signal: {sig}", pos)

        t = self._read_braces()
        if t is None:
            timeout = (TAG_IMM, 0)  # 0 = wait forever
        else:
            ts = t.strip()
            if ts.startswith("$"):
                timeout = (TAG_REG, self._parse_var_ref(ts, pos))
            else:
                try:
                    timeout = (TAG_IMM, max(0, min(4294967295, int(float(ts) * 1000))))
                except ValueError:
                    raise MacroError("Invalid wait timeout", pos)

        self._emit(OP_WAIT, self._str_idx(sig, pos))
        if timeout[0] == TAG_REG:
            self._emit(TAG_REG, timeout[1])
        else:
            self._emit(TAG_IMM)
            self._emit_i32(timeout[1])

    def _cmd_rep(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("rep requires {count}", self._pos)
        count = self._parse_value(arg, pos)
        if count[0] == TAG_IMM:
            count = (TAG_IMM, max(1, min(255, count[1])))

        body = self._read_braces()
        if body is None:
            raise MacroError("rep requires {body}", self._pos)

        # B1: enforce nesting limit
        if self._depth >= MAX_NEST:
            raise MacroError(f"rep nesting exceeds {MAX_NEST} levels", pos)
        self._nest_into(body, "rep body", ("rep", count))

    def _cmd_kdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kdown requires {key}", self._pos)
        v = self._parse_key_arg(arg, pos)  # parse first: slot desugars emit READSLOT
        self._emit(OP_KEY_DOWN)
        self._emit_typed(v, 1)

    def _cmd_kup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kup requires {key} or {}", self._pos)
        arg = arg.strip().lower()
        if not arg:
            self._emit(OP_KEY_UP, TAG_IMM, 0)  # release all
        else:
            v = self._parse_key_arg(arg, pos)  # parse first (READSLOT desugar)
            self._emit(OP_KEY_UP)
            self._emit_typed(v, 1)

    def _cmd_click(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("click requires {btn} or {btn,count}", self._pos)
        parts = arg.split(",")
        btn = self._parse_btn_arg(parts[0], pos)
        count = (TAG_IMM, 1)
        if len(parts) >= 2:
            # reserve the btn register so a slot-derived count cannot clobber it
            count = self._parse_value(
                parts[1], pos, reserved=(btn[1],) if btn[0] == TAG_REG else ())
            if count[0] == TAG_IMM:
                count = (TAG_IMM, max(1, min(65535, count[1])))
        self._emit(OP_MCLICK)
        self._emit_typed(btn, 1)
        self._emit_typed(count, 2)

    def _cmd_move(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("move requires {dx,dy}", self._pos)
        parts = arg.split(",")
        dx = self._parse_value(parts[0], pos)
        dy = (TAG_IMM, 0)
        if len(parts) >= 2:
            # reserve the dx register so a slot-derived dy cannot clobber it
            dy = self._parse_value(
                parts[1], pos, reserved=(dx[1],) if dx[0] == TAG_REG else ())

        if dx[0] == TAG_IMM and dy[0] == TAG_IMM:
            # Immediate moves: emit in chunks of 32767
            vx, vy = dx[1], dy[1]
            while vx != 0 or vy != 0:
                mx = max(-32767, min(32767, vx))
                my = max(-32767, min(32767, vy))
                self._emit(OP_MMOVE)
                self._emit_typed((TAG_IMM, mx), 2)
                self._emit_typed((TAG_IMM, my), 2)
                vx -= mx
                vy -= my
        else:
            # Register moves: a single op, VM reads at exec time
            self._emit(OP_MMOVE)
            self._emit_typed(dx, 2)
            self._emit_typed(dy, 2)

    def _cmd_mdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("mdown requires {btn}", self._pos)
        self._emit(OP_MDOWN)
        self._emit_typed(self._parse_btn_arg(arg, pos), 1)

    def _cmd_mup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("mup requires {btn}", self._pos)
        self._emit(OP_MUP)
        self._emit_typed(self._parse_btn_arg(arg, pos), 1)

    def _parse(self):
        """Main parse driver — iterative nesting via _nest_stack (heap frames),
        no Python recursion, so nesting depth cannot exhaust the tiny device
        pystack (depth is bounded by bytecode size, not call frames)."""
        while True:
            self._parse_level()
            if not self._nest_stack:
                return
            self._pop_nest()

    def _parse_level(self):
        """Parse the current self._text until exhausted (one nesting level)."""
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
                if next_ch in "\\{}$":
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
        strs, ip = self._read_header(bc)
        delay_ms = default_delay_ms
        loop_stack = []  # [(ip_start, remaining, saved_delay)]
        regs = [0] * 256
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

        def read_i32():
            nonlocal ip
            v = bc[ip] | (bc[ip + 1] << 8) | (bc[ip + 2] << 16) | (bc[ip + 3] << 24)
            ip += 4
            return v - 4294967296 if v > 2147483647 else v

        def read_arg(width, signed=False):
            """Typed operand: TAG_REG reads a register, TAG_IMM reads inline."""
            if read_u8() == TAG_REG:
                return regs[read_u8()]
            if width == 1:
                return read_u8()
            if width == 2:
                return read_i16() if signed else read_u16()
            return read_i32()

        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

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
                    await sleep_ms(clamp(read_arg(2), 0, 65535))

                elif op == OP_PACE:
                    # Set inter-action delay for following ops (DSL \pace)
                    delay_ms = clamp(read_arg(2), 0, 65535)

                elif op == OP_MCLICK:
                    btn = read_arg(1) & 0xFF
                    count = clamp(read_arg(2), 0, 65535)
                    for i in range(count):
                        hid.mouse_press(btn)
                        await sleep_ms(click_hold_ms)
                        hid.mouse_release(btn)
                        if i < count - 1:
                            await sleep_ms(delay_ms)
                    await sleep_ms(delay_ms)

                elif op == OP_MMOVE:
                    dx = read_arg(2, signed=True)
                    dy = read_arg(2, signed=True)
                    hid.mouse_move(dx, dy)
                    await sleep_ms(delay_ms)

                elif op == OP_LOOP:
                    count = clamp(read_arg(1), 0, 255)
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
                    hid.key_press(read_arg(1) & 0xFF)

                elif op == OP_KEY_UP:
                    key = read_arg(1) & 0xFF
                    if key == 0:
                        hid.keys_release_all()
                    else:
                        hid.key_release(key)

                elif op == OP_MDOWN:
                    hid.mouse_press(read_arg(1) & 0xFF)

                elif op == OP_MUP:
                    hid.mouse_release(read_arg(1) & 0xFF)

                elif op == OP_SET:
                    reg = read_u8()
                    regs[reg] = read_arg(4)

                elif op == OP_ADD:
                    reg = read_u8()
                    regs[reg] = regs[reg] + read_arg(4)
                    # keep registers in signed i32 range
                    if regs[reg] > 2147483647:
                        regs[reg] -= 4294967296
                    elif regs[reg] < -2147483648:
                        regs[reg] += 4294967296

                elif op == OP_BRA:
                    cc = read_u8()
                    a = regs[read_u8()]
                    rhs = read_arg(4)
                    off = read_u16()
                    if cc == 0:
                        cond = a == rhs
                    elif cc == 1:
                        cond = a != rhs
                    elif cc == 2:
                        cond = a < rhs
                    elif cc == 3:
                        cond = a <= rhs
                    elif cc == 4:
                        cond = a > rhs
                    else:
                        cond = a >= rhs
                    if not cond:
                        ip += off

                elif op == OP_JMP:
                    off = read_u16()
                    ip += off

                elif op == OP_TYPEREG:
                    # Render register as decimal keystrokes (DSL \val);
                    # the whole render is one pacing action
                    for ch in str(regs[read_u8()]):
                        codes = hid.char_keycodes(ch)
                        if codes:
                            for kc in codes:
                                hid.key_press(kc)
                            await sleep_ms(click_hold_ms)
                            hid.keys_release_all()
                    await sleep_ms(delay_ms)

                elif op == OP_READSLOT:
                    reg = read_u8()
                    sig = strs[read_u8()]
                    field = strs[read_u8()]
                    if self.runtime is None:
                        raise RuntimeError("READSLOT without runtime registry")
                    regs[reg] = self.runtime.read_slot(sig, field)

                elif op == OP_WAIT:
                    sig = strs[read_u8()]
                    timeout = read_arg(4)
                    if self.runtime is None:
                        raise RuntimeError("WAIT without runtime registry")
                    ok = await self.runtime.wait_signal(sig, clamp(timeout, 0, 4294967295))
                    regs[REG_TIMEOUT] = 0 if ok else 1

                elif op == OP_CALL_EXT:
                    ns = strs[read_u8()]
                    fn = strs[read_u8()]
                    argc = read_u8()
                    args = []
                    for _ in range(argc):
                        tag = read_u8()
                        if tag == TAG_IMM:
                            args.append(read_i32())
                        elif tag == TAG_REG:
                            args.append(regs[read_u8()])
                        else:
                            args.append(strs[read_u8()])
                    if self.runtime is None:
                        raise RuntimeError("CALL_EXT without runtime registry")
                    r = self.runtime.call(ns, fn, args)
                    if hasattr(r, "send"):  # coroutine: await it
                        r = await r
                    if r is None:
                        regs[REG_RET] = 0
                    else:
                        try:
                            regs[REG_RET] = int(r)
                        except (TypeError, ValueError):
                            regs[REG_RET] = 0

                else:
                    raise RuntimeError(f"Unknown opcode 0x{op:02X}")

            return True
        finally:
            # B2: never leave keys/buttons pressed, whatever the exit path
            hid.keys_release_all()
            hid.mouse_release(BTN_ALL)
