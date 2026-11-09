# Literal parsing, variables, registers, and pixel matrix constructs for MYCRO compiler
from .label import Label
from .syntax import _split_compound_group
from bytecode import (
    TAG_IMM,
    TAG_REG,
    MAX_REGS,
    RECT_MAX_EDGE,
    REG_ERR,
    REG_TIMEOUT,
    REG_RET,
    OP_READSLOT,
    OP_CALL_EXT,
    OP_SET,
    CC_OPS,
    MacroError,
    _is_alpha,
    _is_alnum,
)


class LiteralMixin:
    """Mixin for parsing numbers, durations, variables, registers, and pixel matrices."""

    def _parse_int_literal(self, s, pos):
        """Fold a NumericLiteral to i32 without binary floating point."""
        s = s.strip()
        neg = s.startswith("-")
        if neg or s.startswith("+"):
            s = s[1:].strip()
        try:
            if s[:2].lower() == "0x":
                if not s[2:]:
                    raise ValueError
                v = int(s[2:], 16)
            elif s.endswith("ms"):
                inner = s[:-2].strip()
                if not inner.isdigit():
                    raise ValueError
                v = int(inner)
            elif s.endswith("s"):
                inner = s[:-1].strip()
                int_part, dot, frac_part = inner.partition(".")
                if not int_part.isdigit() or (dot and not frac_part.isdigit()):
                    raise ValueError
                k = len(frac_part)
                n = int(int_part) * (10 ** k) + (int(frac_part) if dot else 0)
                v = (2000 * n + (10 ** k)) // (2 * (10 ** k))
            elif "." in s:
                raise MacroError("fractional number requires unit 's'", pos)
            else:
                if not s.isdigit():
                    raise ValueError
                v = int(s)
        except ValueError:
            raise MacroError(f"Invalid integer: {s}", pos)
        if neg:
            v = -v
        if not (-2147483648 <= v <= 2147483647):
            raise MacroError("integer out of i32 range", pos)
        return v

    def _parse_duration_literal(self, s, pos):
        """Duration and timeout grammars exclude HexLiteral."""
        unsigned = s.strip().lstrip("+-").strip()
        if unsigned[:2].lower() == "0x":
            raise MacroError("hexadecimal duration not allowed", pos)
        return self._parse_int_literal(s, pos)

    def _parse_value(self, s, pos, reserved=()):
        """Value-position segment: int literal, $var/${var}, or \rgb{r,g,b}.
        Returns (TAG_IMM, v) or (TAG_REG, reg). `reserved` holds registers
        already consumed by sibling operands of the same instruction."""
        s = s.strip()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        if self._is_rgb(s):
            return (TAG_IMM, self._parse_rgb_literal(s, pos))
        if self._is_constructor(s):
            raise MacroError("pixelrect requires destination statement", pos)
        return (TAG_IMM, self._parse_int_literal(s, pos))

    def _parse_var_ref(self, s, pos, reserved=()):
        """$name or ${name} -> register index (must be defined)."""
        if s.startswith("${"):
            end = s.find("}")
            if end == -1:
                raise MacroError("Unmatched ${", pos)
            name = s[2:end].strip()
            if not name:
                raise MacroError("Empty reference", pos)
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
            self._check_slot_namespace(ns, sig, pos)
            reg = self._alloc_temp(reserved, pos)
            self._emit(
                OP_READSLOT, reg, self._str_idx(sig, pos), self._str_idx(field, pos)
            )
            return reg
        if name not in self._vars:
            raise MacroError(f"Undefined variable: {name}", pos)
        return self._vars[name]

    def _check_slot_namespace(self, ns, sig, pos):
        """Enforce namespace activation for slot references (spec/dsl.md §9.3)."""
        if self.runtime is None:
            raise MacroError("slot reference requires a runtime registry", pos)
        if ns not in self._declared_namespaces:
            raise MacroError(f"unknown signal namespace: {ns}", pos)

    def _alloc_temp(self, reserved, pos):
        """Temp register for a slot desugar, live only within the current
        instruction: taken from beyond the named-var block, skipping
        registers reserved by sibling operands."""
        blocked = set(self._vars.values()) | set(reserved)
        for r in range(MAX_REGS):
            if r not in blocked:
                return r
        raise MacroError("no free register for slot operand", pos)

    def _validate_var_name(self, name, pos):
        """Validate variable name for assignment without allocating."""
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
        if name not in self._vars and len(self._vars) >= MAX_REGS:
            raise MacroError(f"Too many variables (max {MAX_REGS})", pos)

    def _alloc_var(self, name, pos):
        """Resolve a variable name for assignment, allocating a register if new."""
        self._validate_var_name(name, pos)
        if name not in self._vars:
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
        """Duration arg: static milliseconds or a dynamic register holding ms."""
        s = s.strip()
        if s.startswith("$"):
            return (TAG_REG, self._parse_var_ref(s, pos, reserved))
        if s.startswith("-"):
            raise MacroError("negative duration not allowed", pos)
        ms = self._parse_duration_literal(s, pos)
        if not 0 <= ms <= 65535:
            raise MacroError("duration out of range [0, 65535]", pos)
        return (TAG_IMM, ms)

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

    def _parse_pixel_rows(self, inner, pos):
        r"""Parse PixelRows: Row ("," Row)* where Row is \row{PixelColor, ...} -> (w, h, rgb_bytes)."""
        inner = inner.strip()
        if not inner:
            raise MacroError("pixelrect: empty literal", pos)

        i = 0
        n = len(inner)
        rows_tokens = []
        while i < n:
            while i < n and inner[i] in " \t\r\n,":
                i += 1
            if i >= n:
                break
            if not inner[i:].startswith(r"\row"):
                raise MacroError("pixelrect: rows must be \\row{...}", pos)
            i += 4  # skip \row
            while i < n and inner[i] in " \t\r\n":
                i += 1
            if i >= n or inner[i] != "{":
                raise MacroError("pixelrect: rows must be \\row{...}", pos)
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
            raise MacroError("pixelrect: empty literal", pos)

        rows = []
        for r_tok in rows_tokens:
            pixels_str = _split_compound_group(r_tok, pos)
            if not pixels_str:
                raise MacroError("pixelrect: empty literal", pos)
            row = [self._parse_pixel_color(p, pos) for p in pixels_str]
            rows.append(row)

        w = len(rows[0])
        h = len(rows)
        if w == 0:
            raise MacroError("pixelrect: empty literal", pos)
        for r in rows:
            if len(r) != w:
                raise MacroError("pixelrect: matrix rows have unequal length", pos)
        if w > RECT_MAX_EDGE or h > RECT_MAX_EDGE:
            raise MacroError(f"pixelrect: edge capped at {RECT_MAX_EDGE}", pos)

        data = bytearray()
        for r in rows:
            for color in r:
                data.extend([(color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF])
        return w, h, bytes(data)

    def _cmd_pixelrect(self, pos):
        r"""\pixelrect{name}{rows} — destination-bound pixel resource statement (spec/dsl.md §6.4)."""
        self._skip_absorbed_whitespace()
        name = self._read_braces()
        if name is None:
            raise MacroError("pixelrect requires {name}", pos)
        name = name.strip().lower()
        self._validate_var_name(name, pos)

        self._skip_absorbed_whitespace()
        rows_str = self._read_braces()
        if rows_str is None:
            raise MacroError("pixelrect requires {rows}", pos)

        w, h, data = self._parse_pixel_rows(rows_str, pos)

        if self.runtime is None:
            raise MacroError("pixelrect statement requires a runtime registry", pos)
        try:
            cid = self.runtime.add_const("snap", {"w": w, "h": h, "rgb": data})
        except ValueError as e:
            raise MacroError(f"pixelrect: {e}", pos)

        dst_reg = self._alloc_var(name, pos)
        self._types[name] = "snap"

        if "sys" not in self.activated_namespaces:
            self.activated_namespaces.append("sys")
        sys_id = self.activated_namespaces.index("sys")
        const_id = self.runtime.get_fn_id("sys", "const")
        self._emit(OP_CALL_EXT, sys_id, const_id, 1)
        self._emit_typed((TAG_IMM, cid), 4)

        done_label = Label("pixelrect_done")
        self._emit_bra(CC_OPS["="], REG_ERR, (TAG_IMM, 0), done_label)

        self._emit(OP_SET, dst_reg)
        self._emit(TAG_REG, REG_RET)

        done_label.mark(self)
        self._ret_type = "snap"
