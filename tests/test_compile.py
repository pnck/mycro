"""Compiler tests — run on CPython, no hardware involved."""

import keymap
from macro import (
    Macro,
    MAGIC, VERSION,
    OP_CHAR, OP_KEY, OP_COMBO, OP_SLEEP, OP_PACE, OP_MCLICK, OP_MMOVE,
    OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP, OP_END,
)
from mock_hid import MockHID

H = bytes([MAGIC, VERSION, 0])  # bytecode header prefix (empty string table)


def compile_ok(text):
    bc, err = Macro(MockHID()).compile(text)
    assert err is None, f"unexpected compile error: {err}"
    assert bc[: len(H)] == H, "bytecode must start with the v3 header"
    return bc


def compile_err(text):
    bc, err = Macro(MockHID()).compile(text)
    assert bc is None and err, "expected compile error, got success"
    return err


def test_plain_text():
    bc = compile_ok("ab")
    assert bc == H + bytes([OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_END])


def test_newline_is_enter():
    bc = compile_ok("a\nb")
    assert bc == H + bytes([OP_CHAR, ord("a"), OP_KEY, keymap.ENTER, OP_CHAR, ord("b"), OP_END])


def test_crlf_single_enter():
    bc = compile_ok("a\r\nb")
    assert bc == H + bytes([OP_CHAR, ord("a"), OP_KEY, keymap.ENTER, OP_CHAR, ord("b"), OP_END])


def test_special_key():
    bc = compile_ok("\\enter")
    assert bc == H + bytes([OP_KEY, keymap.ENTER, OP_END])


def test_combo():
    bc = compile_ok("\\ctrl+c")
    assert bc == H + bytes([OP_COMBO, 0x01, keymap.letter_keycode("c"), OP_END])


def test_combo_chain():
    bc = compile_ok("\\ctrl+\\shift+t")
    assert bc == H + bytes([OP_COMBO, 0x03, keymap.letter_keycode("t"), OP_END])


def test_modifier_only():
    bc = compile_ok("\\ctrl")
    assert bc == H + bytes([OP_COMBO, 0x01, 0, OP_END])


def test_empty_braces_separator():
    bc = compile_ok("\\enter{}abc")
    assert bc == H + bytes([
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_CHAR, ord("c"),
        OP_END,
    ])


# --- DSL v2: \delay (one-shot sleep) / \pace (inter-action delay) ---

def test_delay_is_one_shot_sleep():
    bc = compile_ok("\\delay{0.5}")
    assert bc == H + bytes([OP_SLEEP, 0, 500 & 0xFF, 500 >> 8, OP_END])


def test_pace():
    bc = compile_ok("\\pace{0.1}")
    assert bc == H + bytes([OP_PACE, 0, 100, 0, OP_END])


# --- DSL v2: line continuation ---

def test_line_continuation_lf():
    bc = compile_ok("ab\\\ncd")
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"),
        OP_CHAR, ord("c"), OP_CHAR, ord("d"),
        OP_END,
    ])


def test_line_continuation_crlf():
    bc = compile_ok("ab\\\r\ncd")
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"),
        OP_CHAR, ord("c"), OP_CHAR, ord("d"),
        OP_END,
    ])


def test_line_continuation_after_command():
    bc = compile_ok("\\enter\\\nabc")
    assert bc == H + bytes([
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_CHAR, ord("c"),
        OP_END,
    ])


# --- DSL v2: comments ---

def test_comment_to_eol_consumes_newline():
    bc = compile_ok("ab\\# note\ncd")
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"),
        OP_CHAR, ord("c"), OP_CHAR, ord("d"),
        OP_END,
    ])


def test_comment_at_eof():
    bc = compile_ok("ab\\# trailing note")
    assert bc == H + bytes([OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_END])


def test_comment_plus_continuation_block():
    text = "\\# block 1\nab \\\n\\# block 2\ncd"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"),
        OP_CHAR, ord(" "),
        OP_CHAR, ord("c"), OP_CHAR, ord("d"),
        OP_END,
    ])


# --- DSL v2: escaped braces inside command args ---

def test_escaped_brace_in_rep_body():
    bc = compile_ok("\\rep{2}{a\\{b}")
    # body: CHAR a, CHAR {, CHAR b (the \{ must not break depth counting)
    assert bc == H + bytes([
        OP_LOOP, 0, 2, 7, 0,
        OP_CHAR, ord("a"), OP_CHAR, ord("{"), OP_CHAR, ord("b"), OP_LOOP_END,
        OP_END,
    ])


def test_escaped_closing_brace_does_not_close_arg():
    # \{ and \} are literal; the arg ends only at the unescaped }
    err = compile_err("\\delay{1\\}")
    assert "Unmatched" in err


# --- rep / loops ---

def test_rep_basic():
    bc = compile_ok("\\rep{3}{ab}")
    assert bc == H + bytes([
        OP_LOOP, 0, 3, 5, 0,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_LOOP_END,
        OP_END,
    ])


def test_rep_nesting_allowed_to_four_levels():
    compile_ok("\\rep{2}{a\\rep{2}{b\\rep{2}{c\\rep{2}{d}}}}")


def test_rep_nesting_beyond_four_levels_rejected():
    err = compile_err("\\rep{2}{a\\rep{2}{b\\rep{2}{c\\rep{2}{d\\rep{2}{e}}}}}")
    assert "nesting" in err


def test_rep_requires_body():
    assert "body" in compile_err("\\rep{3}")


# --- errors ---

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
    assert bc == H + bytes([OP_MCLICK, 0, keymap.BTN_RIGHT, 0, 3, 0, OP_END])
    bc = compile_ok("\\move{100,-50}")
    assert bc[3] == OP_MMOVE
    bc = compile_ok("\\mdown{L}\\mup{L}")
    assert keymap.BTN_LEFT in bc


def test_mouse_side_buttons():
    # default CP mouse descriptor declares 5 buttons: back/X1=bit3, forward/X2=bit4
    bc = compile_ok("\\click{back}")
    assert bc == H + bytes([OP_MCLICK, 0, keymap.BTN_BACK, 0, 1, 0, OP_END])
    bc = compile_ok("\\click{x2}\\mdown{fwd}\\mup{b}")
    assert bc == H + bytes([
        OP_MCLICK, 0, keymap.BTN_FORWARD, 0, 1, 0,
        0x14, 0, keymap.BTN_FORWARD,  # OP_MDOWN
        0x15, 0, keymap.BTN_BACK,     # OP_MUP
        OP_END,
    ])


def test_kdown_kup():
    bc = compile_ok("\\kdown{ctrl}c\\kup{}")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL,
        OP_CHAR, ord("c"),
        OP_KEY_UP, 0, 0,
        OP_END,
    ])


def test_bytecode_limit():
    err = compile_err("a" * 10000)
    assert "limit" in err


# --- regression: every example from index.html must still compile ---

def test_index_html_examples_compile():
    examples = [
        "Hello World\\enter",
        "\\ctrl+c\\delay{0.2}\\rep{3}{\\ctrl+v}",
        "\\win+r\\delay{0.3}notepad\\enter",
        "\\cmd+space\\delay{0.2}terminal\\enter",
        "\\mdown{L}\\move{100,0}\\mup{L}",
        "\\kdown{ctrl}ccc\\kup{}",
        "\\def{cp}[1]{\\ctrl+#1}\\cp{c}\\cp{v}",
        "\\set{n}{3}\\rep{$n}{ab\\enter}",
    ]
    for ex in examples:
        compile_ok(ex)
    # the \call example needs the runtime registry (sys namespace)
    from runtime import Runtime
    rt = Runtime()
    rt.register_ns("sys", {"free": ([], lambda: 0)})
    bc, err = Macro(MockHID(), rt).compile("\\call{sys.free}\\val{ret}")
    assert err is None, f"unexpected compile error: {err}"


def test_disassemble_canonical_key_names():
    # Aliases (enter/return) must render deterministically on every runtime:
    # CircuitPython dicts are hash-ordered, unlike CPython insertion order
    bc = compile_ok("\\enter")
    assert "KEY ENTER" in Macro(MockHID()).disassemble(bc)
