# Key, mouse, math, extension, and loop command lowering for MYCRO compiler
import keymap
from keymap import (
    MINUS,
    EQUALS,
    SHIFT,
    letter_keycode,
    digit_keycode,
)
from .label import Label
from bytecode import (
    TAG_IMM,
    TAG_REG,
    TAG_STR,
    OP_CHAR,
    OP_KEY,
    OP_SLEEP,
    OP_MCLICK,
    OP_MMOVE,
    OP_PACE,
    OP_CONSUMER_KEY,
    OP_LOOP,
    OP_LOOP_END,
    OP_KEY_DOWN,
    OP_KEY_UP,
    OP_MDOWN,
    OP_MUP,
    OP_SET,
    OP_ADD,
    OP_SUB,
    OP_WAIT,
    OP_CALL_EXT,
    OP_TYPEREG,
    REG_RET,
    REG_TIMEOUT,
    MAX_COMBO_KEYS,
    MAX_RUNTIME_NEST,
    DEFAULT_WAIT_TIMEOUT_MS,
    _PRINTABLE_LONE_KEYS,
    KEYS,
    MODS,
    MOUSE_BTNS,
    CONSUMER_COMMANDS,
    MacroError,
    _is_alpha,
    _is_alnum,
)


class ActionsMixin:
    """Mixin for keyboard, mouse, variable mutations, extensions, and flow control commands."""

    KEYS = KEYS
    MODS = MODS
    MOUSE_BTNS = MOUSE_BTNS

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

    def _cmd_delay(self, pos):
        r"""\delay{t} — one-shot sleep of t seconds ($var = ms)."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("delay requires {value}", self._pos)
        v = self._parse_time(arg, pos)
        self._emit(OP_SLEEP)
        self._emit_typed(v, 2)

    def _cmd_pace(self, pos):
        r"""\pace{t} — set the delay inserted after every following action."""
        arg = self._read_braces()
        if arg is None:
            raise MacroError("pace requires {value}", self._pos)
        v = self._parse_time(arg, pos)
        self._emit(OP_PACE)
        self._emit_typed(v, 2)

    def _cmd_set(self, pos):
        r"""\set{x}{val} — assign a variable (spec/dsl.md §6.1)."""
        name = self._read_braces()
        if name is None:
            raise MacroError("set requires {name}", self._pos)
        name = name.strip().lower()
        self._validate_var_name(name, pos)

        arg = self._read_braces()
        if arg is None:
            raise MacroError("set requires {value}", self._pos)
        s = arg.strip()
        if self._is_constructor(s):
            raise MacroError("pixelrect requires destination statement", pos)

        # Parse value BEFORE declaring name, so \set{x}{$x} sees x as undefined!
        v = self._parse_value(arg, pos)

        reg = self._alloc_var(name, pos)
        self._emit(OP_SET, reg)
        self._emit_typed(v, 4)
        if v[0] == TAG_REG:
            self._types[name] = self._reg_type(v[1])
        else:
            self._types[name] = "int"

    def _cmd_add(self, pos):
        r"""\add{x}{val} — add to an existing variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("add requires {name}", self._pos)
        name = name.strip().lower()
        if "." in name:
            raise MacroError("add: cannot modify a signal slot", pos)
        if name in ("ret", "timeout", "err"):
            raise MacroError("add: cannot modify a special register", pos)
        if name not in self._vars:
            raise MacroError(f"Undefined variable: {name}", pos)
        reg = self._vars[name]
        arg = self._read_braces()
        if arg is None:
            raise MacroError("add requires {value}", self._pos)
        s = arg.strip()
        if self._is_constructor(s):
            raise MacroError("pixelrect requires destination statement", pos)
        v = self._parse_value(arg, pos, reserved=(reg,))
        self._emit(OP_ADD, reg)
        self._emit_typed(v, 4)

    def _cmd_sub(self, pos):
        r"""\sub{x}{val} — subtract from an existing variable."""
        name = self._read_braces()
        if name is None:
            raise MacroError("sub requires {name}", self._pos)
        name = name.strip().lower()
        if "." in name:
            raise MacroError("sub: cannot modify a signal slot", pos)
        if name in ("ret", "timeout", "err"):
            raise MacroError("sub: cannot modify a special register", pos)
        if name not in self._vars:
            raise MacroError(f"Undefined variable: {name}", pos)
        reg = self._vars[name]
        arg = self._read_braces()
        if arg is None:
            raise MacroError("sub requires {value}", self._pos)
        s = arg.strip()
        if self._is_constructor(s):
            raise MacroError("pixelrect requires destination statement", pos)
        v = self._parse_value(arg, pos, reserved=(reg,))
        self._emit(OP_SUB, reg)
        self._emit_typed(v, 4)

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
        if self._nest_stack:
            raise MacroError("use only allowed at top level", pos)
        arg = self._read_braces()
        if arg is None:
            raise MacroError("use requires {ns}", self._pos)
        ns = arg.strip()
        if not ns or "." in ns or not all(_is_alnum(c) or c == "_" for c in ns):
            raise MacroError(f"Invalid namespace: {ns}", pos)
        if self.runtime is None:
            raise MacroError("use requires a runtime registry", pos)
        if not self.runtime.has_namespace(ns):
            raise MacroError(f"Unknown namespace: {ns}", pos)
        self._declared_namespaces.add(ns)
        if ns not in self.activated_namespaces:
            self.activated_namespaces.append(ns)

    def _cmd_call(self, pos):
        r"""\call{ns.fn}{arg}... — invoke a runtime extension function."""
        target = self._read_braces()
        if target is None:
            raise MacroError("call requires {ns.fn}", self._pos)
        target = target.strip()
        if target == "sys.const":
            raise MacroError("sys.const is reserved for compiler lowering", pos)
        parts = target.split(".")
        if len(parts) != 2 or not all(parts):
            raise MacroError(f"call target must be ns.fn: {target}", pos)
        ns, fn = parts
        if self.runtime is None:
            raise MacroError("call requires a runtime registry", pos)
        if ns not in self._declared_namespaces:
            if not self.runtime.has_namespace(ns):
                raise MacroError(f"Unknown namespace: {ns}", pos)
            raise MacroError(f"undeclared namespace: {ns}", pos)
        fid = self.runtime.get_fn_id(ns, fn)
        if fid is None:
            raise MacroError(f"Unknown ext function: {target}", pos)
        sig = self.runtime.get_signature(ns, fn)
        ret_type = self.runtime.get_return_type(ns, fn)

        consumer_skip = Label("consumer_skip")
        args = []
        for typ in sig:
            self._skip_absorbed_whitespace()
            if self._peek() != "{":
                raise MacroError(f"{target} expects {len(sig)} arg(s)", self._pos)
            s = self._read_braces()
            reserved = tuple(v for tag, v in args if tag == TAG_REG)
            parsed = self._parse_ext_arg(
                s, typ, target, pos, reserved=reserved, consumer_skip=consumer_skip
            )
            if isinstance(parsed, list):
                args.extend(parsed)
            else:
                args.append(parsed)
        if self._peek() == "{":
            raise MacroError(f"{target} expects {len(sig)} arg(s)", self._pos)

        ns_id = self.activated_namespaces.index(ns)
        fn_id = fid
        self._emit(OP_CALL_EXT, ns_id, fn_id, len(args))
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

    def _parse_ext_arg(self, a, typ, target, pos, reserved=(), consumer_skip=None):
        r"""Compile one ext arg per its signature type. point/rect/span return a
        LIST of typed operands (compound value in one brace group, e.g.
        {x,y} / {x,y,w,h} / {interval,duration}); all other types
        return a single (tag, value) pair."""
        s = a.strip()
        if self._is_constructor(s):
            raise MacroError("pixelrect requires destination statement", pos)

        if typ in ("point", "rect", "span"):
            n = 4 if typ == "rect" else 2
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
        try:
            ms = self._parse_duration_literal(s, pos)
        except MacroError as e:
            if e.msg == "integer out of i32 range":
                raise MacroError("wait timeout out of i32 range", pos)
            if e.msg == "fractional number requires unit 's'":
                raise
            raise MacroError(f"Invalid wait timeout: {s}", pos)
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
        self._check_slot_namespace(ns, sig, pos)

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

        if self._runtime_depth >= MAX_RUNTIME_NEST:
            raise MacroError(f"rep nesting exceeds {MAX_RUNTIME_NEST} levels", pos)
        self._nest_into(body, "rep body", ("rep", count))

    def _cmd_kdown(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kdown requires {key}", self._pos)
        arg = arg.strip()
        if not arg:
            return  # No selected keys: pressing nothing is a no-op.
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
        for kc in K:
            self._emit(OP_KEY_DOWN)
            self._emit_typed((TAG_IMM, kc))

    def _cmd_kup(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("kup requires {key} or {}", self._pos)
        arg = arg.strip()
        if not arg:
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
        if len(parts) not in (1, 2):
            raise MacroError("click requires {btn} or {btn,count}", pos)
        btn = self._parse_btn_arg(parts[0], pos)
        count = (TAG_IMM, 1)
        if len(parts) == 2:
            count = self._parse_value(parts[1], pos)
            if count[0] == TAG_IMM and not 1 <= count[1] <= 65535:
                raise MacroError("click count out of range [1, 65535]", pos)
        self._emit(OP_MCLICK)
        self._emit_typed((TAG_IMM, btn))
        self._emit_typed(count)

    def _cmd_move(self, pos):
        arg = self._read_braces()
        if arg is None:
            raise MacroError("move requires {dx,dy}", self._pos)
        parts = arg.split(",")
        if len(parts) != 2:
            raise MacroError("move requires {dx,dy}", pos)
        dx = self._parse_value(parts[0], pos)
        if dx[0] == TAG_IMM and not -32768 <= dx[1] <= 32767:
            raise MacroError("move component out of i16 range", pos)
        dy = self._parse_value(
            parts[1], pos, reserved=(dx[1],) if dx[0] == TAG_REG else ())
        if dy[0] == TAG_IMM and not -32768 <= dy[1] <= 32767:
            raise MacroError("move component out of i16 range", pos)

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
