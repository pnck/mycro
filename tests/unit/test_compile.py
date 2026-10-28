"""Compiler tests — run on CPython, no hardware involved."""

import keymap
from macro import (
    Macro,
    MAGIC, VERSION,
    OP_CHAR, OP_KEY, OP_COMBO, OP_SLEEP, OP_PACE, OP_MCLICK, OP_MMOVE,
    OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP, OP_END,
    MAX_RUNTIME_NEST,
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
    kc = keymap.letter_keycode("c")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL, 0, 0, 0,
        OP_KEY_DOWN, 0, kc, 0, 0, 0,
        OP_SLEEP, 0, 10, 0, 0, 0,
        OP_KEY_UP, 0, kc, 0, 0, 0,
        OP_KEY_UP, 0, keymap.CONTROL, 0, 0, 0,
        OP_END,
    ])


def test_combo_chain():
    bc = compile_ok("\\ctrl+\\shift+t")
    kt = keymap.letter_keycode("t")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL, 0, 0, 0,
        OP_KEY_DOWN, 0, keymap.SHIFT, 0, 0, 0,
        OP_KEY_DOWN, 0, kt, 0, 0, 0,
        OP_SLEEP, 0, 10, 0, 0, 0,
        OP_KEY_UP, 0, kt, 0, 0, 0,
        OP_KEY_UP, 0, keymap.SHIFT, 0, 0, 0,
        OP_KEY_UP, 0, keymap.CONTROL, 0, 0, 0,
        OP_END,
    ])


def test_modifier_only():
    bc = compile_ok("\\ctrl")
    assert bc == H + bytes([OP_KEY, keymap.CONTROL, OP_END])


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
    assert bc == H + bytes([OP_SLEEP, 0, 500 & 0xFF, 500 >> 8, 0, 0, OP_END])


def test_pace():
    bc = compile_ok("\\pace{0.1}")
    assert bc == H + bytes([OP_PACE, 0, 100, 0, 0, 0, OP_END])


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
        OP_LOOP, 0, 2, 0, 0, 0, 7, 0,
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
        OP_LOOP, 0, 3, 0, 0, 0, 5, 0,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_LOOP_END,
        OP_END,
    ])


def test_rep_nesting_at_limit():
    code = "\\rep{2}{" * MAX_RUNTIME_NEST + "a" + "}" * MAX_RUNTIME_NEST
    compile_ok(code)


def test_rep_nesting_exceeding_limit_rejected():
    code = "\\rep{2}{" * (MAX_RUNTIME_NEST + 1) + "a" + "}" * (MAX_RUNTIME_NEST + 1)
    err = compile_err(code)
    assert f"rep nesting exceeds {MAX_RUNTIME_NEST} levels" in err


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
    assert bc == H + bytes([OP_MCLICK, 0, keymap.BTN_RIGHT, 0, 0, 0, 0, 3, 0, 0, 0, OP_END])
    bc = compile_ok("\\move{100,-50}")
    assert bc[3] == OP_MMOVE
    bc = compile_ok("\\mdown{L}\\mup{L}")
    assert keymap.BTN_LEFT in bc


def test_mouse_side_buttons():
    # default CP mouse descriptor declares 5 buttons: back/X1=bit3, forward/X2=bit4
    bc = compile_ok("\\click{back}")
    assert bc == H + bytes([OP_MCLICK, 0, keymap.BTN_BACK, 0, 0, 0, 0, 1, 0, 0, 0, OP_END])
    bc = compile_ok("\\click{x2}\\mdown{fwd}\\mup{b}")
    assert bc == H + bytes([
        OP_MCLICK, 0, keymap.BTN_FORWARD, 0, 0, 0, 0, 1, 0, 0, 0,
        0x14, 0, keymap.BTN_FORWARD, 0, 0, 0,  # OP_MDOWN
        0x15, 0, keymap.BTN_BACK, 0, 0, 0,     # OP_MUP
        OP_END,
    ])


def test_kdown_kup():
    bc = compile_ok("\\kdown{ctrl}c\\kup{}")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL, 0, 0, 0,
        OP_CHAR, ord("c"),
        OP_KEY_UP, 0, 0, 0, 0, 0,
        OP_END,
    ])


def test_combo_eight_exact():
    m = Macro(MockHID())
    bc, err = m.compile("\\a+b+c+d+e+f+g+h", config={"click_hold_ms": 3})
    assert err is None
    # 8 KEY_DOWN, SLEEP 3, 8 KEY_UP in reverse, OP_END
    expected = H
    for c in "abcdefgh":
        expected += bytes([OP_KEY_DOWN, 0, keymap.letter_keycode(c), 0, 0, 0])
    expected += bytes([OP_SLEEP, 0, 3, 0, 0, 0])
    for c in reversed("abcdefgh"):
        expected += bytes([OP_KEY_UP, 0, keymap.letter_keycode(c), 0, 0, 0])
    expected += bytes([OP_END])
    assert bc == expected


def test_combo_over_limit():
    err = compile_err("\\a+b+c+d+e+f+g+h+i")
    assert "key combination exceeds MAX_COMBO_KEYS=8" in err


def test_combo_case_expansion():
    # \a+A -> [a, shift, a] down, hold, reverse up
    bc = compile_ok("\\a+A")
    ka = keymap.letter_keycode("a")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, ka, 0, 0, 0,
        OP_KEY_DOWN, 0, keymap.SHIFT, 0, 0, 0,
        OP_KEY_DOWN, 0, ka, 0, 0, 0,
        OP_SLEEP, 0, 10, 0, 0, 0,
        OP_KEY_UP, 0, ka, 0, 0, 0,
        OP_KEY_UP, 0, keymap.SHIFT, 0, 0, 0,
        OP_KEY_UP, 0, ka, 0, 0, 0,
        OP_END,
    ])


def test_lone_printable_keys():
    for ch in ("a", "z", "A", "Z"):
        bc = compile_ok(f"\\{ch}")
        assert bc == H + bytes([OP_CHAR, ord(ch), OP_END])
    for cmd, ascii_val in (("plus", ord("+")), ("minus", ord("-")), ("equal", ord("=")), ("space", ord(" "))):
        bc = compile_ok(f"\\{cmd}")
        assert bc == H + bytes([OP_CHAR, ascii_val, OP_END])


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
    rt.register_ns("sys", {"free": ([], "int", lambda: 0)})
    bc, err = Macro(MockHID(), rt).compile("\\call{sys.free}\\typenum{$ret}")
    assert err is None, f"unexpected compile error: {err}"


def test_disassemble_canonical_key_names():
    # Aliases (enter/return) must render deterministically on every runtime:
    # CircuitPython dicts are hash-ordered, unlike CPython insertion order
    bc = compile_ok("\\enter")
    assert "KEY_TAP ENTER" in Macro(MockHID()).disassemble(bc)


def test_header_magic_and_version():
    assert MAGIC == 0xA6
    assert VERSION == 4
    bc = compile_ok("")
    assert bc[:2] == bytes([0xA6, 0x04])


def test_header_rejects_mismatched_magic_or_version():
    import pytest
    m = Macro(MockHID())
    with pytest.raises(ValueError, match="Bytecode version mismatch"):
        m.disassemble(bytes([0xA5, 0x03, 0, OP_END]))
    with pytest.raises(ValueError, match="Bytecode version mismatch"):
        m.disassemble(bytes([0x99, VERSION, 0, OP_END]))
