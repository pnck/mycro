r"""Stage B: variables / \ifnum / \val / typed operands / bytecode v3.

Compile tests assert exact bytecode layout (including backpatched branch
offsets); execute tests verify runtime semantics through MockHID events.
"""

import asyncio

import keymap
from macro import (
    Macro,
    MAGIC, VERSION, TAG_IMM, TAG_REG, REG_RET, REG_TIMEOUT, REG_ERR,
    OP_CHAR, OP_SLEEP, OP_PACE, OP_MCLICK, OP_MMOVE, OP_LOOP, OP_LOOP_END,
    OP_KEY_DOWN, OP_KEY_UP, OP_SET, OP_ADD, OP_SUB, OP_BRA, OP_JMP, OP_TYPEREG, OP_END,
)
from mock_hid import MockHID

H = bytes([MAGIC, VERSION, 0])


def compile_ok(text):
    bc, err = Macro(MockHID()).compile(text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text):
    bc, err = Macro(MockHID()).compile(text)
    assert bc is None and err, f"expected compile error, got success: {text}"
    return err


def run(text, hid=None):
    hid = hid or MockHID()
    m = Macro(hid)
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    return hid


def key_events(hid, kc):
    return [e for e in hid.events if e == ("kp", kc)]


# --- set/add emission ---

def test_set_imm_layout():
    bc = compile_ok("\\set{x}{5}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 5, 0, 0, 0, OP_END])


def test_set_hex_layout():
    bc = compile_ok("\\set{x}{0xff3c00}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 0x00, 0x3C, 0xFF, 0x00, OP_END])


def test_set_negative_layout():
    bc = compile_ok("\\set{x}{-1}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 0xFF, 0xFF, 0xFF, 0xFF, OP_END])


def test_set_from_var():
    bc = compile_ok("\\set{x}{1}\\set{y}{$x}")
    assert bc[3:] == bytes([
        OP_SET, 0, TAG_IMM, 1, 0, 0, 0,
        OP_SET, 1, TAG_REG, 0,
        OP_END,
    ])


def test_add_emission():
    bc = compile_ok("\\set{x}{1}\\add{x}{-3}")
    assert bc[3 + 7 :] == bytes([OP_ADD, 0, TAG_IMM, 0xFD, 0xFF, 0xFF, 0xFF, OP_END])


def test_sub_emission():
    bc = compile_ok("\\set{x}{1}\\sub{x}{3}")
    assert bc[3 + 7 :] == bytes([OP_SUB, 0, TAG_IMM, 3, 0, 0, 0, OP_END])


def test_sub_from_var():
    bc = compile_ok("\\set{x}{10}\\set{y}{4}\\sub{x}{$y}")
    assert bc[3 + 7 * 2 :] == bytes([OP_SUB, 0, TAG_REG, 1, OP_END])


def test_reg_err_and_special_reservations():
    assert "Reserved variable name: err" in compile_err("\\set{err}{1}")
    assert "Reserved variable name: ret" in compile_err("\\set{ret}{1}")
    assert "Reserved variable name: timeout" in compile_err("\\set{timeout}{1}")
    assert "Reserved variable name: slot" in compile_err("\\set{slot}{1}")
    assert "cannot modify a special register" in compile_err("\\add{err}{1}")
    assert "cannot modify a special register" in compile_err("\\sub{err}{1}")


def test_read_err_register():
    bc = compile_ok("\\set{x}{$err}")
    assert bc == H + bytes([OP_SET, 0, TAG_REG, REG_ERR, OP_END])
    bc2 = compile_ok("\\set{x}{${err}}")
    assert bc2 == H + bytes([OP_SET, 0, TAG_REG, REG_ERR, OP_END])


def test_reg_allocation_order():
    bc = compile_ok("\\set{a}{1}\\set{b}{2}\\set{c}{3}")
    regs = [bc[i] for i in (4, 11, 18)]  # each SET is 7 bytes; reg operand at +1
    assert regs == [0, 1, 2]


# --- typed operands on existing commands ---

def test_typed_move():
    bc = compile_ok("\\set{x}{100}\\move{$x,$x}")
    assert bc[3 + 7 :] == bytes([OP_MMOVE, TAG_REG, 0, TAG_REG, 0, OP_END])


def test_typed_delay():
    bc = compile_ok("\\set{t}{50}\\delay{$t}")
    assert bc[3 + 7 :] == bytes([OP_SLEEP, TAG_REG, 0, OP_END])


def test_imm_delay_stays_imm():
    bc = compile_ok("\\delay{0.5}")
    assert bc[3:] == bytes([OP_SLEEP, TAG_IMM, 500 & 0xFF, 500 >> 8, OP_END])


def test_typed_rep_count():
    bc = compile_ok("\\set{n}{3}\\rep{$n}{a}")
    assert bc[3 + 7 :] == bytes([
        OP_LOOP, TAG_REG, 0, 3, 0,
        OP_CHAR, ord("a"), OP_LOOP_END,
        OP_END,
    ])


def test_typed_click():
    bc = compile_ok("\\set{n}{3}\\click{L,$n}")
    assert bc[3 + 7 :] == bytes([
        OP_MCLICK, TAG_IMM, keymap.BTN_LEFT, TAG_REG, 0, OP_END,
    ])


def test_kdown_var_rejected():
    err = compile_err("\\set{k}{0x28}\\kdown{$k}")
    assert "kdown/kup takes a key name, not a variable" in err


def test_mouse_btn_name_position():
    err = compile_err("\\click{$L}")
    assert "click/mdown/mup takes a button name, not a variable" in err
    err = compile_err("\\click{invalid_btn}")
    assert "Unknown mouse button: invalid_btn" in err


def test_kup_imm_still_works():
    bc = compile_ok("\\kup{}")
    assert bc[3:] == bytes([OP_KEY_UP, TAG_IMM, 0, OP_END])


def test_braced_var_form():
    bc = compile_ok("\\set{n1}{7}\\move{${n1},0}")
    assert bc[3 + 7 :] == bytes([OP_MMOVE, TAG_REG, 0, TAG_IMM, 0, 0, OP_END])


# --- \ifnum emission (backpatched offsets) ---

def test_ifnum_no_else_layout():
    bc = compile_ok("\\set{x}{1}\\ifnum{$x}{=}{1}{a}")
    bra = 3 + 7  # after header + SET
    assert bc[bra : bra + 8] == bytes([OP_BRA, 0, 0, TAG_IMM, 1, 0, 0, 0])
    off = bc[bra + 8] | (bc[bra + 9] << 8)
    then_start = bra + 10
    assert off == len(bc) - 1 - then_start  # target = END (past trailing OP_END? no:)
    assert bc[then_start : then_start + 2] == bytes([OP_CHAR, ord("a")])


def test_ifnum_else_layout():
    bc = compile_ok("\\set{x}{1}\\ifnum{$x}{=}{1}{a}{b}")
    bra = 3 + 7
    then_start = bra + 10
    bra_off = bc[bra + 8] | (bc[bra + 9] << 8)
    # then body = CHAR a (2B), then JMP (3B), else starts after JMP
    jmp_at = then_start + 2
    else_start = jmp_at + 3
    assert bra_off == else_start - then_start
    assert bc[jmp_at] == OP_JMP
    jmp_off = bc[jmp_at + 1] | (bc[jmp_at + 2] << 8)
    assert jmp_off == 2  # else body = CHAR b
    assert bc[else_start : else_start + 2] == bytes([OP_CHAR, ord("b")])


def test_ifnum_all_ops_compile():
    for op in ("=", "!=", "<", "<=", ">", ">="):
        compile_ok(f"\\set{{x}}{{1}}\\ifnum{{$x}}{{{op}}}{{2}}{{a}}")


def test_typenum_emission():
    bc = compile_ok("\\set{x}{1}\\typenum{$x}")
    assert bc[3 + 7 :] == bytes([OP_TYPEREG, 0, OP_END])


def test_typenum_special_regs():
    assert compile_ok("\\typenum{$ret}")[3:] == bytes([OP_TYPEREG, REG_RET, OP_END])
    assert compile_ok("\\typenum{$timeout}")[3:] == bytes([OP_TYPEREG, REG_TIMEOUT, OP_END])


def test_val_obsolete_and_typenum_requires_var():
    assert "Unknown" in compile_err("\\set{x}{1}\\val{$x}")
    assert "typenum requires a variable ($name)" in compile_err("\\set{x}{1}\\typenum{x}")


# --- compile-time error matrix ---

def test_err_undefined_var_read():
    assert "Undefined variable" in compile_err("\\move{$x,0}")


def test_err_set_reserved():
    assert "Reserved" in compile_err("\\set{ret}{1}")
    assert "Reserved" in compile_err("\\set{timeout}{1}")


def test_err_add_undefined():
    assert "Undefined" in compile_err("\\add{x}{1}")


def test_err_add_special_reg():
    assert "special" in compile_err("\\add{ret}{1}")


def test_err_ifnum_left_not_var():
    assert "must be a variable" in compile_err("\\ifnum{5}{=}{5}{a}")


def test_err_ifnum_bad_op():
    assert "op must be" in compile_err("\\set{x}{1}\\ifnum{$x}{==}{1}{a}")


def test_err_too_many_vars():
    text = "".join(f"\\set{{v{i}}}{{1}}" for i in range(33))  # MAX_REGS = 32
    assert "Too many variables" in compile_err(text)


def test_err_unmatched_var_brace():
    # `${` inside an unbalanced arg is caught by brace-depth counting first
    assert "Unmatched" in compile_err("\\set{x}{1}\\move{${x,0}")


def test_err_i32_range():
    assert "out of i32 range" in compile_err("\\set{x}{0x100000000}")


def test_err_dot_in_var_name():
    compile_err("\\set{a.b}{1}")


def test_var_visible_inside_rep_body():
    # shared allocation across sub-compiles
    compile_ok("\\set{i}{0}\\rep{3}{\\add{i}{1}}")


# --- runtime semantics ---

def test_run_set_and_val():
    hid = run("\\set{x}{3}\\typenum{$x}")
    assert key_events(hid, keymap.digit_keycode("3"))


def test_run_val_negative():
    hid = run("\\set{x}{-2}\\typenum{$x}")
    kc2 = keymap.digit_keycode("2")
    assert ("kp", keymap.MINUS) in hid.events
    assert ("kp", kc2) in hid.events


def test_run_val_inline_text():
    hid = run("\\set{n}{3}press \\typenum{$n}")
    kc3 = keymap.digit_keycode("3")
    assert ("kp", kc3) in hid.events


def test_run_counter_loop():
    hid = run("\\set{i}{0}\\rep{3}{\\add{i}{1}}\\typenum{$i}")
    assert key_events(hid, keymap.digit_keycode("3"))


def test_run_ifnum_all_ops():
    cases = [
        ("=", 5, 5, True), ("=", 5, 4, False),
        ("!=", 5, 4, True), ("!=", 5, 5, False),
        ("<", 4, 5, True), ("<", 5, 5, False),
        ("<=", 5, 5, True), ("<=", 6, 5, False),
        (">", 6, 5, True), (">", 5, 5, False),
        (">=", 5, 5, True), (">=", 4, 5, False),
    ]
    for op, a, b, expect in cases:
        hid = run(f"\\set{{x}}{{{a}}}\\ifnum{{$x}}{{{op}}}{{{b}}}{{y}}{{z}}")
        typed = [e for e in hid.events if e[0] == "kp" and e[1] in (
            keymap.letter_keycode("y"), keymap.letter_keycode("z"))]
        want = keymap.letter_keycode("y") if expect else keymap.letter_keycode("z")
        assert ("kp", want) in typed, f"{a} {op} {b} -> expect {expect}"
        other = keymap.letter_keycode("z") if expect else keymap.letter_keycode("y")
        assert ("kp", other) not in typed


def test_run_rep_reg_count_zero_skips():
    hid = run("\\set{n}{0}\\rep{$n}{a}b")
    assert ("kp", keymap.letter_keycode("a")) not in hid.events
    assert ("kp", keymap.letter_keycode("b")) in hid.events


def test_run_move_with_reg():
    hid = run("\\set{x}{10}\\move{$x,$x}")
    assert ("mm", 10, 10) in hid.events


def test_run_kdown_name():
    hid = run("\\kdown{enter}\\kup{}")
    assert ("kp", keymap.ENTER) in hid.events


def test_run_click_with_reg_count():
    hid = run("\\set{n}{3}\\click{L,$n}")
    clicks = [e for e in hid.events if e == ("mp", keymap.BTN_LEFT)]
    assert len(clicks) == 3


def test_run_add_i32_wraps():
    hid = run("\\set{x}{0x7fffffff}\\add{x}{1}\\typenum{$x}")
    kc2 = keymap.digit_keycode("2")
    # 0x7fffffff + 1 wraps to -2147483648: must render '-' and digits
    assert ("kp", keymap.MINUS) in hid.events


def test_run_sub_i32_wraps():
    # -2147483648 - 1 wraps to 2147483647: must render digits without '-'
    hid = run("\\set{y}{-2147483648}\\sub{y}{1}\\typenum{$y}")
    assert ("kp", keymap.MINUS) not in hid.events
    assert ("kp", keymap.digit_keycode("2")) in hid.events


def test_disassemble_sub_and_err():
    m = Macro(MockHID())
    bc = compile_ok("\\set{x}{10}\\sub{x}{3}\\set{y}{$err}\\sub{x}{$y}")
    dis = m.disassemble(bc)
    assert "SUB $r0, 3" in dis
    assert "SET $r1, $err" in dis
    assert "SUB $r0, $r1" in dis


def test_disassemble_stage_b():
    m = Macro(MockHID())
    bc, err = m.compile("\\set{x}{1}\\ifnum{$x}{=}{1}{a}\\typenum{$x}")
    assert err is None
    out = m.disassemble(bc)
    assert "SET $r0, 1" in out
    assert "BRA $r0 = 1" in out
    assert "TYPEREG $r0" in out


def test_dollar_escape():
    # spec rule 2: literal $ in text is \$ (a bare $ in text stays literal too)
    bc = compile_ok(r"\$")
    assert bc == H + bytes([OP_CHAR, ord("$"), OP_END])
    bc = compile_ok("a$b")
    assert bc == H + bytes([OP_CHAR, ord("a"), OP_CHAR, ord("$"), OP_CHAR, ord("b"), OP_END])
