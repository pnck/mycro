# Bytecode Virtual Machine and Disassembler for MYCRO.
# Normative reference: spec/vm.md (execution semantics, registers, pacing)

import asyncio
import time

import keymap
from keymap import BTN_ALL
from bytecode import (
    VERSION,
    OP_CHAR, OP_KEY, OP_COMBO, OP_SLEEP, OP_MCLICK, OP_MMOVE, OP_PACE,
    OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP, OP_MDOWN, OP_MUP,
    OP_SET, OP_ADD, OP_SUB, OP_BRA, OP_JMP, OP_GUARD_ENTER, OP_GUARD_EXIT, OP_TYPEREG,
    OP_READSLOT, OP_WAIT, OP_CALL_EXT, OP_END,
    TAG_IMM, TAG_REG, TAG_STR,
    REG_RET, REG_TIMEOUT, REG_ERR,
    ERR_OK, ERR_HANDLE_MISUSE,
    MAX_GUARD_NEST, DELAY_SLICE_QUANTUM,
    GUARD_TAG_IMM, GUARD_TAG_REG, GUARD_TAG_SLOT,
    CC_NAMES,
    read_header, keycode_name, mod_names,
)

try:
    _TimeoutError = asyncio.TimeoutError
except AttributeError:
    _TimeoutError = TimeoutError


class VM:
    """Bytecode execution engine and disassembler."""

    def __init__(self, hid=None, runtime=None):
        self.hid = hid
        self.runtime = runtime

    @staticmethod
    def _read_header(bc):
        return read_header(bc)

    def disassemble(self, bytecode):
        """Disassemble bytecode to human-readable format."""
        strs, i = self._read_header(bytecode)
        lines = [f"; bytecode v{VERSION}, {len(bytecode)}B, {len(strs)} str(s)"]
        indent = 0

        def fmt_reg(r):
            if r == REG_RET:
                return "$ret"
            if r == REG_TIMEOUT:
                return "$timeout"
            if r == REG_ERR:
                return "$err"
            return f"$r{r}"

        def fmt_arg(width=4, signed=False):
            nonlocal i
            tag = bytecode[i]
            i += 1
            if tag == TAG_REG:
                r = bytecode[i]
                i += 1
                return fmt_reg(r)
            if tag == TAG_IMM:
                v = (
                    bytecode[i]
                    | (bytecode[i + 1] << 8)
                    | (bytecode[i + 2] << 16)
                    | (bytecode[i + 3] << 24)
                )
                i += 4
                if signed and v > 2147483647:
                    v -= 4294967296
                return str(v)
            return f"INVALID_TAG_{tag}"

        def fmt_key_arg():
            nonlocal i
            tag = bytecode[i]
            i += 1
            if tag == TAG_REG:
                r = f"$r{bytecode[i]}"
                i += 1
                return r
            if tag == TAG_IMM:
                kc = (
                    bytecode[i]
                    | (bytecode[i + 1] << 8)
                    | (bytecode[i + 2] << 16)
                    | (bytecode[i + 3] << 24)
                )
                i += 4
                if kc == 0:
                    return "ALL"
                return keycode_name(kc)
            return f"INVALID_TAG_{tag}"

        def fmt_btn_arg():
            nonlocal i
            tag = bytecode[i]
            i += 1
            if tag == TAG_REG:
                r = f"$r{bytecode[i]}"
                i += 1
                return r
            if tag == TAG_IMM:
                b = (
                    bytecode[i]
                    | (bytecode[i + 1] << 8)
                    | (bytecode[i + 2] << 16)
                    | (bytecode[i + 3] << 24)
                )
                i += 4
                names = {1: "LEFT", 2: "RIGHT", 4: "MIDDLE", 8: "BACK", 16: "FORWARD", 0x1F: "ALL"}
                return names.get(b, str(b))
            return f"INVALID_TAG_{tag}"

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
                key_name = keycode_name(bytecode[i + 1])
                lines.append(f"{prefix}KEY_TAP {key_name}")
                i += 2
            elif op == OP_COMBO:
                mods = mod_names(bytecode[i + 1])
                key_name = keycode_name(bytecode[i + 2])
                lines.append(f"{prefix}COMBO (NOP) {mods}+{key_name}")
                i += 3
            elif op == OP_SLEEP:
                i += 1
                lines.append(f"{prefix}SLEEP {fmt_arg(2)}ms")
            elif op == OP_PACE:
                i += 1
                lines.append(f"{prefix}PACE {fmt_arg(2)}ms")
            elif op == OP_MCLICK:
                i += 1
                btn = fmt_btn_arg()
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
                lines.append(f"{prefix}KEY_DOWN {fmt_key_arg()}")
            elif op == OP_KEY_UP:
                i += 1
                lines.append(f"{prefix}KEY_UP {fmt_key_arg()}")
            elif op == OP_MDOWN:
                i += 1
                lines.append(f"{prefix}MDOWN {fmt_btn_arg()}")
            elif op == OP_MUP:
                i += 1
                lines.append(f"{prefix}MUP {fmt_btn_arg()}")
            elif op == OP_SET:
                reg = bytecode[i + 1]
                i += 2
                lines.append(f"{prefix}SET {fmt_reg(reg)}, {fmt_arg(4)}")
            elif op == OP_ADD:
                reg = bytecode[i + 1]
                i += 2
                lines.append(f"{prefix}ADD {fmt_reg(reg)}, {fmt_arg(4)}")
            elif op == OP_SUB:
                reg = bytecode[i + 1]
                i += 2
                lines.append(f"{prefix}SUB {fmt_reg(reg)}, {fmt_arg(4)}")
            elif op == OP_BRA:
                cc = bytecode[i + 1]
                reg = bytecode[i + 2]
                i += 3
                rhs = fmt_arg(4)
                off = bytecode[i] | (bytecode[i + 1] << 8)
                i += 2
                cc_name = CC_NAMES[cc] if cc < len(CC_NAMES) else f"0x{cc:02X}"
                lines.append(f"{prefix}BRA {fmt_reg(reg)} {cc_name} {rhs} +{off}")
            elif op == OP_JMP:
                off = bytecode[i + 1] | (bytecode[i + 2] << 8)
                lines.append(f"{prefix}JMP +{off}")
                i += 3
            elif op == OP_GUARD_ENTER:
                abort_off = bytecode[i + 1] | (bytecode[i + 2] << 8)
                header = bytecode[i + 3]
                logic_mode = (header >> 7) & 1
                triplet_cnt = ((header >> 4) & 7) + 1
                has_throttle = header & 1
                curr = i + 4
                throttle_str = ""
                if has_throttle:
                    throttle_ms = bytecode[curr] | (bytecode[curr + 1] << 8)
                    curr += 2
                    throttle_str = f" [{throttle_ms}ms]"
                mode_str = "ANY" if logic_mode == 1 else "ALL"
                lines.append(f"{prefix}GUARD_ENTER abort_off=+{abort_off}{throttle_str} {mode_str}({triplet_cnt})")
                for _ in range(triplet_cnt):
                    spec = bytecode[curr]
                    curr += 1
                    lhs_tag = (spec >> 3) & 3
                    rhs_tag = (spec >> 5) & 3
                    curr += 4 if lhs_tag == 0 else (1 if lhs_tag == 1 else 2)
                    curr += 4 if rhs_tag == 0 else (1 if rhs_tag == 1 else 2)
                i = curr
            elif op == OP_GUARD_EXIT:
                lines.append(f"{prefix}GUARD_EXIT")
                i += 1
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
                field = strs[bytecode[i + 2]]
                i += 3
                lines.append(f"{prefix}WAIT $slot.{sig}.{field} {fmt_arg(4)}ms")
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

    async def execute(self, bytecode, default_delay_ms=50, click_hold_ms=10):
        """Execute compiled bytecode.

        On exception or cancellation/abort, emergency-releases all keys and
        mouse buttons (B2). On normal completion, keys/buttons held via
        KEY_DOWN/MDOWN remain held until an explicit KEY_UP/MUP or abort.
        """
        bc = bytecode
        n = len(bc)
        strs, ip = self._read_header(bc)
        delay_ms = default_delay_ms
        loop_stack = []  # [(ip_start, remaining, saved_delay)]
        regs = [0] * 256
        guard_slots = [None] * MAX_GUARD_NEST
        guard_depth = 0
        active_consumer_usage = None
        hid = self.hid
        runtime = self.runtime
        if runtime is not None:
            runtime.release_all()  # execution bracket: start (defensive)

        class _GuardAbort(Exception):
            pass

        def get_now_ms():
            return int(time.monotonic() * 1000)

        def eval_guard_triplet(triplet):
            cc, lhs_tag, lhs_val, rhs_tag, rhs_val = triplet

            if lhs_tag == GUARD_TAG_IMM:
                v_lhs = lhs_val
            elif lhs_tag == GUARD_TAG_REG:
                if runtime is not None and runtime.is_handle(lhs_val):
                    runtime.reg_write(REG_ERR, ERR_HANDLE_MISUSE, None, False)
                    regs[REG_ERR] = ERR_HANDLE_MISUSE
                    return False
                v_lhs = regs[lhs_val]
            elif lhs_tag == GUARD_TAG_SLOT:
                sig = strs[lhs_val[0]]
                field = strs[lhs_val[1]]
                v_lhs = runtime.read_slot(sig, field) if runtime is not None else 0
            else:
                return False

            if rhs_tag == GUARD_TAG_IMM:
                v_rhs = rhs_val
            elif rhs_tag == GUARD_TAG_REG:
                if runtime is not None and runtime.is_handle(rhs_val):
                    runtime.reg_write(REG_ERR, ERR_HANDLE_MISUSE, None, False)
                    regs[REG_ERR] = ERR_HANDLE_MISUSE
                    return False
                v_rhs = regs[rhs_val]
            elif rhs_tag == GUARD_TAG_SLOT:
                sig = strs[rhs_val[0]]
                field = strs[rhs_val[1]]
                v_rhs = runtime.read_slot(sig, field) if runtime is not None else 0
            else:
                return False

            if cc == 0:
                return v_lhs == v_rhs
            elif cc == 1:
                return v_lhs != v_rhs
            elif cc == 2:
                return v_lhs < v_rhs
            elif cc == 3:
                return v_lhs <= v_rhs
            elif cc == 4:
                return v_lhs > v_rhs
            elif cc == 5:
                return v_lhs >= v_rhs
            return False

        def eval_guard_pred(pred_desc):
            logic_mode, triplets = pred_desc
            if logic_mode == 0:  # ALL
                for t in triplets:
                    if not eval_guard_triplet(t):
                        return False
                return True
            else:  # ANY
                for t in triplets:
                    if eval_guard_triplet(t):
                        return True
                return False

        def abort_guard(level):
            nonlocal ip, guard_depth, active_consumer_usage
            if hid is not None:
                hid.keys_release_all()
                hid.mouse_release(0x1F)
                if hasattr(hid, "consumer_release") and active_consumer_usage is not None:
                    hid.consumer_release(active_consumer_usage)
                    active_consumer_usage = None
            target_loop_depth = guard_slots[level][3]
            del loop_stack[target_loop_depth:]
            abort_ip = guard_slots[level][2]
            ip = abort_ip
            for k in range(level, guard_depth):
                guard_slots[k] = None
            guard_depth = level
            raise _GuardAbort()

        def check_guards():
            now = get_now_ms()
            for i in range(guard_depth):
                slot = guard_slots[i]
                throttle_ms = slot[0]
                last_check_ms = slot[1]
                if throttle_ms == 0 or (now - last_check_ms) >= (throttle_ms - 1):
                    slot[1] = now
                    if not eval_guard_pred(slot[4]):
                        abort_guard(i)

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

        def read_argx(width=4, signed=False):
            """Typed operand with source identity (register-write
            mediation needs to know when a value comes from a register)."""
            tag = read_u8()
            if tag == TAG_REG:
                r = read_u8()
                return regs[r], r
            if tag == TAG_IMM:
                return read_i32(), None
            raise RuntimeError(f"Invalid operand tag: {tag}")

        def read_arg(width=4, signed=False):
            """Typed operand: TAG_REG reads a register, TAG_IMM reads inline."""
            return read_argx(width, signed)[0]

        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        async def sleep_ms(ms):
            nonlocal ip, guard_depth
            if ms <= 0:
                return
            if guard_depth == 0:
                await asyncio.sleep(ms / 1000.0)
                return
            rem = ms
            while rem > 0:
                slice_quantum = min(
                    (slot[0] if slot[0] > 0 else DELAY_SLICE_QUANTUM)
                    for slot in guard_slots[:guard_depth]
                )
                dt = min(rem, slice_quantum)
                await asyncio.sleep(dt / 1000.0)
                rem -= dt
                check_guards()

        try:
            while ip < n:
                try:
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
                        for kc in reversed(codes):
                            hid.key_release(kc)
                        await sleep_ms(delay_ms)

                    elif op == OP_KEY:
                        kc = read_u8()
                        hid.key_press(kc)
                        await sleep_ms(click_hold_ms)
                        hid.key_release(kc)
                        await sleep_ms(delay_ms)

                    elif op == OP_COMBO:
                        read_u8()
                        read_u8()

                    elif op == OP_SLEEP:
                        # One-shot sleep (DSL \delay)
                        await sleep_ms(clamp(read_arg(), 0, 65535))

                    elif op == OP_PACE:
                        # Set inter-action delay for following ops (DSL \pace)
                        delay_ms = clamp(read_arg(), 0, 65535)

                    elif op == OP_MCLICK:
                        btn = read_arg() & 0xFF
                        count = clamp(read_arg(), 0, 65535)
                        for i in range(count):
                            hid.mouse_press(btn)
                            await sleep_ms(click_hold_ms)
                            hid.mouse_release(btn)
                            if i < count - 1:
                                await sleep_ms(delay_ms)
                        await sleep_ms(delay_ms)

                    elif op == OP_MMOVE:
                        dx = clamp(read_arg(signed=True), -32767, 32767)
                        dy = clamp(read_arg(signed=True), -32767, 32767)
                        hid.mouse_move(dx, dy)
                        await sleep_ms(delay_ms)

                    elif op == OP_LOOP:
                        count = clamp(read_arg(), 0, 255)
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
                        hid.key_press(read_arg() & 0xFF)

                    elif op == OP_KEY_UP:
                        key = read_arg() & 0xFF
                        if key == 0:
                            hid.keys_release_all()
                        else:
                            hid.key_release(key)

                    elif op == OP_MDOWN:
                        hid.mouse_press(read_arg() & 0xFF)

                    elif op == OP_MUP:
                        btn = read_arg() & 0xFF
                        hid.mouse_release(btn)

                    elif op == OP_SET:
                        reg = read_u8()
                        val, src = read_argx(4)
                        is_h = False
                        if runtime is not None:
                            if src is not None and runtime.is_handle(src):
                                is_h = True
                            runtime.reg_write(reg, val, src, is_h)
                        regs[reg] = val

                    elif op == OP_ADD:
                        reg = read_u8()
                        val, src = read_argx(4)
                        is_reg_h = runtime is not None and runtime.is_handle(reg)
                        is_src_h = runtime is not None and src is not None and runtime.is_handle(src)
                        if is_reg_h or is_src_h:
                            if runtime is not None:
                                runtime.reg_write(REG_ERR, ERR_HANDLE_MISUSE, None, False)
                            regs[REG_ERR] = ERR_HANDLE_MISUSE
                        else:
                            u = (regs[reg] + val) % 4294967296
                            wrapped = u if u < 2147483648 else u - 4294967296
                            if runtime is not None:
                                runtime.reg_write(reg, wrapped, None, False)
                            regs[reg] = wrapped

                    elif op == OP_SUB:
                        reg = read_u8()
                        val, src = read_argx(4)
                        is_reg_h = runtime is not None and runtime.is_handle(reg)
                        is_src_h = runtime is not None and src is not None and runtime.is_handle(src)
                        if is_reg_h or is_src_h:
                            if runtime is not None:
                                runtime.reg_write(REG_ERR, ERR_HANDLE_MISUSE, None, False)
                            regs[REG_ERR] = ERR_HANDLE_MISUSE
                        else:
                            u = (regs[reg] - val) % 4294967296
                            wrapped = u if u < 2147483648 else u - 4294967296
                            if runtime is not None:
                                runtime.reg_write(reg, wrapped, None, False)
                            regs[reg] = wrapped

                    elif op == OP_BRA:
                        cc = read_u8()
                        reg_a = read_u8()
                        rhs, src_rhs = read_argx(4)
                        off = read_u16()
                        is_a_h = runtime is not None and runtime.is_handle(reg_a)
                        is_rhs_h = runtime is not None and src_rhs is not None and runtime.is_handle(src_rhs)
                        if is_a_h or is_rhs_h:
                            if runtime is not None:
                                runtime.reg_write(REG_ERR, ERR_HANDLE_MISUSE, None, False)
                            regs[REG_ERR] = ERR_HANDLE_MISUSE
                            cond = False
                        else:
                            a = regs[reg_a]
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

                    elif op == OP_GUARD_ENTER:
                        abort_off = read_u16()
                        header = read_u8()
                        logic_mode = (header >> 7) & 1
                        triplet_cnt = ((header >> 4) & 7) + 1
                        has_throttle = header & 1
                        throttle_ms = read_u16() if has_throttle else 0

                        triplets = []
                        for _ in range(triplet_cnt):
                            spec = read_u8()
                            cc = spec & 7
                            lhs_tag = (spec >> 3) & 3
                            rhs_tag = (spec >> 5) & 3
                            if lhs_tag == GUARD_TAG_IMM:
                                lhs_val = read_i32()
                            elif lhs_tag == GUARD_TAG_REG:
                                lhs_val = read_u8()
                            elif lhs_tag == GUARD_TAG_SLOT:
                                lhs_val = (read_u8(), read_u8())
                            else:
                                raise RuntimeError(f"Invalid guard lhs tag: {lhs_tag}")

                            if rhs_tag == GUARD_TAG_IMM:
                                rhs_val = read_i32()
                            elif rhs_tag == GUARD_TAG_REG:
                                rhs_val = read_u8()
                            elif rhs_tag == GUARD_TAG_SLOT:
                                rhs_val = (read_u8(), read_u8())
                            else:
                                raise RuntimeError(f"Invalid guard rhs tag: {rhs_tag}")

                            triplets.append((cc, lhs_tag, lhs_val, rhs_tag, rhs_val))

                        base_point = ip
                        abort_ip = base_point + abort_off
                        loop_depth = len(loop_stack)
                        pred_desc = (logic_mode, triplets)

                        if guard_depth >= MAX_GUARD_NEST:
                            raise RuntimeError(f"guard nesting exceeds {MAX_GUARD_NEST} levels")

                        guard_slots[guard_depth] = [throttle_ms, 0, abort_ip, loop_depth, pred_desc]
                        guard_depth += 1

                        # Entry inspection: evaluate every active guard from outermost to innermost
                        now = get_now_ms()
                        for i in range(guard_depth):
                            guard_slots[i][1] = now
                            if not eval_guard_pred(guard_slots[i][4]):
                                abort_guard(i)

                    elif op == OP_GUARD_EXIT:
                        if guard_depth > 0:
                            guard_depth -= 1
                            guard_slots[guard_depth] = None

                    elif op == OP_TYPEREG:
                        # Render register as decimal keystrokes (DSL \typenum);
                        # a handle operand renders empty output and sets $err = ERR_HANDLE_MISUSE
                        reg = read_u8()
                        if runtime is not None and runtime.is_handle(reg):
                            runtime.reg_write(REG_ERR, ERR_HANDLE_MISUSE, None, False)
                            regs[REG_ERR] = ERR_HANDLE_MISUSE
                        else:
                            for ch in str(regs[reg]):
                                codes = hid.char_keycodes(ch)
                                if codes:
                                    for kc in codes:
                                        hid.key_press(kc)
                                    await sleep_ms(click_hold_ms)
                                    for kc in reversed(codes):
                                        hid.key_release(kc)
                            await sleep_ms(delay_ms)

                    elif op == OP_READSLOT:
                        reg = read_u8()
                        sig = strs[read_u8()]
                        field = strs[read_u8()]
                        if runtime is None:
                            raise RuntimeError("READSLOT without runtime registry")
                        val = runtime.read_slot(sig, field)
                        runtime.reg_write(reg, val, None, False)
                        regs[reg] = val

                    elif op == OP_WAIT:
                        sig = strs[read_u8()]
                        field = strs[read_u8()]
                        timeout = read_arg(4)
                        if runtime is None:
                            raise RuntimeError("WAIT without runtime registry")

                        if guard_depth == 0:
                            ok = await runtime.wait_slot(sig, field, timeout)
                            status = 0 if ok else 1
                            runtime.reg_write(REG_TIMEOUT, status, None, False)
                            regs[REG_TIMEOUT] = status
                        else:
                            # §6.2 #4: Initial poll check before arrival or timeout processing
                            check_guards()

                            if runtime.slot_pending(sig, field) == 1:
                                runtime.consume_slot_pending(sig, field)
                                runtime.reg_write(REG_TIMEOUT, 0, None, False)
                                regs[REG_TIMEOUT] = 0
                            elif timeout <= 0:
                                runtime.reg_write(REG_TIMEOUT, 1, None, False)
                                regs[REG_TIMEOUT] = 1
                            else:
                                ev = asyncio.Event()
                                runtime.add_waiter(sig, field, ev)
                                start_time = get_now_ms()
                                rem_timeout = timeout
                                try:
                                    while rem_timeout > 0:
                                        slice_quantum = min(
                                            (s[0] if s[0] > 0 else DELAY_SLICE_QUANTUM)
                                            for s in guard_slots[:guard_depth]
                                        )
                                        dt = min(rem_timeout, slice_quantum)
                                        try:
                                            await asyncio.wait_for(ev.wait(), dt / 1000.0)
                                        except _TimeoutError:
                                            pass
                                        now = get_now_ms()
                                        rem_timeout = max(0, timeout - (now - start_time))

                                        # Slice wake-up: evaluate guards BEFORE arrival or timeout
                                        for i in range(guard_depth):
                                            slot = guard_slots[i]
                                            throttle_ms = slot[0]
                                            last_check_ms = slot[1]
                                            if throttle_ms == 0 or (now - last_check_ms) >= (throttle_ms - 1):
                                                slot[1] = now
                                                if not eval_guard_pred(slot[4]):
                                                    runtime.remove_waiter(sig, field, ev)
                                                    abort_guard(i)

                                        if runtime.slot_pending(sig, field) == 1:
                                            runtime.consume_slot_pending(sig, field)
                                            runtime.remove_waiter(sig, field, ev)
                                            runtime.reg_write(REG_TIMEOUT, 0, None, False)
                                            regs[REG_TIMEOUT] = 0
                                            break

                                        if rem_timeout <= 0:
                                            runtime.remove_waiter(sig, field, ev)
                                            if runtime.slot_pending(sig, field) == 1:
                                                runtime.consume_slot_pending(sig, field)
                                                runtime.reg_write(REG_TIMEOUT, 0, None, False)
                                                regs[REG_TIMEOUT] = 0
                                            else:
                                                runtime.reg_write(REG_TIMEOUT, 1, None, False)
                                                regs[REG_TIMEOUT] = 1
                                            break
                                finally:
                                    runtime.remove_waiter(sig, field, ev)

                    elif op == OP_CALL_EXT:
                        if guard_depth > 0:
                            check_guards()
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
                        if runtime is None:
                            raise RuntimeError("CALL_EXT without runtime registry")
                        sig_entry = runtime._ns.get(ns, {}).get(fn)
                        ret_type = sig_entry[1] if sig_entry else "int"

                        r = runtime.call(ns, fn, args)
                        if hasattr(r, "send"):  # coroutine: await it
                            r = await r

                        if isinstance(r, tuple):
                            ret_val, err_val = r
                        else:
                            ret_val = r
                            err_val = 0

                        if err_val != 0 or ret_val is None:
                            ret_val = 0
                            is_handle = False
                        else:
                            try:
                                ret_val = int(ret_val)
                            except (TypeError, ValueError):
                                ret_val = 0
                            is_handle = (ret_type not in ("int", "color", "void") and ret_val > 0)

                        runtime.reg_write(REG_ERR, err_val, None, False)
                        regs[REG_ERR] = err_val
                        runtime.reg_write(REG_RET, ret_val, None, is_handle)
                        regs[REG_RET] = ret_val
                        runtime.claim(REG_RET, ret_val)

                    else:
                        raise RuntimeError(f"Unknown opcode 0x{op:02X}")

                    # Instruction boundary guard check (§6.2 #2)
                    if op != OP_GUARD_ENTER and guard_depth > 0:
                        check_guards()

                except _GuardAbort:
                    continue

            return True
        except BaseException:
            # B2: on error, exception, or cancellation/abort, emergency-release all keys/buttons
            if hid is not None:
                hid.keys_release_all()
                hid.mouse_release(BTN_ALL)
            raise
        finally:
            guard_slots = [None] * MAX_GUARD_NEST
            guard_depth = 0
            if runtime is not None:
                runtime.release_all()  # execution bracket: exit

