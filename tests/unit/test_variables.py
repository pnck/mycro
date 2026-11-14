r"""Variables, registers, arithmetic mutation, and dynamic typed operands per spec/dsl.md §6.

Compile tests assert exact bytecode layout and typed operand encodings.
Execute tests verify runtime semantics, register allocation, and two's complement arithmetic.
"""

import asyncio
import keymap
from macro import (
    Macro,
    MAGIC, VERSION, TAG_IMM, TAG_REG, REG_RET, REG_TIMEOUT, REG_ERR,
    OP_CHAR, OP_SLEEP, OP_PACE, OP_MCLICK, OP_MMOVE, OP_LOOP, OP_LOOP_END,
    OP_KEY_DOWN, OP_KEY_UP, OP_SET, OP_ADD, OP_SUB, OP_TYPEREG, OP_END,
)
from mock_hid import MockHID

H = bytes([VERSION, 0])


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


# --- set/add/sub emission ---

def test_set_imm_layout():
    bc = compile_ok("\\set{x}{5}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 5, 0, 0, 0, OP_END])


def test_set_hex_layout():
    bc = compile_ok("\\set{x}{0x1234}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 0x34, 0x12, 0, 0, OP_END])


def test_set_negative_layout():
    bc = compile_ok("\\set{x}{-5}")
    # -5 as 32-bit signed: 0xFFFFFFFB
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 0xFB, 0xFF, 0xFF, 0xFF, OP_END])


def test_set_from_var():
    bc = compile_ok("\\set{x}{1}\\set{y}{$x}")
    # y is reg 1, reads from reg 0
    assert bc == H + bytes([
        OP_SET, 0, TAG_IMM, 1, 0, 0, 0,
        OP_SET, 1, TAG_REG, 0,
        OP_END,
    ])


def test_add_emission():
    bc = compile_ok("\\set{x}{1}\\add{x}{5}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 1, 0, 0, 0, OP_ADD, 0, TAG_IMM, 5, 0, 0, 0, OP_END])


def test_sub_emission():
    bc = compile_ok("\\set{x}{10}\\sub{x}{3}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 10, 0, 0, 0, OP_SUB, 0, TAG_IMM, 3, 0, 0, 0, OP_END])


def test_sub_from_var():
    bc = compile_ok("\\set{x}{10}\\set{y}{3}\\sub{x}{$y}")
    assert bc == H + bytes([
        OP_SET, 0, TAG_IMM, 10, 0, 0, 0,
        OP_SET, 1, TAG_IMM, 3, 0, 0, 0,
        OP_SUB, 0, TAG_REG, 1,
        OP_END,
    ])


def test_reg_err_and_special_reservations():
    assert REG_ERR == 0xFD
    assert REG_TIMEOUT == 0xFE
    assert REG_RET == 0xFF


def test_read_err_register():
    bc = compile_ok("\\set{x}{$err}")
    assert bc == H + bytes([
        OP_SET, 0, TAG_REG, REG_ERR,
        OP_END,
    ])


def test_reg_allocation_order():
    bc = compile_ok("\\set{b}{1}\\set{a}{2}")
    # b assigned reg 0 (first seen), a assigned reg 1
    assert bc == H + bytes([
        OP_SET, 0, TAG_IMM, 1, 0, 0, 0,
        OP_SET, 1, TAG_IMM, 2, 0, 0, 0,
        OP_END,
    ])


# --- typed operand lowering ---

def test_typed_move():
    bc = compile_ok("\\set{dx}{10}\\move{$dx,-5}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 10, 0, 0, 0, OP_MMOVE, TAG_REG, 0, TAG_IMM, 0xFB, 0xFF, 0xFF, 0xFF, OP_END])


def test_typed_delay():
    bc = compile_ok("\\set{ms}{100}\\delay{$ms}")
    assert bc == H + bytes([OP_SET, 0, TAG_IMM, 100, 0, 0, 0, OP_SLEEP, TAG_REG, 0, OP_END])


def test_typed_rep_count():
    bc = compile_ok("\\set{n}{5}\\rep{$n}{a}")
    assert bc[len(H) + 7 :] == bytes([
        OP_LOOP, TAG_REG, 0, 3, 0,
        OP_CHAR, ord("a"), OP_LOOP_END,
        OP_END,
    ])


def test_typed_click():
    bc = compile_ok("\\set{c}{3}\\click{L,$c}")
    assert bc[len(H) + 7 :] == bytes([
        OP_MCLICK, TAG_IMM, keymap.BTN_LEFT, 0, 0, 0, TAG_REG, 0, OP_END,
    ])


def test_kdown_var_rejected():
    err = compile_err("\\set{k}{1}\\kdown{$k}")
    assert "kdown/kup takes a key name, not a variable" in err


def test_mouse_btn_name_position():
    err = compile_err("\\click{$L}")
    assert "click/mdown/mup takes a button name, not a variable" in err
    err = compile_err("\\click{invalid_btn}")
    assert "Unknown mouse button: invalid_btn" in err


def test_kup_imm_still_works():
    bc = compile_ok("\\kup{}")
    assert bc[len(H):] == bytes([OP_KEY_UP, TAG_IMM, 0, 0, 0, 0, OP_END])


def test_braced_var_form():
    bc = compile_ok("\\set{n1}{7}\\move{${n1},0}")
    assert bc[len(H) + 7 :] == bytes([OP_MMOVE, TAG_REG, 0, TAG_IMM, 0, 0, 0, 0, OP_END])


def test_typenum_emission():
    bc = compile_ok("\\set{x}{1}\\typenum{$x}")
    assert bc[len(H) + 7 :] == bytes([OP_TYPEREG, 0, OP_END])


def test_typenum_special_regs():
    assert compile_ok("\\typenum{$ret}")[len(H):] == bytes([OP_TYPEREG, REG_RET, OP_END])
    assert compile_ok("\\typenum{$timeout}")[len(H):] == bytes([OP_TYPEREG, REG_TIMEOUT, OP_END])


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


def test_err_too_many_vars():
    text = "".join(f"\\set{{v{i}}}{{1}}" for i in range(33))  # MAX_REGS = 32
    assert "Too many variables" in compile_err(text)


def test_err_unmatched_var_brace():
    assert "Unmatched" in compile_err("\\set{x}{1}\\move{${x,0}")


def test_err_i32_range():
    assert "out of i32 range" in compile_err("\\set{x}{0x100000000}")


def test_err_dot_in_var_name():
    assert "not allowed in variable names" in compile_err("\\set{a.b}{1}")


def test_var_visible_inside_rep_body():
    compile_ok("\\set{i}{0}\\rep{3}{\\add{i}{1}}")


# --- runtime execution semantics ---

def test_run_set_and_typenum():
    hid = run("\\set{x}{3}\\typenum{$x}")
    assert key_events(hid, keymap.digit_keycode("3"))


def test_run_typenum_negative():
    hid = run("\\set{x}{-2}\\typenum{$x}")
    kc2 = keymap.digit_keycode("2")
    assert ("kp", keymap.MINUS) in hid.events
    assert ("kp", kc2) in hid.events


def test_run_typenum_inline_text():
    hid = run("\\set{n}{3}press \\typenum{$n}")
    kc3 = keymap.digit_keycode("3")
    assert ("kp", kc3) in hid.events


def test_run_counter_loop():
    hid = run("\\set{i}{0}\\rep{3}{\\add{i}{1}}\\typenum{$i}")
    assert key_events(hid, keymap.digit_keycode("3"))


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
    # 0x7fffffff + 1 wraps to -2147483648: must render '-' and digits
    assert ("kp", keymap.MINUS) in hid.events


def test_run_sub_i32_wraps():
    # -2147483648 - 1 wraps to 2147483647: must render digits without '-'
    hid = run("\\set{y}{-2147483648}\\sub{y}{1}\\typenum{$y}")
    assert ("kp", keymap.MINUS) not in hid.events
    assert ("kp", keymap.digit_keycode("2")) in hid.events
