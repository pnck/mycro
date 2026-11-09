# Lexical analysis, preamble directives, macro expansion, and syntax handling for MYCRO compiler
from keymap import ENTER, TAB
from bytecode import (
    MacroError,
    MAX_MACRO_EXPAND,
    OP_CHAR,
    OP_KEY,
    _PRINTABLE_LONE_KEYS,
    CONSUMER_COMMANDS,
    _is_alpha,
    _is_alnum,
)


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


class SyntaxMixin:
    """Mixin for lexical navigation, directives, macros, and verbatim text blocks."""

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
        last_open_is_ref = False
        prev = ""
        while self._pos < self._len and depth > 0:
            ch = self._advance()
            nxt = self._peek()
            if ch == "\\" and nxt is not None and nxt in "{}\\":
                self._advance()  # escaped char: skip, don't count
                prev = nxt
                continue
            if ch == "{":
                depth += 1
                last_open_is_ref = (prev == "$")
            elif ch == "}":
                depth -= 1
                last_open_is_ref = False
            prev = ch
        if depth != 0:
            if last_open_is_ref:
                raise MacroError("Unmatched ${", start - 1)
            raise MacroError("Unmatched {", start - 1)
        return self._text[start : self._pos - 1]

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
            or name in CONSUMER_COMMANDS
            or name in ("rgb", "all", "any")
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
        # \use is only allowed at top level (spec/dsl.md §9.3)
        idx = 0
        while True:
            idx = body.find(r"\use", idx)
            if idx == -1:
                break
            bs_count = 0
            p = idx - 1
            while p >= 0 and body[p] == "\\":
                bs_count += 1
                p -= 1
            after = idx + 4
            if bs_count % 2 == 0 and (after >= len(body) or not (_is_alnum(body[after]) or body[after] == "_")):
                line_start = body.rfind("\n", 0, idx)
                line_start = 0 if line_start == -1 else line_start + 1
                comment_idx = body.find(r"\#", line_start)
                if comment_idx == -1 or comment_idx > idx:
                    raise MacroError("use only allowed at top level", pos + idx)
            idx = after
        self._defs[name] = (argc, body)

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
