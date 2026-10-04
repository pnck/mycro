"""Compiler tests — run on CPython, no hardware involved."""

import keymap
from macro import (
    Macro,
    OP_CHAR, OP_KEY, OP_COMBO, OP_DELAY, OP_MCLICK, OP_MMOVE,
    OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP, OP_END,
)
from mock_hid import MockHID


def compile_ok(text):
    bc, err = Macro(MockHID()).compile(text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text):
    bc, err = Macro(MockHID()).compile(text)
    assert bc is None and err, "expected compile error, got success"
    return err


def test_plain_text():
    bc = compile_ok("ab")
    assert bc == bytes([OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_END])


def test_newline_is_enter():
    bc = compile_ok("a\nb")
    assert bc == bytes([OP_CHAR, ord("a"), OP_KEY, keymap.ENTER, OP_CHAR, ord("b"), OP_END])


def test_crlf_single_enter():
    bc = compile_ok("a\r\nb")
    assert bc == bytes([OP_CHAR, ord("a"), OP_KEY, keymap.ENTER, OP_CHAR, ord("b"), OP_END])


def test_special_key():
    bc = compile_ok("\\enter")
    assert bc == bytes([OP_KEY, keymap.ENTER, OP_END])


def test_combo():
    bc = compile_ok("\\ctrl+c")
    assert bc == bytes([OP_COMBO, 0x01, keymap.letter_keycode("c"), OP_END])


def test_combo_chain():
    bc = compile_ok("\\ctrl+\\shift+t")
    assert bc == bytes([OP_COMBO, 0x03, keymap.letter_keycode("t"), OP_END])


def test_modifier_only():
    bc = compile_ok("\\ctrl")
    assert bc == bytes([OP_COMBO, 0x01, 0, OP_END])


def test_empty_braces_separator():
    bc = compile_ok("\\enter{}abc")
    assert bc == bytes([
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_CHAR, ord("c"),
        OP_END,
    ])


def test_delay_seconds_to_ms():
    bc = compile_ok("\\delay{0.5}")
    assert bc == bytes([OP_DELAY, 500 & 0xFF, 500 >> 8, OP_END])


def test_rep_basic():
    bc = compile_ok("\\rep{3}{ab}")
    # LOOP count=3 len=5 (2*CHAR + LOOP_END), body, LOOP_END, END
    assert bc == bytes([
        OP_LOOP, 3, 5, 0,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_LOOP_END,
        OP_END,
    ])


def test_rep_nesting_allowed_to_two_levels():
    compile_ok("\\rep{2}{a\\rep{2}{b}}")


def test_rep_nesting_beyond_two_levels_rejected():
    err = compile_err("\\rep{2}{a\\rep{2}{b\\rep{2}{c}}}")
    assert "nesting" in err


def test_rep_requires_body():
    assert "body" in compile_err("\\rep{3}")


def test_trailing_backslash_is_clean_error():
    # Regression: used to crash with TypeError instead of a MacroError
    assert "escape" in compile_err("abc\\")


def test_unmatched_brace():
    assert "Unmatched" in compile_err("\\delay{1")


def test_unknown_key():
    assert "Unknown key" in compile_err("\\nosuchkey")


def test_invalid_char():
    compile_err("abcé")  # non-ASCII rejected


def test_mouse_commands():
    bc = compile_ok("\\click{R,3}")
    assert bc == bytes([OP_MCLICK, keymap.BTN_RIGHT, 3, 0, OP_END])
    bc = compile_ok("\\move{100,-50}")
    assert bc[0] == OP_MMOVE
    bc = compile_ok("\\mdown{L}\\mup{L}")
    assert keymap.BTN_LEFT in bc


def test_kdown_kup():
    bc = compile_ok("\\kdown{ctrl}c\\kup{}")
    assert bc == bytes([
        OP_KEY_DOWN, keymap.CONTROL,
        OP_CHAR, ord("c"),
        OP_KEY_UP, 0,
        OP_END,
    ])


def test_bytecode_limit():
    err = compile_err("a" * 10000)
    assert "limit" in err


def test_disassemble_canonical_key_names():
    # Aliases (enter/return) must render deterministically on every runtime:
    # CircuitPython dicts are hash-ordered, unlike CPython insertion order
    bc = compile_ok("\enter")
    assert "KEY ENTER" in Macro(MockHID()).disassemble(bc)
