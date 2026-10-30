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
    OP_SET, OP_ADD, OP_BRA, OP_JMP, OP_SUB, OP_GUARD_ENTER, OP_GUARD_EXIT,
    OP_READSLOT, OP_WAIT,
    OP_CALL_EXT, OP_TYPEREG, OP_END,
    TAG_IMM, TAG_REG, TAG_STR,
    MAX_REGS, RECT_MAX_EDGE, REG_ERR, REG_SCRATCH, REG_TIMEOUT, REG_RET,
    CC_OPS, CONVERSE_CC, INVERT_CC,
    MAX_BYTECODE, MAX_RUNTIME_NEST, MAX_MACRO_EXPAND, MAX_PRED_NEST, MAX_GUARD_NEST,
    MAX_COMBO_KEYS, MAX_SLOTS, DEFAULT_WAIT_TIMEOUT_MS, DEFAULT_CLICK_HOLD_MS, _PRINTABLE_LONE_KEYS,
    GUARD_TAG_IMM, GUARD_TAG_REG, GUARD_TAG_SLOT,
    _is_alpha, _is_alnum,
    KEYS, MODS, MOUSE_BTNS,
    MacroError,
)


class Label:
    """Forward branch label with backpatching fixup list."""

    def __init__(self, name=""):
        self.name = name
        self.target = None
        self.fixups = []

    def mark(self, compiler):
        """Mark this label at the current bytecode position and resolve all pending fixups."""
        pos = len(compiler._bc)
        self.target = pos
        for patch_pos in self.fixups:
            off = pos - (patch_pos + 2)
            if off < 0:
                raise MacroError("Negative branch offset", patch_pos)
            if off > 65535:
                raise MacroError("Branch offset exceeds 65535", patch_pos)
            compiler._patch_u16(patch_pos, off)
        self.fixups.clear()

    def add_fixup(self, patch_pos, compiler):
        """Add a fixup for this label. If label is already marked, patch immediately."""
        if self.target is not None:
            off = self.target - (patch_pos + 2)
            if off < 0:
                raise MacroError("Negative branch offset", patch_pos)
            if off > 65535:
                raise MacroError("Branch offset exceeds 65535", patch_pos)
            compiler._patch_u16(patch_pos, off)
        else:
            self.fixups.append(patch_pos)


class TripletNode:
    """Atomic comparison node: lhs, cc, rhs."""

    def __init__(self, lhs_str, cc, rhs_str, pos):
        self.lhs_str = lhs_str
        self.cc = cc
        self.rhs_str = rhs_str
        self.pos = pos


class LogicNode:
    """Compound logic node: 'all' or 'any' with children predicates."""

    def __init__(self, op, children, pos):
        self.op = op
        self.children = children
        self.pos = pos


def _split_compound_group(s, pos):
    """Split string by top-level commas respecting nested braces.
    Rejects trailing commas and empty elements per spec/dsl.md §6.8."""
    s = s.strip()
    if not s:
        return []
    items = []
    start = 0
    brace_depth = 0
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and s[i + 1] in "{}\\":
            i += 2
            continue
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth < 0:
                raise MacroError("Unmatched }", pos)
        elif ch == "," and brace_depth == 0:
            piece = s[start:i].strip()
            if not piece:
                raise MacroError("empty element in compound group", pos)
            items.append(piece)
            start = i + 1
        i += 1
    if brace_depth != 0:
        raise MacroError("Unmatched {", pos)
    piece = s[start:].strip()
    if not piece:
        raise MacroError("trailing comma is not allowed", pos)
    items.append(piece)
    return items


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
        self._used_namespaces = set()
        self._guard_depth = 0
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
        self._used_namespaces.clear()
        self._guard_depth = 0
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

        if name_lower in ("rect", "pixelrect", "rgb"):
            raise MacroError(
                f"\\{name_lower}{{...}} is a value-position literal (valid inside "
                "\\set{...} or an argument)", pos)

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
        elif kind == "if_then":
            false_label, else_body = cont[1], cont[2]
            self._emit(sub)
            if else_body is not None:
                end_label = Label("if_end")
                self._emit_jmp(end_label)
                false_label.mark(self)
                self._nest_into(else_body, "if else", ("if_else", end_label))
            else:
                false_label.mark(self)
        elif kind == "if_else":
            end_label = cont[1]
            self._emit(sub)
            end_label.mark(self)
        elif kind == "guarded_body":
            patch_pos, base_point, on_abort_body = cont[1], cont[2], cont[3]
            self._guard_depth -= 1
            self._emit(sub)
            self._emit(OP_GUARD_EXIT)
            if on_abort_body is not None:
                end_label = Label("guarded_end")
                self._emit_jmp(end_label)
                abort_target = len(self._bc)
                abort_off = abort_target - base_point
                if abort_off > 65535 or abort_off < 0:
                    raise MacroError("Guard abort offset out of range", patch_pos)
                self._patch_u16(patch_pos, abort_off)
                self._nest_into(on_abort_body, "guarded on_abort", ("guarded_abort", end_label))
            else:
                abort_target = len(self._bc)
                abort_off = abort_target - base_point
                if abort_off > 65535 or abort_off < 0:
                    raise MacroError("Guard abort offset out of range", patch_pos)
                self._patch_u16(patch_pos, abort_off)
        elif kind == "guarded_abort":
            end_label = cont[1]
            self._emit(sub)
            end_label.mark(self)

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
        """Value-position segment: int literal, $var/${var}, or \rgb{r,g,b}.
        Returns (TAG_IMM, v) or (TAG_REG, reg). `reserved` holds registers
        already consumed by sibling operands of the same instruction."""
        s = s.strip()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        if self._is_rgb(s):
            return (TAG_IMM, self._parse_rgb_literal(s, pos))
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
        if name.startswith("slot."):
            name = name[5:]
        if "." in name:
            # $sig.field — signal names may themselves contain dots
            # (net.msg), so split at the LAST dot
            sig, sep, field = name.rpartition(".")
            if not sep or not sig or not field:
                raise MacroError(f"Invalid slot reference: {name}", pos)
            ns = sig.split(".")[0]
            if self.runtime is not None:
                if (
                    ns not in self._used_namespaces
                    and not self.runtime.has_slot_ns(sig)
                    and not self.runtime.has_signal(sig)
                ):
                    raise MacroError(f"unknown signal namespace: {ns}", pos)
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
        raise MacroError("no free register for value operand", pos)

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

    def _is_constructor(self, s):
        s = s.strip()
        return s.startswith(r"\pixelrect") or s.startswith(r"\rect")

    def _is_rgb(self, s):
        return s.strip().startswith(r"\rgb")

    def _parse_rgb_literal(self, s, pos):
        r"""Parse \rgb{r,g,b} -> packed 24-bit integer ((r << 16) | (g << 8) | b)."""
        s = s.strip()
        if not s.startswith(r"\rgb"):
            raise MacroError(r"expected \rgb{r,g,b}", pos)
        rest = s[4:].strip()
        if not (rest.startswith("{") and rest.endswith("}")):
            raise MacroError(r"\rgb requires {r, g, b}", pos)
        inner = rest[1:-1].strip()
        parts = _split_compound_group(inner, pos)
        if len(parts) != 3:
            raise MacroError(r"\rgb requires {r, g, b}", pos)
        r = self._parse_int_literal(parts[0], pos)
        g = self._parse_int_literal(parts[1], pos)
        b = self._parse_int_literal(parts[2], pos)
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            raise MacroError("rgb component out of range [0, 255]", pos)
        return (r << 16) | (g << 8) | b

    def _parse_pixel_color(self, s, pos):
        s = s.strip()
        if self._is_rgb(s):
            val = self._parse_rgb_literal(s, pos)
        else:
            val = self._parse_int_literal(s, pos)
        if not 0 <= val <= 0xFFFFFF:
            raise MacroError("rect: color out of 0xRRGGBB range", pos)
        return val

    def _parse_pixelrect(self, t, pos):
        r"""Parse \pixelrect{\row{...}, ...} or \rect{\row{...}, ...} -> (w, h, rgb bytes)."""
        t = t.strip()
        if t.startswith(r"\pixelrect"):
            name = "pixelrect"
            rest = t[len(r"\pixelrect") :].strip()
        elif t.startswith(r"\rect"):
            name = "rect"
            rest = t[len(r"\rect") :].strip()
        else:
            raise MacroError(r"expected \pixelrect or \rect", pos)

        if not (rest.startswith("{") and rest.endswith("}")):
            raise MacroError(f"{name} requires {{\\row{{...}}, ...}}", pos)

        inner = rest[1:-1].strip()
        if not inner:
            raise MacroError(f"{name}: empty literal", pos)

        # Extract rows
        i = 0
        n = len(inner)
        rows_tokens = []
        while i < n:
            while i < n and inner[i] in " \t\r\n,":
                i += 1
            if i >= n:
                break
            if not inner[i:].startswith(r"\row"):
                raise MacroError(f"{name}: rows must be \\row{{...}}", pos)
            i += 4  # skip \row
            while i < n and inner[i] in " \t\r\n":
                i += 1
            if i >= n or inner[i] != "{":
                raise MacroError(f"{name}: rows must be \\row{{...}}", pos)
            start = i + 1
            depth = 1
            i += 1
            while i < n and depth > 0:
                if inner[i] == "{":
                    depth += 1
                elif inner[i] == "}":
                    depth -= 1
                i += 1
            if depth != 0:
                raise MacroError("Unmatched { in row", pos)
            row_content = inner[start : i - 1].strip()
            rows_tokens.append(row_content)

        if not rows_tokens:
            raise MacroError(f"{name}: empty literal", pos)

        rows = []
        for r_tok in rows_tokens:
            pixels_str = _split_compound_group(r_tok, pos)
            if not pixels_str:
                raise MacroError(f"{name}: empty literal", pos)
            row = [self._parse_pixel_color(p, pos) for p in pixels_str]
            rows.append(row)

        w = len(rows[0])
        h = len(rows)
        if w == 0:
            raise MacroError(f"{name}: empty literal", pos)
        for r in rows:
            if len(r) != w:
                raise MacroError(f"{name}: ragged rows", pos)
        if w > RECT_MAX_EDGE or h > RECT_MAX_EDGE:
            raise MacroError(f"{name}: edge capped at {RECT_MAX_EDGE}", pos)

        data = bytearray()
        for r in rows:
            for color in r:
                data.extend([(color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF])
        return w, h, bytes(data)

    def _materialize_constructor(self, s, pos, skip_label, reserved=()):
        r"""Compile-time constructor materialization.
        Emits:
            CALL_EXT sys.const, TAG_IMM cid
            BRA $err, =, 0, off -> skip_label
            SET scratch, TAG_REG $ret
        Returns (TAG_REG, scratch)."""
        if self.runtime is None:
            name = "pixelrect" if "\\pixelrect" in s else "rect"
            raise MacroError(f"{name} literal requires a runtime registry", pos)
        w, h, data = self._parse_pixelrect(s, pos)
        try:
            cid = self.runtime.add_const("snap", {"w": w, "h": h, "rgb": data})
        except ValueError as e:
            raise MacroError(f"rect: {e}", pos)

        scratch = self._alloc_temp(reserved, pos)
        self._emit(
            OP_CALL_EXT,
            self._str_idx("sys", pos),
            self._str_idx("const", pos),
            1,
        )
        self._emit(TAG_IMM)
        self._emit_i32(cid)
        self._emit_bra(0, REG_ERR, (TAG_IMM, 0), skip_label)
        self._emit(OP_SET, scratch)
        self._emit(TAG_REG, REG_RET)
        return (TAG_REG, scratch)

    def _cmd_set(self, pos):
        r"""\set{x}{i32|$var|constructor} — assign a variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("set requires {name}", self._pos)
        name = name.strip().lower()
        reg = self._alloc_var(name, pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("set requires {value}", self._pos)
        s = arg.strip()
        if self._is_constructor(s):
            skip_label = Label("consumer_skip")
            _, scratch = self._materialize_constructor(s, pos, skip_label, reserved=(reg,))
            self._emit(OP_SET, reg)
            self._emit(TAG_REG, scratch)
            skip_label.mark(self)
            self._types[name] = "snap"
            return
        if self._is_rgb(s):
            val = self._parse_rgb_literal(s, pos)
            self._emit(OP_SET, reg)
            self._emit_typed((TAG_IMM, val), 4)
            self._types[name] = "int"
            return
        v = self._parse_value(arg, pos, reserved=(reg,))
        self._emit(OP_SET, reg)
        self._emit_typed(v, 4)
        if v[0] == TAG_REG:
            self._types[name] = self._reg_type(v[1])
        else:
            self._types[name] = "int"

    def _cmd_add(self, pos):
        r"""\add{x}{i32|$var|constructor} — add to an existing variable."""
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
        s = arg.strip()
        if self._is_constructor(s):
            skip_label = Label("consumer_skip")
            _, scratch = self._materialize_constructor(s, pos, skip_label, reserved=(reg,))
            self._emit(OP_ADD, reg)
            self._emit(TAG_REG, scratch)
            skip_label.mark(self)
            return
        v = self._parse_value(arg, pos, reserved=(reg,))
        self._emit(OP_ADD, reg)
        self._emit_typed(v, 4)

    def _cmd_sub(self, pos):
        r"""\sub{x}{i32|$var|constructor} — subtract from an existing variable."""
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
        s = arg.strip()
        if self._is_constructor(s):
            skip_label = Label("consumer_skip")
            _, scratch = self._materialize_constructor(s, pos, skip_label, reserved=(reg,))
            self._emit(OP_SUB, reg)
            self._emit(TAG_REG, scratch)
            skip_label.mark(self)
            return
        v = self._parse_value(arg, pos, reserved=(reg,))
        self._emit(OP_SUB, reg)
        self._emit_typed(v, 4)

    def _emit_bra(self, cc, reg, rhs_val, target_label):
        self._emit(OP_BRA, cc, reg)
        self._emit_typed(rhs_val, 4)
        patch_pos = len(self._bc)
        self._emit_u16(0)
        target_label.add_fixup(patch_pos, self)

    def _emit_jmp(self, target_label):
        self._emit(OP_JMP)
        patch_pos = len(self._bc)
        self._emit_u16(0)
        target_label.add_fixup(patch_pos, self)

    def _emit_triplet(self, node, true_target, false_target):
        # Step 1: Ephemeral Value Pre-Loading in source order (§5.4 #1)
        if self._is_constructor(node.lhs_str):
            lhs_val = self._materialize_constructor(node.lhs_str, node.pos, false_target, reserved=())
        else:
            lhs_val = self._parse_value(node.lhs_str, node.pos)

        rhs_reserved = (lhs_val[1],) if lhs_val[0] == TAG_REG else ()
        if self._is_constructor(node.rhs_str):
            rhs_val = self._materialize_constructor(node.rhs_str, node.pos, false_target, reserved=rhs_reserved)
        else:
            rhs_val = self._parse_value(node.rhs_str, node.pos, reserved=rhs_reserved)
        cc = node.cc

        # Check handle types if known at compile time
        if (
            lhs_val[0] == TAG_REG
            and lhs_val[1] != REG_RET
            and self._reg_type(lhs_val[1]) not in ("int", "color")
        ):
            raise MacroError(
                f"if: cannot compare a {self._reg_type(lhs_val[1])} handle", node.pos
            )
        if (
            rhs_val[0] == TAG_REG
            and rhs_val[1] != REG_RET
            and self._reg_type(rhs_val[1]) not in ("int", "color")
        ):
            raise MacroError(
                f"if: cannot compare a {self._reg_type(rhs_val[1])} handle", node.pos
            )

        # Step 2: Immediate/Register Swapping (§5.3 Converse Operator Mapping)
        if lhs_val[0] == TAG_IMM and rhs_val[0] == TAG_REG:
            lhs_val, rhs_val = rhs_val, lhs_val
            cc = CONVERSE_CC[cc]

        # Step 3: Static Constant Elimination (§5.4 #3)
        if lhs_val[0] == TAG_IMM and rhs_val[0] == TAG_IMM:
            a = lhs_val[1]
            b = rhs_val[1]
            if cc == 0:
                cond = (a == b)
            elif cc == 1:
                cond = (a != b)
            elif cc == 2:
                cond = (a < b)
            elif cc == 3:
                cond = (a <= b)
            elif cc == 4:
                cond = (a > b)
            else:
                cond = (a >= b)
            if cond:
                self._emit_jmp(true_target)
            else:
                self._emit_jmp(false_target)
            return

        # Step 4: Emit native BRA + JMP (§5.2 #1)
        self._emit_bra(cc, lhs_val[1], rhs_val, false_target)
        self._emit_jmp(true_target)

    def _emit_predicate(self, node, true_target, false_target):
        if isinstance(node, TripletNode):
            self._emit_triplet(node, true_target, false_target)
        elif isinstance(node, LogicNode):
            n = len(node.children)
            if node.op == "all":
                for i, child in enumerate(node.children):
                    if i < n - 1:
                        child_true = Label(f"all_{i+1}")
                    else:
                        child_true = true_target
                    self._emit_predicate(child, child_true, false_target)
                    if i < n - 1:
                        child_true.mark(self)
            elif node.op == "any":
                for i, child in enumerate(node.children):
                    if i < n - 1:
                        child_false = Label(f"any_{i+1}")
                    else:
                        child_false = false_target
                    self._emit_predicate(child, true_target, child_false)
                    if i < n - 1:
                        child_false.mark(self)

    def _parse_predicate(self, s, pos, nest_depth=1):
        """Parse a Predicate (Triplet or LogicBlock) with nesting limit MAX_PRED_NEST."""
        if nest_depth > MAX_PRED_NEST:
            raise MacroError(f"Predicate nesting exceeds {MAX_PRED_NEST} levels", pos)
        s = s.strip()
        if s.startswith(r"\all") or s.startswith(r"\any"):
            kind = "all" if s.startswith(r"\all") else "any"
            rest = s[4:].lstrip()
            if not (rest.startswith("{") and rest.endswith("}")):
                raise MacroError(f"\\{kind} requires {{...}}", pos)
            inner = rest[1:-1].strip()
            items = _split_compound_group(inner, pos)
            if not items:
                raise MacroError(f"\\{kind} requires at least one predicate", pos)
            children = [
                self._parse_predicate(item, pos, nest_depth + 1)
                for item in items
            ]
            return LogicNode(kind, children, pos)
        elif s.startswith("{") and s.endswith("}"):
            inner = s[1:-1].strip()
            parts = _split_compound_group(inner, pos)
            if len(parts) != 3:
                raise MacroError("Predicate triplet requires {lhs, op, rhs}", pos)
            lhs_str, op_str, rhs_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if op_str not in CC_OPS:
                raise MacroError(
                    f"Invalid relational operator: {op_str} (must be one of = != < <= > >=)", pos
                )
            cc = CC_OPS[op_str]
            return TripletNode(lhs_str, cc, rhs_str, pos)
        else:
            raise MacroError(r"Invalid predicate: expected {...}, \all, or \any", pos)

    def _cmd_if(self, pos):
        r"""\if{predicate}{then}[{else}] — conditional branch."""
        if self._runtime_depth >= MAX_RUNTIME_NEST:
            raise MacroError(f"if nesting exceeds {MAX_RUNTIME_NEST} levels", pos)
        pred_str = self._read_braces()
        if pred_str is None:
            raise MacroError("if requires {predicate}", pos)
        if not pred_str.strip():
            raise MacroError("empty predicate", pos)
        pred_node = self._parse_predicate(pred_str, pos)

        then_body = self._read_braces()
        if then_body is None:
            raise MacroError("if requires {then}", pos)
        else_body = self._read_braces()  # optional

        then_label = Label("then_start")
        false_label = Label("false_target")

        self._emit_predicate(pred_node, then_label, false_label)
        then_label.mark(self)
        self._nest_into(then_body, "if then", ("if_then", false_label, else_body))

    def _cmd_all(self, pos):
        raise MacroError(r"\all is only valid inside a predicate", pos)

    def _cmd_any(self, pos):
        raise MacroError(r"\any is only valid inside a predicate", pos)

    def _cmd_guarded(self, pos):
        r"""\guarded [ [throttle] ] { predicate } { body } [ { on_abort } ]"""
        if self._guard_depth >= MAX_GUARD_NEST:
            raise MacroError(f"guard nesting exceeds {MAX_GUARD_NEST} levels", pos)

        self._skip_absorbed_whitespace()
        has_throttle = 0
        throttle_ms = 0
        if self._peek() == "[":
            self._advance()  # skip [
            start = self._pos
            while self._peek() is not None and self._peek() != "]":
                self._advance()
            if self._peek() != "]":
                raise MacroError("Unmatched [ in guarded throttle", start)
            t_str = self._text[start : self._pos].strip()
            self._advance()  # skip ]
            if t_str.startswith("$"):
                raise MacroError("guarded throttle must be static literal", start)
            if not t_str:
                raise MacroError("Empty guarded throttle", start)
            # Parse throttle duration
            if t_str.endswith("ms"):
                inner = t_str[:-2].strip()
                if not inner.isdigit():
                    raise MacroError(f"Invalid guarded throttle: {t_str}", start)
                throttle_ms = int(inner)
            elif t_str.endswith("s"):
                inner = t_str[:-1].strip()
                if "." in inner:
                    int_part, dot, frac_part = inner.partition(".")
                    if not int_part.isdigit() or not frac_part.isdigit():
                        raise MacroError(f"Invalid guarded throttle: {t_str}", start)
                    k = len(frac_part)
                    N = int(int_part) * (10 ** k) + int(frac_part)
                    throttle_ms = (2000 * N + (10 ** k)) // (2 * (10 ** k))
                else:
                    if not inner.isdigit():
                        raise MacroError(f"Invalid guarded throttle: {t_str}", start)
                    throttle_ms = int(inner) * 1000
            elif t_str.isdigit():
                throttle_ms = int(t_str)
            else:
                raise MacroError(f"Invalid guarded throttle: {t_str}", start)

            if not 0 <= throttle_ms <= 65535:
                raise MacroError(f"guarded throttle out of range [0, 65535]: {throttle_ms}", start)
            has_throttle = 1

        pred_str = self._read_braces()
        if pred_str is None:
            raise MacroError("guarded requires {predicate}", self._pos)
        pred_str = pred_str.strip()
        if not pred_str:
            raise MacroError("empty guard predicate", pos)

        # Parse guard predicate
        logic_mode, triplets = self._parse_guard_predicate(pred_str, pos)

        # Build descriptor bytes
        desc_bytes = bytearray()
        for cc, lhs_tag, lhs_bytes, rhs_tag, rhs_bytes in triplets:
            spec = (cc & 7) | ((lhs_tag & 3) << 3) | ((rhs_tag & 3) << 5)
            desc_bytes.append(spec)
            desc_bytes.extend(lhs_bytes)
            desc_bytes.extend(rhs_bytes)

        triplet_cnt = len(triplets)
        header = ((logic_mode & 1) << 7) | (((triplet_cnt - 1) & 7) << 4) | (has_throttle & 1)

        then_body = self._read_braces()
        if then_body is None:
            raise MacroError("guarded requires {body}", self._pos)
        on_abort_body = self._read_braces()  # optional

        # Emit GUARD_ENTER instruction header
        self._emit(OP_GUARD_ENTER)
        patch_pos = len(self._bc)
        self._emit_u16(0)  # placeholder for abort_off
        self._emit(header)
        if has_throttle:
            self._emit_u16(throttle_ms)
        self._emit(desc_bytes)
        base_point = len(self._bc)

        self._guard_depth += 1
        cont = ("guarded_body", patch_pos, base_point, on_abort_body)
        self._nest_into(then_body, "guarded body", cont)

    def _parse_guard_predicate(self, s, pos):
        s = s.strip()
        if s.startswith(r"\all"):
            logic_mode = 0
            rest = s[4:].lstrip()
            if not (rest.startswith("{") and rest.endswith("}")):
                raise MacroError(r"\all requires { ... }", pos)
            inner = rest[1:-1].strip()
            raw_triplets = _split_compound_group(inner, pos)
        elif s.startswith(r"\any"):
            logic_mode = 1
            rest = s[4:].lstrip()
            if not (rest.startswith("{") and rest.endswith("}")):
                raise MacroError(r"\any requires { ... }", pos)
            inner = rest[1:-1].strip()
            raw_triplets = _split_compound_group(inner, pos)
        else:
            logic_mode = 0
            raw_triplets = [s]

        if not raw_triplets or len(raw_triplets) > 8:
            raise MacroError("guarded does not support nested predicate logic; max 8 flat triplets", pos)

        # Check for nested \all or \any in any triplet string
        for t in raw_triplets:
            if r"\all" in t or r"\any" in t:
                raise MacroError("guarded does not support nested predicate logic; max 8 flat triplets", pos)

        parsed_triplets = []
        for t in raw_triplets:
            parsed_triplets.append(self._parse_guard_triplet(t, pos))
        return logic_mode, parsed_triplets

    def _parse_guard_triplet(self, s, pos):
        s = s.strip()
        if not s.startswith("{") or not s.endswith("}"):
            raise MacroError("guard condition must be {lhs, op, rhs}", pos)
        inner = s[1:-1].strip()
        parts = _split_compound_group(inner, pos)
        if len(parts) != 3:
            raise MacroError("guard condition must be {lhs, op, rhs}", pos)
        lhs_str, op_str, rhs_str = parts[0].strip(), parts[1].strip(), parts[2].strip()
        if lhs_str.startswith("{") and lhs_str.endswith("}"):
            lhs_str = lhs_str[1:-1].strip()
        if op_str.startswith("{") and op_str.endswith("}"):
            op_str = op_str[1:-1].strip()
        if rhs_str.startswith("{") and rhs_str.endswith("}"):
            rhs_str = rhs_str[1:-1].strip()
        if op_str not in CC_OPS:
            raise MacroError(f"invalid comparison operator: {op_str}", pos)
        cc = CC_OPS[op_str]
        lhs_tag, lhs_bytes = self._parse_guard_operand(lhs_str, pos)
        rhs_tag, rhs_bytes = self._parse_guard_operand(rhs_str, pos)
        return (cc, lhs_tag, lhs_bytes, rhs_tag, rhs_bytes)

    def _parse_guard_operand(self, s, pos):
        s = s.strip()
        if not s:
            raise MacroError("empty guard operand", pos)

        if self._is_constructor(s):
            raise MacroError("guards do not support constructors", pos)
        if self._is_rgb(s):
            val = self._parse_rgb_literal(s, pos)
            val = val - 4294967296 if val > 2147483647 else val
            return (GUARD_TAG_IMM, val.to_bytes(4, "little", signed=True))

        # Slot reference?
        if s.startswith("${"):
            end = s.find("}")
            if end == -1:
                raise MacroError("Unmatched ${", pos)
            inner = s[2:end].strip()
            if s[end + 1 :].strip():
                raise MacroError(f"Unexpected text after ${{{inner}}}", pos)
        elif s.startswith("$"):
            inner = s[1:].strip()
        else:
            inner = None

        if inner is not None:
            if inner.startswith("slot."):
                slot_name = inner[5:]
            elif "." in inner:
                slot_name = inner
            else:
                slot_name = None

            if slot_name is not None:
                sig, sep, field = slot_name.rpartition(".")
                if not sep or not sig or not field:
                    raise MacroError(f"Invalid slot reference: {s}", pos)
                ns = sig.split(".")[0]
                if self.runtime is not None:
                    if (
                        ns not in self._used_namespaces
                        and not self.runtime.has_slot_ns(sig)
                        and not self.runtime.has_signal(sig)
                    ):
                        raise MacroError(f"unknown signal namespace: {ns}", pos)
                sig_idx = self._str_idx(sig, pos)
                field_idx = self._str_idx(field, pos)
                return (GUARD_TAG_SLOT, bytes([sig_idx, field_idx]))
            else:
                # Variable reference
                reg = self._var_reg(inner, pos)
                return (GUARD_TAG_REG, bytes([reg]))

        # Immediate literal
        val = self._parse_int_literal(s, pos)
        val = val - 4294967296 if val > 2147483647 else val
        return (GUARD_TAG_IMM, val.to_bytes(4, "little", signed=True))

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
        self._used_namespaces.add(ns)

    def _cmd_call(self, pos):
        r"""\call{ns.fn}{arg}... — invoke a runtime extension function."""
        target = self._read_braces()
        if target is None:
            raise MacroError("call requires {ns.fn}", self._pos)
        target = target.strip().lower()
        if target == "sys.const":
            raise MacroError("sys.const is reserved for compiler lowering", pos)
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

        consumer_skip = Label("consumer_skip")
        args = []
        for typ in sig:
            a = self._read_braces()
            if a is None:
                raise MacroError(f"{target} expects {len(sig)} arg(s)", self._pos)
            # reserve the registers of already-parsed args so slot desugars
            # for later args cannot clobber them
            reserved = tuple(v for tag, v in args if tag == TAG_REG)
            parsed = self._parse_ext_arg(a, typ, target, pos, reserved, consumer_skip)
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
        consumer_skip.mark(self)
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

    def _parse_ext_arg(self, a, typ, target, pos, reserved=(), consumer_skip=None):
        r"""Compile one ext arg per its signature type. point/rect return a
        LIST of typed operands (compound value in one brace group, e.g.
        {x,y} / {x,y,w,h} — the \move{dx,dy} idiom); all other types
        return a single (tag, value) pair."""
        s = a.strip()
        if self._is_constructor(s):
            if typ != "snap":
                raise MacroError(f"{target}: pixelrect literal is a snap handle", pos)
            if consumer_skip is None:
                consumer_skip = Label("consumer_skip")
            return self._materialize_constructor(s, pos, consumer_skip, reserved)

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
            s_unesc = self._unescape_str(s)
            if s_unesc.startswith("$"):
                raise MacroError(f"{target}: str args are literal (no string variables)", pos)
            return (TAG_STR, self._str_idx(s_unesc, pos))
        if typ in ("int", "color"):
            return self._parse_value(a, pos, reserved)
        # handle types (snap, tpl, ...):
        if not s.startswith("$"):
            raise MacroError(f"{target}: {typ} arg must be a variable ($handle)", pos)
        reg = self._parse_var_ref(s, pos, reserved)
        vt = self._reg_type(reg)
        if vt != typ:
            raise MacroError(
                f"{target}: arg must hold a {typ} handle (got {vt})", pos)
        return (TAG_REG, reg)

    def _parse_wait_timeout(self, s, pos):
        r"""Parse \wait[timeout]: DimLiteral | DecLiteral | ScalarRef."""
        s = s.strip()
        if not s:
            raise MacroError("Empty wait timeout", pos)
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos))

        neg = False
        if s.startswith("-"):
            neg = True
            s = s[1:].strip()
        elif s.startswith("+"):
            s = s[1:].strip()

        if s.endswith("ms"):
            inner = s[:-2].strip()
            if not inner.isdigit():
                raise MacroError(f"Invalid wait timeout: {s}", pos)
            ms = int(inner)
        elif s.endswith("s"):
            inner = s[:-1].strip()
            if "." in inner:
                int_part, dot, frac_part = inner.partition(".")
                if not int_part.isdigit() or not frac_part.isdigit():
                    raise MacroError(f"Invalid wait timeout: {s}", pos)
                k = len(frac_part)
                N = int(int_part) * (10 ** k) + int(frac_part)
                ms = (2000 * N + (10 ** k)) // (2 * (10 ** k))
            else:
                if not inner.isdigit():
                    raise MacroError(f"Invalid wait timeout: {s}", pos)
                ms = int(inner) * 1000
        elif "." in s:
            try:
                ms = int(float(s) * 1000)
            except ValueError:
                raise MacroError(f"Invalid wait timeout: {s}", pos)
        elif s.startswith("0x") or s.startswith("0X"):
            try:
                ms = int(s[2:], 16)
            except ValueError:
                raise MacroError(f"Invalid wait timeout: {s}", pos)
        elif s.isdigit():
            ms = int(s)
        else:
            raise MacroError(f"Invalid wait timeout: {s}", pos)

        if neg:
            ms = -ms

        if not (-2147483648 <= ms <= 2147483647):
            raise MacroError("wait timeout out of i32 range", pos)
        return (TAG_IMM, ms)

    def _cmd_wait(self, pos):
        r"""\wait[timeout]{ $slot.<sig>.<field> } — suspend until slot event arrives."""
        self._skip_absorbed_whitespace()
        timeout = None
        if self._peek() == "[":
            self._advance()  # skip [
            start = self._pos
            while self._peek() is not None and self._peek() != "]":
                self._advance()
            if self._peek() != "]":
                raise MacroError("Unmatched [ in wait timeout", start)
            t_str = self._text[start : self._pos].strip()
            self._advance()  # skip ]
            timeout = self._parse_wait_timeout(t_str, start)
        else:
            timeout = (TAG_IMM, DEFAULT_WAIT_TIMEOUT_MS)

        arg = self._read_braces()
        if arg is None:
            raise MacroError("wait requires { $slot.<sig>.<field> }", self._pos)
        s = arg.strip()
        if not s.startswith("$"):
            raise MacroError("wait target must be a slot reference ($slot.<sig>.<field>)", pos)

        if s.startswith("${"):
            end = s.find("}")
            if end == -1:
                raise MacroError("Unmatched ${ in slot reference", pos)
            name = s[2:end].strip()
            if s[end + 1 :].strip():
                raise MacroError(f"Unexpected text after ${{{name}}}", pos)
        else:
            name = s[1:].strip()

        if name.startswith("slot."):
            name = name[5:]

        if "." not in name:
            raise MacroError(f"wait target must be a slot reference ($slot.<sig>.<field>): {s}", pos)

        sig, sep, field = name.rpartition(".")
        if not sig or not field:
            raise MacroError(f"Invalid slot reference: {s}", pos)

        ns = sig.split(".")[0]
        if self.runtime is not None:
            if (
                ns not in self._used_namespaces
                and not self.runtime.has_slot_ns(sig)
                and not self.runtime.has_signal(sig)
            ):
                raise MacroError(f"unknown signal namespace: {ns}", pos)

        sig_idx = self._str_idx(sig, pos)
        field_idx = self._str_idx(field, pos)
        self._emit(OP_WAIT, sig_idx, field_idx)
        self._emit_typed(timeout, 4)

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

