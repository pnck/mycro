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


# --- DSL v4: Step 3 Preamble & Layout Modes ---

def test_preamble_typewriter_default_and_explicit():
    # Default is typewriter: spaces emit OP_CHAR 0x20, newlines emit ENTER
    bc_def = compile_ok("a b\nc")
    assert bc_def == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, 0x20, OP_CHAR, ord("b"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("c"),
        OP_END,
    ])
    # Explicit typewriter directive
    bc_exp = compile_ok("\\#!syntax: typewriter\na b\nc")
    assert bc_exp == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, 0x20, OP_CHAR, ord("b"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("c"),
        OP_END,
    ])


def test_preamble_free_mode_absorbs_whitespace():
    # In free mode, spaces, tabs, and newlines are absorbed
    text = "\\#!syntax: free\n  a   b  \t  \n  c  \n"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_CHAR, ord("c"), OP_END,
    ])


def test_preamble_free_mode_explicit_keystrokes():
    # In free mode, explicit commands emit target keystrokes
    text = "\\#!syntax: free\na\\space{}b\\tab{}c\\enter"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"),
        OP_CHAR, 0x20,
        OP_CHAR, ord("b"),
        OP_KEY, keymap.TAB,
        OP_CHAR, ord("c"),
        OP_KEY, keymap.ENTER,
        OP_END,
    ])


def test_preamble_fine_grained_newline_ignore_space_emit():
    text = "\\#!syntax: newline=ignore, space=emit\na b\nc d"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, 0x20, OP_CHAR, ord("b"),
        OP_CHAR, ord("c"), OP_CHAR, 0x20, OP_CHAR, ord("d"),
        OP_END,
    ])


def test_preamble_fine_grained_newline_emit_space_ignore():
    text = "\\#!syntax: newline=emit, space=ignore\na b\nc d"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("c"), OP_CHAR, ord("d"),
        OP_END,
    ])


def test_preamble_fine_grained_single_key_inherits_default():
    # newline=ignore inherits space=emit
    bc1 = compile_ok("\\#!syntax: newline=ignore\na b\nc")
    assert bc1 == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, 0x20, OP_CHAR, ord("b"),
        OP_CHAR, ord("c"),
        OP_END,
    ])
    # space=ignore inherits newline=emit
    bc2 = compile_ok("\\#!syntax: space=ignore\na b\nc")
    assert bc2 == H + bytes([
        OP_CHAR, ord("a"), OP_CHAR, ord("b"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("c"),
        OP_END,
    ])


def test_preamble_multiple_directives_override_per_key():
    text = "\\#!syntax: free\n\\#!syntax: newline=emit\na\nb"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"), OP_KEY, keymap.ENTER, OP_CHAR, ord("b"), OP_END,
    ])


def test_preamble_comments_and_line_continuations():
    text = (
        "\\# Leading header comment\n"
        "\\\n"  # line continuation
        "\\#!syntax: free \\# trailing comment\n"
        "  x  \n"
    )
    bc = compile_ok(text)
    assert bc == H + bytes([OP_CHAR, ord("x"), OP_END])


def test_preamble_errors_in_executable_code():
    err1 = compile_err("echo\n\\#!syntax: free")
    assert "directive in executable code" in err1

    err2 = compile_err("\\def{foo}{\\#!syntax: free}")
    assert "directive in executable code" in err2

    err3 = compile_err("\\rep{2}{\\#!syntax: free}")
    assert "directive in executable code" in err3


def test_preamble_syntax_errors():
    assert "Unknown preamble directive" in compile_err("\\#!unknown_dir: free")
    assert "Unknown layout preset" in compile_err("\\#!syntax: unknown_preset")
    assert "Unknown syntax config key" in compile_err("\\#!syntax: indents=ignore")
    assert "Unknown syntax config value" in compile_err("\\#!syntax: newline=discard")
    assert "Invalid syntax configuration" in compile_err("\\#!syntax:")
    assert "Invalid syntax configuration" in compile_err("\\#!syntax: newline=ignore,")


# --- DSL v4: Step 3 Verbatim Text Blocks ---

def test_text_block_basic():
    bc = compile_ok('\\text{}echo "Hello World"\\endtext{}')
    expected_payload = b'echo "Hello World"'
    expected_ops = []
    for b in expected_payload:
        expected_ops.extend([OP_CHAR, b])
    assert bc == H + bytes(expected_ops + [OP_END])


def test_text_block_in_free_mode_preserves_spaces():
    text = '\\#!syntax: free\n\\text{}a   b\\endtext{}'
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"),
        OP_CHAR, 0x20, OP_CHAR, 0x20, OP_CHAR, 0x20,
        OP_CHAR, ord("b"),
        OP_END,
    ])


def test_text_block_line_endings_and_tab():
    # LF emits ENTER, CRLF emits single ENTER, standalone CR emits ENTER, Tab emits TAB
    text = "\\text{}a\nb\r\nc\rd\te\\endtext{}"
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_CHAR, ord("a"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("b"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("c"),
        OP_KEY, keymap.ENTER,
        OP_CHAR, ord("d"),
        OP_KEY, keymap.TAB,
        OP_CHAR, ord("e"),
        OP_END,
    ])


def test_text_block_literal_characters():
    # Commands, comments, escapes, and variables are literal characters inside text block
    text = "\\text{}\\enter \\#comment $var {braces} \\\\endtext\\endtext{}"
    bc = compile_ok(text)
    payload = b"\\enter \\#comment $var {braces} \\\\endtext"
    expected = []
    for b in payload:
        expected.extend([OP_CHAR, b])
    assert bc == H + bytes(expected + [OP_END])


def test_text_block_adjacent_empty_braces_required():
    assert "\\text requires {}" in compile_err("\\text foo")
    assert "\\text requires {}" in compile_err("\\text{foo}")
    assert "\\text requires {}" in compile_err("\\text {}")
    assert "\\endtext requires {}" in compile_err("\\endtext foo")
    assert "\\endtext requires {}" in compile_err("\\endtext{foo}")
    assert "\\endtext requires {}" in compile_err("\\endtext {}")


def test_text_block_mismatched_and_unterminated():
    assert "endtext without text block" in compile_err("\\endtext{}")
    assert "Unterminated text block" in compile_err("\\text{}hello")
    assert "Unterminated text block" in compile_err("\\text{}hello\\endtext")


def test_text_block_macro_substitution():
    text = "\\def{gg}[1]{\\text{}#1\\endtext{}}\\gg{echo 'hi'}"
    bc = compile_ok(text)
    payload = b"echo 'hi'"
    expected = []
    for b in payload:
        expected.extend([OP_CHAR, b])
    assert bc == H + bytes(expected + [OP_END])


def test_text_block_macro_substitution_with_terminator_in_arg():
    # Literal \endtext{} in argument terminates the text block
    text = "\\def{gg}[1]{\\text{}#1\\endtext{}}\\gg{first\\endtext{}second}"
    err = compile_err(text)
    assert "endtext without text block" in err


def test_text_block_reserved_command_names():
    assert "macro name conflicts with builtin: text" in compile_err("\\def{text}{a}")
    assert "macro name conflicts with builtin: endtext" in compile_err("\\def{endtext}{a}")


def test_empty_group_separator_in_macro_call():
    text = "\\def{go}{\\enter}\\go{}\\a{}"
    bc = compile_ok(text)
    assert bc == H + bytes([OP_KEY, keymap.ENTER, OP_CHAR, ord("a"), OP_END])


def test_text_block_invalid_char():
    assert "Invalid char: 0x01" in compile_err("\\text{}\x01\\endtext{}")


def test_spec_section_3_4_normative_examples():
    # Example 1: Structured source
    ex1 = (
        "\\# Structured source\n"
        "\\#!syntax: free\n"
        "\\def{submit_form}[0]{\n"
        "  \\kdown{ctrl}\n"
        "  \\delay{50ms}\n"
        "  \\kup{ctrl}\n"
        "  \\enter          \\# Explicit Enter\n"
        "}\n"
    )
    compile_ok(ex1)

    # Example 2: Whitespace-bearing text in free mode
    ex2 = (
        "\\# Whitespace-bearing text in free mode\n"
        "\\#!syntax: free\n"
        '\\text{}echo "Hello World"\\endtext{}\n'
        "\\enter\n"
    )
    compile_ok(ex2)

    # Example 3: Default typewriter layout
    ex3 = (
        "\\# Default typewriter layout\n"
        'echo "Hello MYCRO"\n'
        "ls -la\n"
    )
    compile_ok(ex3)


# --- DSL v4: Step 2 & Modularization Coverage Additions ---

def test_compiler_class_direct_usage():
    from compiler import Compiler
    c = Compiler()
    bc, err = c.compile("a")
    assert err is None
    assert bc == H + bytes([OP_CHAR, ord("a"), OP_END])


def test_bytecode_read_header():
    from bytecode import read_header
    bc = H + bytes([OP_END])
    strs, off = read_header(bc)
    assert strs == []
    assert off == 3


def test_combo_uppercase_expansion():
    # \ctrl+A expands A to [shift, a] -> down ctrl, down shift, down a, sleep, up a, up shift, up ctrl
    bc = compile_ok("\\ctrl+A")
    kc_a = keymap.letter_keycode("a")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL, 0, 0, 0,
        OP_KEY_DOWN, 0, keymap.SHIFT, 0, 0, 0,
        OP_KEY_DOWN, 0, kc_a, 0, 0, 0,
        OP_SLEEP, 0, 10, 0, 0, 0,
        OP_KEY_UP, 0, kc_a, 0, 0, 0,
        OP_KEY_UP, 0, keymap.SHIFT, 0, 0, 0,
        OP_KEY_UP, 0, keymap.CONTROL, 0, 0, 0,
        OP_END,
    ])


def test_kdown_kup_multi_key_combo():
    # \kdown emits keys forward, \kup emits keys reversed
    bc = compile_ok("\\kdown{ctrl+c}\\kup{ctrl+c}")
    kc_c = keymap.letter_keycode("c")
    assert bc == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL, 0, 0, 0,
        OP_KEY_DOWN, 0, kc_c, 0, 0, 0,
        OP_KEY_UP, 0, kc_c, 0, 0, 0,
        OP_KEY_UP, 0, keymap.CONTROL, 0, 0, 0,
        OP_END,
    ])


def test_kup_explicit_zero_releases_all():
    bc = compile_ok("\\kup{0}")
    assert bc == H + bytes([OP_KEY_UP, 0, 0, 0, 0, 0, OP_END])


def test_combo_max_keys_limit():
    # 8 keys is allowed
    compile_ok("\\a+b+c+d+e+f+g+h")
    # 9 keys exceeds MAX_COMBO_KEYS=8
    err = compile_err("\\a+b+c+d+e+f+g+h+i")
    assert "exceeds MAX_COMBO_KEYS=8" in err

    # \kdown with 9 keys
    err_kdown = compile_err("\\kdown{a+b+c+d+e+f+g+h+i}")
    assert "exceeds MAX_COMBO_KEYS=8" in err_kdown


def test_keypad_and_right_modifier_combos():
    bc1 = compile_ok("\\rctrl+c")
    kc_c = keymap.letter_keycode("c")
    assert bc1 == H + bytes([
        OP_KEY_DOWN, 0, keymap.RCONTROL, 0, 0, 0,
        OP_KEY_DOWN, 0, kc_c, 0, 0, 0,
        OP_SLEEP, 0, 10, 0, 0, 0,
        OP_KEY_UP, 0, kc_c, 0, 0, 0,
        OP_KEY_UP, 0, keymap.RCONTROL, 0, 0, 0,
        OP_END,
    ])

    bc2 = compile_ok("\\ctrl+kp_1")
    assert bc2 == H + bytes([
        OP_KEY_DOWN, 0, keymap.CONTROL, 0, 0, 0,
        OP_KEY_DOWN, 0, keymap.KP_1, 0, 0, 0,
        OP_SLEEP, 0, 10, 0, 0, 0,
        OP_KEY_UP, 0, keymap.KP_1, 0, 0, 0,
        OP_KEY_UP, 0, keymap.CONTROL, 0, 0, 0,
        OP_END,
    ])


def test_empty_text_block():
    bc = compile_ok("\\text{}\\endtext{}")
    assert bc == H + bytes([OP_END])


def test_free_mode_absorbed_whitespace_between_args():
    text = (
        "\\#!syntax: free\n"
        "\\rep{2}\n"
        "  {ab}\n"
    )
    bc = compile_ok(text)
    assert bc == H + bytes([
        OP_LOOP, 0, 2, 0, 0, 0, 5, 0,
        OP_CHAR, ord("a"), OP_CHAR, ord("b"), OP_LOOP_END,
        OP_END,
    ])

