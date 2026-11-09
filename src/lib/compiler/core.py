# Core bytecode emitter, non-recursive nesting machine, and Compiler facade for MYCRO
from keymap import ENTER, TAB
from bytecode import (
    VERSION,
    OP_CHAR,
    OP_KEY,
    OP_LOOP,
    OP_LOOP_END,
    OP_GUARD_EXIT,
    OP_END,
    OP_CONSUMER_KEY,
    TAG_IMM,
    TAG_REG,
    MAX_CODE_BYTES,
    DEFAULT_CLICK_HOLD_MS,
    CONSUMER_COMMANDS,
    _is_alpha,
    read_header,
    MacroError,
)
from .label import Label
from .syntax import SyntaxMixin
from .literals import LiteralMixin
from .predicates import PredicateMixin
from .actions import ActionsMixin


class Compiler(SyntaxMixin, LiteralMixin, PredicateMixin, ActionsMixin):
    """Macro DSL Compiler for MYCRO.
    Normative reference: spec/dsl.md (grammar, syntax lowering, type environment).
    """

    def __init__(self, runtime=None, click_hold_ms=DEFAULT_CLICK_HOLD_MS):
        self.runtime = runtime
        self.click_hold_ms = click_hold_ms
        self._emit_newline = True
        self._emit_space = True
        self._first_executable_pos = 0
        self._declared_namespaces = set()
        self.activated_namespaces = []
        self._guard_depth = 0

    def compile(self, text, config=None, click_hold_ms=None):
        """Compile macro text to bytecode. Returns (bytecode, error_msg).

        Nesting (rep/if/guarded/macro bodies) is parsed iteratively on an explicit
        heap stack (self._nest_stack), NOT by Python recursion: the device
        pystack only survives ~2 levels of recursive compilation once the
        asyncio + httpserver baseline is active.
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
        self._declared_namespaces = set()
        self.activated_namespaces = []
        self._guard_depth = 0
        if self.runtime is not None:
            self.runtime.clear_consts()  # const resources belong to one compilation

        try:
            self._parse_preamble()
            self._emit(VERSION, 0)  # header placeholder, rebuilt below
            self._parse()
            self._emit(OP_END)
            # Rebuild the header with the collected string table
            hdr = bytearray([VERSION, len(self._strs)])
            for s in self._strs:
                hdr.append(len(s.encode("utf-8")))
                hdr.extend(s.encode("utf-8"))
            return bytes(hdr + self._bc[2:]), None
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
        return read_header(bc)

    def _emit(self, *args):
        size = 0
        for b in args:
            size += 1 if isinstance(b, int) else len(b)
        if len(self._bc) - 2 + size > MAX_CODE_BYTES:
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

        if name_lower == "rgb":
            raise MacroError(
                r"\rgb{...} is a value-position literal (valid inside \set{...} or an argument)", pos)

        # Consumer Control commands: \mute, \volup, \media_next, etc. (spec/dsl.md §5.2)
        if name_lower in CONSUMER_COMMANDS:
            self._emit(OP_CONSUMER_KEY)
            self._emit_typed((TAG_IMM, CONSUMER_COMMANDS[name_lower]), 2)
            return

        # User-defined macro: \name{arg}... (compile-time expansion)
        if name_lower in self._defs:
            self._expand_macro(name_lower, pos)
            return

        # Otherwise it's a key or combo: \enter, \ctrl+c, \a, \A
        self._parse_key_or_combo(name)

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
                if self._emit_newline:
                    self._emit(OP_KEY, ENTER)
            elif ch == "\r":
                if self._pos + 1 < self._len and self._text[self._pos + 1] == "\n":
                    if self._emit_newline:
                        self._emit(OP_KEY, ENTER)
                    self._advance()  # skip \r
                    self._advance()  # skip \n
                    continue
                else:
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
