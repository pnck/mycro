# Macro DSL Compiler for MYCRO.
# Normative reference: spec/dsl.md (grammar, syntax lowering, type environment)

import keymap
from keymap import (
    ENTER, TAB, MINUS, EQUALS, SHIFT,
    letter_keycode, digit_keycode,
)
from bytecode import (
    MAGIC, VERSION,
    OP_CHAR, OP_KEY, OP_SLEEP, OP_MCLICK, OP_MMOVE, OP_PACE,
    OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP, OP_MDOWN, OP_MUP,
    OP_SET, OP_ADD, OP_BRA, OP_JMP, OP_SUB, OP_READSLOT, OP_WAIT,
    OP_CALL_EXT, OP_TYPEREG, OP_END,
    TAG_IMM, TAG_REG, TAG_STR,
    MAX_REGS, RECT_MAX_EDGE, REG_ERR, REG_SCRATCH, REG_TIMEOUT, REG_RET,
    CC_OPS, MAX_BYTECODE, MAX_RUNTIME_NEST, MAX_MACRO_EXPAND,
    MAX_COMBO_KEYS, DEFAULT_CLICK_HOLD_MS, _PRINTABLE_LONE_KEYS,
    _is_alpha, _is_alnum,
    KEYS, MODS, MOUSE_BTNS,
    MacroError,
)


class Compiler:
    KEYS = KEYS
    MODS = MODS
    MOUSE_BTNS = MOUSE_BTNS
    def __init__(self, runtime=None, click_hold_ms=DEFAULT_CLICK_HOLD_MS):
        self.runtime = runtime
        self.click_hold_ms = click_hold_ms
        self._emit_newline = True
        self._emit_space = True
        self._first_executable_pos = 0
    def compile(self, text, config=None, click_hold_ms=None):
        """Compile macro text to bytecode. Returns (bytecode, error_msg).

        Nesting (rep/ifnum/macro bodies) is parsed iteratively on an explicit
        heap stack (self._nest_stack), NOT by Python recursion: the device
        pystack only survives ~2 levels of the old recursive compile chain
        (each level cost 5 fat frames: compile/_parse/_parse_command/
        _cmd_*/_compile_sub) once the asyncio+httpserver baseline is on it.
        """
        if config and isinstance(config, dict) and "click_hold_ms" in config:
            self._click_hold_ms = config["click_hold_ms"]
        elif click_hold_ms is not None:
            self._click_hold_ms = click_hold_ms
        else:
            self._click_hold_ms = getattr(self, "click_hold_ms", DEFAULT_CLICK_HOLD_MS)
        self._bc = bytearray()
        self._text = text
        self._pos = 0
        self._len = len(text)
        self._runtime_depth = 0
        self._macro_depth = 0
        self._defs = {}
        self._chain = ()
        self._vars = {}
        self._types = {}       # type environment: name -> type (spec/vm.md Part II §2)
        self._ret_type = "int"  # type of $ret at this point (last \call's return)
        self._strs = []
        self._strmap = {}
        self._nest_stack = []
        self._emit_newline = True
        self._emit_space = True
        self._first_executable_pos = 0
        if self.runtime is not None:
            self.runtime.clear_consts()  # const resources belong to one compilation

        try:
            self._parse_preamble()
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
                msg = "In {}: {}".format(fr[-2], msg)
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

    def _resolve_combo_key(self, token, pos):
        if token.startswith("$"):
            raise MacroError("kdown/kup takes a key name, not a variable", pos)
        if len(token) == 1:
            if "a" <= token <= "z":
                return [letter_keycode(token)]
            if "A" <= token <= "Z":
                return [SHIFT, letter_keycode(token)]
            if "0" <= token <= "9":
                return [digit_keycode(token)]
            if token == "-":
                return [MINUS]
            if token == "=":
                return [EQUALS]
            raise MacroError(f"Unknown key: {token}", pos)
        low = token.lower()
        if low == "plus":
            return [SHIFT, EQUALS]
        if low in self.KEYS:
            return [self.KEYS[low]]
        raise MacroError(f"Unknown key: {token}", pos)

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

    def _emit_typed(self, val, width=4):
        """val = (TAG_IMM, int) or (TAG_REG, reg).

        In v4, all TAG_IMM typed operands are physically 4-byte signed i32.
        """
        tag, v = val
        if tag == TAG_REG:
            self._emit(TAG_REG, v)
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
        """Read alphanumeric command name, preserving raw case."""
        start = self._pos
        while self._pos < self._len:
            ch = self._text[self._pos]
            if _is_alnum(ch) or ch == "_":
                self._pos += 1
            else:
                break
        return self._text[start : self._pos]

    def _apply_preamble_directive(self, raw_line, pos):
        """Parse and apply a preamble directive line like \\#!syntax: free."""
        line = raw_line.rstrip("\r\n")
        if len(line) > 3:
            cmt_idx = line[3:].find(r"\#")
            if cmt_idx != -1:
                line = line[: 3 + cmt_idx]

        content = line[3:].lstrip(" \t")
        if not content.startswith("syntax:"):
            raise MacroError(f"Unknown preamble directive: {line.strip()}", pos)

        cfg_str = content[len("syntax:") :].strip()
        if not cfg_str:
            raise MacroError("Invalid syntax configuration", pos)

        if cfg_str == "free":
            self._emit_newline = False
            self._emit_space = False
        elif cfg_str == "typewriter":
            self._emit_newline = True
            self._emit_space = True
        else:
            items = cfg_str.split(",")
            for raw_item in items:
                item = raw_item.strip()
                if not item:
                    raise MacroError("Invalid syntax configuration", pos)
                if "=" not in item:
                    raise MacroError(f"Unknown layout preset: {item}", pos)
                k, v = item.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k not in ("newline", "space"):
                    raise MacroError(f"Unknown syntax config key: {k}", pos)
                if v not in ("ignore", "emit"):
                    raise MacroError(f"Unknown syntax config value: {v}", pos)
                if k == "newline":
                    self._emit_newline = v == "emit"
                elif k == "space":
                    self._emit_space = v == "emit"

    def _parse_preamble(self):
        """Scan leading comment-and-whitespace region for preamble directives."""
        self._emit_newline = True
        self._emit_space = True
        pos = 0
        text = self._text
        n = self._len

        while pos < n:
            ch = text[pos]
            if ch in " \t\r\n":
                pos += 1
                continue
            if ch == "\\" and pos + 1 < n and text[pos + 1] in "\r\n":
                # Line continuation: backslash + newline
                pos += 2
                if text[pos - 1] == "\r" and pos < n and text[pos] == "\n":
                    pos += 1
                continue
            if ch == "\\" and pos + 1 < n and text[pos + 1] == "#":
                line_start = pos
                is_directive = pos + 2 < n and text[pos + 2] == "!"
                pos += 2
                while pos < n and text[pos] not in "\r\n":
                    pos += 1
                line_end = pos
                if pos < n and text[pos] == "\r":
                    pos += 1
                if pos < n and text[pos] == "\n":
                    pos += 1

                if is_directive:
                    self._apply_preamble_directive(text[line_start:line_end], line_start)
                continue

            # First executable token reached!
            break

        self._first_executable_pos = pos

    def _skip_absorbed_whitespace(self):
        """In free mode (or fine-grained ignore), skip layout-absorbed whitespace and comments."""
        while self._pos < self._len:
            ch = self._peek()
            if ch in " \t" and not self._emit_space:
                self._advance()
            elif ch in "\r\n" and not self._emit_newline:
                self._advance()
            elif (
                ch == "\\"
                and self._pos + 1 < self._len
                and self._text[self._pos + 1] in "\r\n"
            ):
                self._advance()
                if self._peek() == "\r":
                    self._advance()
                if self._peek() == "\n":
                    self._advance()
            elif (
                ch == "\\"
                and self._pos + 1 < self._len
                and self._text[self._pos + 1] == "#"
            ):
                self._pos += 2
                while self._pos < self._len and self._text[self._pos] not in "\r\n":
                    self._pos += 1
                if self._peek() == "\r":
                    self._advance()
                if self._peek() == "\n":
                    self._advance()
            else:
                break

    def _read_braces(self):
        r"""Read content between { and }. Escaped braces/backslashes (\{ \} \\)
        are not counted toward nesting depth."""
        self._skip_absorbed_whitespace()
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
        tokens = [name]
        pos = self._pos

        # Handle + chains: \ctrl+\shift+c, \a+b, \ctrl+c
        while self._peek() == "+":
            self._advance()  # skip +
            if self._peek() == "\\":
                self._advance()  # skip \
                next_name = self._read_name()
            elif self._peek() and (_is_alnum(self._peek()) or self._peek() == "_"):
                next_name = self._read_name()
            elif self._peek() in ("-", "="):
                next_name = self._advance()
            else:
                raise MacroError("Expected key after +", self._pos)
            tokens.append(next_name)

        # Skip empty {} separator
        if (
            self._peek() == "{"
            and self._pos + 1 < self._len
            and self._text[self._pos + 1] == "}"
        ):
            self._pos += 2

        if len(tokens) > MAX_COMBO_KEYS:
            raise MacroError(
                f"key combination exceeds MAX_COMBO_KEYS={MAX_COMBO_KEYS}", pos
            )

        if len(tokens) == 1:
            tok = tokens[0]
            if len(tok) == 1:
                if ("a" <= tok <= "z") or ("A" <= tok <= "Z") or ("0" <= tok <= "9"):
                    self._emit(OP_CHAR, ord(tok))
                    return
            low = tok.lower()
            if low in _PRINTABLE_LONE_KEYS:
                self._emit(OP_CHAR, _PRINTABLE_LONE_KEYS[low])
                return
            if low in self.KEYS:
                self._emit(OP_KEY, self.KEYS[low])
                return
            raise MacroError(f"Unknown key: {tok}", pos)

        # Combination lowering: forward KEY_DOWN, SLEEP click_hold_ms, reverse KEY_UP
        K = []
        for tok in tokens:
            K.extend(self._resolve_combo_key(tok, pos))

        for kc in K:
            self._emit(OP_KEY_DOWN)
            self._emit_typed((TAG_IMM, kc))

        self._emit(OP_SLEEP)
        self._emit_typed((TAG_IMM, self._click_hold_ms))

        for kc in reversed(K):
            self._emit(OP_KEY_UP)
            self._emit_typed((TAG_IMM, kc))

    def _parse_command(self):
        """Parse command after backslash."""
        pos = self._pos
        name = self._read_name()

        if not name:
            raise MacroError("Expected command name", pos)

        name_lower = name.lower()
        handler = getattr(self, "_cmd_" + name_lower, None)
        if handler:
            handler(pos)
            return

        if name_lower == "rect":
            raise MacroError(
                "\\rect{...} is a value-position literal (valid inside "
                "\\set{h}{...} or a handle argument)", pos)

        # User-defined macro: \name{arg}... (compile-time expansion)
        if name_lower in self._defs:
            self._expand_macro(name_lower, pos)
            return

        # Otherwise it's a key or combo: \enter, \ctrl+c, \a, \A
        self._parse_key_or_combo(name)

    def _cmd_def(self, pos):
        r"""\def{name}[argc]{body} — define a compile-time user macro (top level only)."""
        if self._nest_stack:
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
        if (
            getattr(self, "_cmd_" + name, None)
            or name in self.KEYS
            or name in self.MODS
            or name in _PRINTABLE_LONE_KEYS
        ):
            raise MacroError(f"macro name conflicts with builtin: {name}", pos)

        self._skip_absorbed_whitespace()
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
        if r"\#!" in body:
            idx = body.find(r"\#!")
            raise MacroError("directive in executable code", pos + idx)
        self._defs[name] = (argc, body)

    def _nest_into(self, body, what, cont):
        """Swap the parser into a nested body. Parent state goes onto the
        explicit heap stack; `what` labels the body for error messages and
        `cont` tells _pop_nest how to wrap the finished sub-buffer.
        Callers do their own depth check first (messages differ)."""
        self._nest_stack.append(
            (self._text, self._pos, self._len, self._bc,
             self._runtime_depth, self._macro_depth, self._chain, what, cont)
        )
        self._text, self._pos, self._len = body, 0, len(body)
        self._bc = bytearray()
        if cont[0] == "inline":
            self._macro_depth += 1
        else:
            self._runtime_depth += 1

    def _pop_nest(self):
        """A nested body is finished: restore the parent frame and wrap the
        sub-buffer per the continuation."""
        sub = self._bc
        (self._text, self._pos, self._len, self._bc,
         self._runtime_depth, self._macro_depth, self._chain, what, cont) = self._nest_stack.pop()
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
        if self._macro_depth >= MAX_MACRO_EXPAND:
            raise MacroError(f"macro expansion exceeds {MAX_MACRO_EXPAND} levels", pos)
        argc, body = self._defs[name]
        args = []
        if argc == 0:
            if (
                self._peek() == "{"
                and self._pos + 1 < self._len
                and self._text[self._pos + 1] == "}"
            ):
                self._pos += 2
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
        if name == "err":
            return REG_ERR
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
        if name in ("ret", "timeout", "err"):
            raise MacroError(f"Reserved variable name: {name}", pos)
        if name == "slot":
            raise MacroError("Reserved variable name: slot", pos)
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
            self._types[name] = "int"
        return self._vars[name]

    def _reg_type(self, reg):
        """Recorded semantic type of a register's current content
        (type environment, spec/vm.md Part II §2)."""
        if reg == REG_RET:
            return self._ret_type
        if reg == REG_TIMEOUT or reg == REG_ERR:
            return "int"
        for name, r in self._vars.items():
            if r == reg:
                return self._types.get(name, "int")
        return "int"  # transient slot-temp registers hold ints

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

    def _parse_btn_arg(self, s, pos):
        """Mouse button: name (L, R, M, B, F)."""
        s = s.strip().lower()
        if s.startswith("$"):
            raise MacroError("click/mdown/mup takes a button name, not a variable", pos)
        if s in self.MOUSE_BTNS:
            return self.MOUSE_BTNS[s]
        raise MacroError(f"Unknown mouse button: {s}", pos)

    def _parse_key_arg(self, s, pos):
        """Key: name (page 0x07 key name)."""
        s = s.strip().lower()
        if s.startswith("$"):
            raise MacroError("kdown/kup takes a key name, not a variable", pos)
        return self._resolve_key(s, pos)

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

    def _parse_rect_literal(self, t, pos):
        r"""Parse \rect{\row{0xRRGGBB,...},...} -> (w, h, rgb bytes).

        Compile-time pixel constant: the data goes to the middleware's
        const store, never into the bytecode (spec/dsl.md §3)."""
        i = t.find("{")
        if i < 0 or not t.endswith("}"):
            raise MacroError("rect requires {\\row{...},...}", pos)
        rows = []
        for part in t[i + 1 : -1].split("\\row"):
            part = part.strip()
            if not part:
                continue
            part = part.rstrip(",").strip()
            if not part.startswith("{") or not part.endswith("}"):
                raise MacroError(r"rect: rows must be \row{...}", pos)
            row = []
            for tok in part[1:-1].split(","):
                tok = tok.strip()
                if not tok:
                    continue
                v = self._parse_int_literal(tok, pos)
                if not 0 <= v <= 0xFFFFFF:
                    raise MacroError("rect: color out of 0xRRGGBB range", pos)
                row.append(v)
            rows.append(row)
        if not rows or not rows[0]:
            raise MacroError("rect: empty literal", pos)
        w = len(rows[0])
        h = len(rows)
        for r in rows:
            if len(r) != w:
                raise MacroError("rect: ragged rows", pos)
        if w > RECT_MAX_EDGE or h > RECT_MAX_EDGE:
            raise MacroError(f"rect: edge capped at {RECT_MAX_EDGE}", pos)
        data = bytearray()
        for r in rows:
            for v in r:
                data += bytes((v >> 16, (v >> 8) & 0xFF, v & 0xFF))
        return w, h, bytes(data)

    def _try_rect_literal(self, s, pos, reserved=()):
        r"""If s is a \rect{...} literal: register its pixels in the
        middleware const store and emit CALL_EXT sys.const <cid> + SET into
        a temp register (the ownership then flows through the normal
        claim/move machinery). Returns (TAG_REG, reg) or None."""
        t = s.strip()
        if not t.startswith("\\rect"):
            return None
        if self.runtime is None:
            raise MacroError("rect literal requires a runtime registry", pos)
        w, h, data = self._parse_rect_literal(t, pos)
        try:
            cid = self.runtime.add_const("snap", {"w": w, "h": h, "rgb": data})
        except ValueError as e:
            raise MacroError("rect: {}".format(e), pos)
        reg = self._alloc_temp(reserved, pos)
        self._emit(OP_CALL_EXT, self._str_idx("sys", pos),
                   self._str_idx("const", pos), 1)
        self._emit(TAG_IMM)
        self._emit_i32(cid)
        self._emit(OP_SET, reg)
        self._emit(TAG_REG, REG_RET)
        return TAG_REG, reg

    def _cmd_set(self, pos):
        r"""\set{x}{i32|$var} — assign an i32 variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("set requires {name}", self._pos)
        name = name.strip().lower()
        reg = self._alloc_var(name, pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("set requires {value}", self._pos)
        rect = self._try_rect_literal(arg, pos)
        if rect is not None:
            # \set{h}{\rect{...}}: emissions already precede; just land it
            self._emit(OP_SET, reg)
            self._emit(TAG_REG, rect[1])
            self._types[name] = "snap"
            return
        v = self._parse_value(arg, pos)  # parse first: slot desugars emit READSLOT
        self._emit(OP_SET, reg)
        self._emit_typed(v, 4)
        # type environment: copy the source's type (no computation);
        # handle types MOVE — the source reverts to int, mirroring the
        # runtime's ownership move (spec/vm.md Part II §2/§4)
        if v[0] == TAG_REG:
            t = self._reg_type(v[1])
            if t in ("int", "color"):
                self._types[name] = t
            else:
                self._types[name] = t
                if v[1] == REG_RET:
                    self._ret_type = "int"
                else:
                    for nm, r in self._vars.items():
                        if r == v[1]:
                            self._types[nm] = "int"
                            break
        else:
            self._types[name] = "int"

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
        if self._reg_type(reg) != "int":
            raise MacroError(
                f"add: cannot modify a {self._reg_type(reg)} handle", pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("add requires {value}", self._pos)
        v = self._parse_value(arg, pos)  # parse first: slot desugars emit READSLOT
        self._emit(OP_ADD, reg)
        self._emit_typed(v, 4)

    def _cmd_sub(self, pos):
        r"""\sub{x}{i32|$var} — subtract from an existing variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("sub requires {name}", self._pos)
        name = name.strip().lower()
        if "." in name:
            raise MacroError("sub: cannot modify a signal slot", pos)
        reg = self._var_reg(name, pos)
        if reg >= REG_SCRATCH:
            raise MacroError("sub: cannot modify a special register", pos)
        if self._reg_type(reg) != "int":
            raise MacroError(
                f"sub: cannot modify a {self._reg_type(reg)} handle", pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("sub requires {value}", self._pos)
        v = self._parse_value(arg, pos)  # parse first: slot desugars emit READSLOT
        self._emit(OP_SUB, reg)
        self._emit_typed(v, 4)

    def _cmd_ifnum(self, pos):
        r"""\ifnum{$a}{op}{b}{then}[{else}] — conditional branch."""
        if self._runtime_depth >= MAX_RUNTIME_NEST:
            raise MacroError(f"ifnum nesting exceeds {MAX_RUNTIME_NEST} levels", pos)
        left = self._read_braces()
        if left is None or not left.strip().startswith("$"):
            raise MacroError("ifnum left operand must be a variable ($name)", pos)
        reg = self._parse_var_ref(left.strip(), pos)
        lt = self._reg_type(reg)
        # $ret is exempt: it carries error sentinels alongside results,
        # so comparing it is the mandatory failure check (spec/vm.md)
        if reg != REG_RET and lt not in ("int", "color"):
            raise MacroError(f"ifnum: cannot compare a {lt} handle", pos)

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
        if rhs_val[0] == TAG_REG and rhs_val[1] != REG_RET \
                and self._reg_type(rhs_val[1]) not in ("int", "color"):
            raise MacroError(
                f"ifnum: cannot compare a {self._reg_type(rhs_val[1])} handle", pos)
        self._emit(OP_BRA, cc, reg)
        self._emit_typed(rhs_val, 4)
        off_pos = len(self._bc)
        self._emit_u16(0)  # backpatched by _pop_nest
        self._nest_into(then_body, "ifnum then", ("ifnum_then", off_pos, else_body))

    def _cmd_typenum(self, pos):
        r"""\typenum{$var} — render a numeric register as decimal keystrokes (text position)."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("typenum requires {$var}", self._pos)
        s = arg.strip()
        if not s.startswith("$"):
            raise MacroError("typenum requires a variable ($name)", pos)
        reg = self._parse_var_ref(s, pos)
        vt = self._reg_type(reg)
        if reg != REG_RET and vt not in ("int", "color"):
            raise MacroError(f"typenum: cannot render a {vt} handle", pos)
        self._emit(OP_TYPEREG, reg)

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
        ret_type = self.runtime.get_return_type(ns, fn)

        args = []
        for typ in sig:
            a = self._read_braces()
            if a is None:
                raise MacroError(f"{target} expects {len(sig)} arg(s)", self._pos)
            # reserve the registers of already-parsed args so slot desugars
            # for later args cannot clobber them
            reserved = tuple(v for tag, v in args if tag == TAG_REG)
            parsed = self._parse_ext_arg(a, typ, target, pos, reserved)
            if isinstance(parsed, list):  # point/rect expand to N operands
                args.extend(parsed)
            else:
                args.append(parsed)
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
        self._ret_type = ret_type  # $ret's type flows from the call site

    @staticmethod
    def _unescape_str(s):
        r"""str literal args: the brace-matching escapes (\{ \} \\) become
        the literal characters (single pass)."""
        out = []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in "{}\\":
                out.append(s[i + 1])
                i += 2
            else:
                out.append(s[i])
                i += 1
        return "".join(out)

    def _parse_ext_arg(self, a, typ, target, pos, reserved=()):
        r"""Compile one ext arg per its signature type. point/rect return a
        LIST of typed operands (compound value in one brace group, e.g.
        {x,y} / {x,y,w,h} — the \move{dx,dy} idiom); all other types
        return a single (tag, value) pair."""
        if typ in ("point", "rect"):
            n = 2 if typ == "point" else 4
            parts = [p.strip() for p in a.split(",")]
            if len(parts) != n or not all(parts):
                raise MacroError(
                    f"{target}: {typ} expects {n} comma-separated values", pos)
            out = []
            acc = tuple(reserved)
            for p in parts:
                t, v = self._parse_value(p, pos, acc)
                if t == TAG_REG:
                    acc = acc + (v,)  # don't clobber a sibling's slot-temp
                out.append((t, v))
            return out
        if typ == "str":
            s = self._unescape_str(a.strip())
            if s.startswith("$"):
                raise MacroError(f"{target}: str args are literal (no string variables)", pos)
            return (TAG_STR, self._str_idx(s, pos))
        if typ in ("int", "color"):
            return self._parse_value(a, pos, reserved)
        # handle types (snap, tpl, ...): a \rect{...} literal desugars to a
        # fresh const handle, or a variable whose recorded type matches —
        # compile-time environment check (spec/vm.md)
        s = a.strip()
        if s.startswith("\\rect"):
            if typ != "snap":
                raise MacroError(f"{target}: \\rect literal is a snap handle", pos)
            return self._try_rect_literal(s, pos, reserved)
        if not s.startswith("$"):
            raise MacroError(f"{target}: {typ} arg must be a variable ($handle)", pos)
        reg = self._parse_var_ref(s, pos, reserved)
        vt = self._reg_type(reg)
        if vt != typ:
            raise MacroError(
                f"{target}: arg must hold a {typ} handle (got {vt})", pos)
        return (TAG_REG, reg)

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
            if not (0 <= count[1] <= 255):
                raise MacroError("rep count out of u8 range", pos)
            count = (TAG_IMM, count[1])

        body = self._read_braces()
        if body is None:
            raise MacroError("rep requires {body}", self._pos)

        # B1: enforce nesting limit
        if self._runtime_depth >= MAX_RUNTIME_NEST:
            raise MacroError(f"rep nesting exceeds {MAX_RUNTIME_NEST} levels", pos)
        self._nest_into(body, "rep body", ("rep", count))

    def _cmd_text(self, pos):
        """Verbatim text block: \\text{} payload \\endtext{}"""
        if (
            self._peek() != "{"
            or self._pos + 1 >= self._len
            or self._text[self._pos + 1] != "}"
        ):
            raise MacroError(r"\text requires {}", pos)
        self._pos += 2  # skip {}

        payload_start = self._pos
        term = self._text.find(r"\endtext{}", payload_start)
        if term == -1:
            raise MacroError("Unterminated text block", pos)

        i = payload_start
        text = self._text
        while i < term:
            ch = text[i]
            if ch == "\r":
                if i + 1 < term and text[i + 1] == "\n":
                    self._emit(OP_KEY, ENTER)
                    i += 2
                    continue
                else:
                    self._emit(OP_KEY, ENTER)
                    i += 1
                    continue
            elif ch == "\n":
                self._emit(OP_KEY, ENTER)
                i += 1
                continue
            elif ch == "\t":
                self._emit(OP_KEY, TAB)
                i += 1
                continue
            elif 32 <= ord(ch) <= 126:
                self._emit(OP_CHAR, ord(ch))
                i += 1
                continue
            else:
                raise MacroError(f"Invalid char: 0x{ord(ch):02X}", i)

        self._pos = term + len(r"\endtext{}")

    def _cmd_endtext(self, pos):
        """Mismatched \\endtext{} outside text block."""
        if (
            self._peek() != "{"
            or self._pos + 1 >= self._len
            or self._text[self._pos + 1] != "}"
        ):
            raise MacroError(r"\endtext requires {}", pos)
        raise MacroError("endtext without text block", pos)

    def _cmd_kdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kdown requires {key}", self._pos)
        arg = arg.strip()
        if not arg:
            raise MacroError("kdown requires {key}", pos)
        if arg.startswith("$"):
            raise MacroError("kdown/kup takes a key name, not a variable", pos)
        if arg == "0":
            self._emit(OP_KEY_DOWN)
            self._emit_typed((TAG_IMM, 0))
            return
        tokens = [t.strip() for t in arg.split("+")]
        if any(not t for t in tokens):
            raise MacroError("Expected key after +", pos)
        if len(tokens) > MAX_COMBO_KEYS:
            raise MacroError(
                f"key combination exceeds MAX_COMBO_KEYS={MAX_COMBO_KEYS}", pos
            )
        K = []
        for t in tokens:
            if t.startswith("\\"):
                t = t[1:].strip()
            K.extend(self._resolve_combo_key(t, pos))
        for kc in K:
            self._emit(OP_KEY_DOWN)
            self._emit_typed((TAG_IMM, kc))

    def _cmd_kup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kup requires {key} or {}", self._pos)
        arg = arg.strip()
        if not arg or arg == "0":
            self._emit(OP_KEY_UP)
            self._emit_typed((TAG_IMM, 0))  # release all
            return
        if arg.startswith("$"):
            raise MacroError("kdown/kup takes a key name, not a variable", pos)
        tokens = [t.strip() for t in arg.split("+")]
        if any(not t for t in tokens):
            raise MacroError("Expected key after +", pos)
        if len(tokens) > MAX_COMBO_KEYS:
            raise MacroError(
                f"key combination exceeds MAX_COMBO_KEYS={MAX_COMBO_KEYS}", pos
            )
        K = []
        for t in tokens:
            if t.startswith("\\"):
                t = t[1:].strip()
            K.extend(self._resolve_combo_key(t, pos))
        for kc in reversed(K):
            self._emit(OP_KEY_UP)
            self._emit_typed((TAG_IMM, kc))

    def _cmd_click(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("click requires {btn} or {btn,count}", self._pos)
        parts = arg.split(",")
        btn = self._parse_btn_arg(parts[0], pos)
        count = (TAG_IMM, 1)
        if len(parts) >= 2:
            count = self._parse_value(parts[1], pos)
            if count[0] == TAG_IMM:
                count = (TAG_IMM, max(1, min(65535, count[1])))
        self._emit(OP_MCLICK)
        self._emit_typed((TAG_IMM, btn))
        self._emit_typed(count)

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
                self._emit_typed((TAG_IMM, mx))
                self._emit_typed((TAG_IMM, my))
                vx -= mx
                vy -= my
        else:
            # Register moves: a single op, VM reads at exec time
            self._emit(OP_MMOVE)
            self._emit_typed(dx)
            self._emit_typed(dy)

    def _cmd_mdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("mdown requires {btn}", self._pos)
        btn = self._parse_btn_arg(arg, pos)
        self._emit(OP_MDOWN)
        self._emit_typed((TAG_IMM, btn))

    def _cmd_mup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("mup requires {btn} or {}", self._pos)
        arg = arg.strip().lower()
        if not arg:
            self._emit(OP_MUP)
            self._emit_typed((TAG_IMM, keymap.BTN_ALL))
        else:
            btn = self._parse_btn_arg(arg, pos)
            self._emit(OP_MUP)
            self._emit_typed((TAG_IMM, btn))

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
                    pos_directive = self._pos - 1
                    self._advance()  # skip #
                    if self._peek() == "!":
                        if (
                            self._nest_stack
                            or pos_directive >= self._first_executable_pos
                        ):
                            raise MacroError(
                                "directive in executable code", pos_directive
                            )
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
                if self._emit_newline:
                    self._emit(OP_KEY, ENTER)
            elif ch == "\r":
                # Windows CR or old Mac - check if followed by \n
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "\n":
                    # Windows \r\n - emit ENTER and skip both chars
                    if self._emit_newline:
                        self._emit(OP_KEY, ENTER)
                    self._advance()  # skip \r
                    self._advance()  # skip \n
                    continue
                else:
                    # Standalone \r - emit ENTER
                    if self._emit_newline:
                        self._emit(OP_KEY, ENTER)
            elif ch == "\t":
                if self._emit_space:
                    self._emit(OP_KEY, TAB)
            elif ch == " ":
                if self._emit_space:
                    self._emit(OP_CHAR, 0x20)
            elif 33 <= ord(ch) <= 126:
                self._emit(OP_CHAR, ord(ch))
            else:
                raise MacroError(f"Invalid char: 0x{ord(ch):02X}", self._pos)

            self._advance()

