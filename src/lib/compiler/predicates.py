# Predicates, conditional branching (\\if), and guard instructions (\\guarded) for MYCRO compiler
from .label import Label
from .syntax import _split_compound_group
from bytecode import (
    TAG_IMM,
    TAG_REG,
    OP_BRA,
    OP_JMP,
    OP_GUARD_ENTER,
    GUARD_TAG_IMM,
    GUARD_TAG_REG,
    GUARD_TAG_SLOT,
    CC_OPS,
    CONVERSE_CC,
    MAX_RUNTIME_NEST,
    MAX_PRED_NEST,
    MAX_GUARD_NEST,
    MacroError,
)


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


class PredicateMixin:
    """Mixin for parsing predicates, conditional branching (\\if), and \\guarded blocks."""

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
        # Step 1: Ephemeral Slot Pre-Loading in source order (§5.4 #1)
        lhs_val = self._parse_value(node.lhs_str, node.pos)
        rhs_reserved = (lhs_val[1],) if lhs_val[0] == TAG_REG else ()
        rhs_val = self._parse_value(node.rhs_str, node.pos, reserved=rhs_reserved)
        cc = node.cc

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
        # Handle fault at runtime: OP_BRA evaluates to FALSE and sets $err = ERR_HANDLE_MISUSE
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
        if self._runtime_depth >= MAX_RUNTIME_NEST:
            raise MacroError(f"guard nesting exceeds {MAX_RUNTIME_NEST} levels", pos)
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
            if t_str.startswith("-"):
                raise MacroError("negative duration not allowed", start)
            throttle_ms = self._parse_duration_literal(t_str, start)
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
            raise MacroError("pixelrect requires destination statement", pos)
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
                self._check_slot_namespace(ns, sig, pos)
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
