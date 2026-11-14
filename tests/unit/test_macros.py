r"""Compile-Time Text Macros (\def) per spec/dsl.md §7.

Covers:
- Definition syntax, naming constraints (>=2 chars, identifier, reserved keywords)
- Inline bounded expansion and substitution equivalence (#1-#9, ##)
- Scoping rules (top-level only, recursion detection, call-before-def rejection)
- Decoupled macro expansion ceiling (MAX_MACRO_EXPAND = 16)
- Escaped delimiter preservation in argument capture
"""

import keymap
from macro import Macro, MAGIC, VERSION, OP_END, MAX_MACRO_EXPAND, MAX_RUNTIME_NEST
from mock_hid import MockHID

H = bytes([VERSION])


def compile_ok(text):
    bc, err = Macro(MockHID()).compile(text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text):
    bc, err = Macro(MockHID()).compile(text)
    assert bc is None and err, f"expected compile error, got success: {text}"
    return err


# --- expansion equivalence ---

def test_no_arg_macro_equals_inline():
    assert compile_ok("\\def{save}{\\ctrl+s}\\save") == compile_ok("\\ctrl+s")


def test_macro_used_twice_expands_twice():
    assert compile_ok("\\def{save}{\\ctrl+s}\\save\\save") == compile_ok("\\ctrl+s\\ctrl+s")


def test_one_arg_substitution():
    assert compile_ok(
        "\\def{open}[1]{\\win+r\\delay{0.3s}#1\\enter}\\open{notepad}"
    ) == compile_ok("\\win+r\\delay{0.3s}notepad\\enter")


def test_arg_used_twice():
    assert compile_ok("\\def{dup}[1]{#1#1}\\dup{ab}") == compile_ok("abab")


def test_multi_arg():
    assert compile_ok("\\def{pair}[2]{#1-#2}\\pair{a}{b}") == compile_ok("a-b")


def test_macro_in_rep_body():
    assert compile_ok("\\def{save}{\\ctrl+s}\\rep{2}{\\save}") == compile_ok(
        "\\rep{2}{\\ctrl+s}"
    )


def test_rep_inside_macro():
    assert compile_ok("\\def{trip}[1]{\\rep{3}{#1}}\\trip{ab}") == compile_ok(
        "\\rep{3}{ab}"
    )


def test_macro_calling_macro():
    assert compile_ok(
        "\\def{a1}{X}\\def{b1}{\\a1\\a1}\\b1"
    ) == compile_ok("XX")


def test_arg_containing_command():
    assert compile_ok(
        "\\def{wrap}[1]{[#1]}\\wrap{\\rep{2}{ab}}"
    ) == compile_ok("[\\rep{2}{ab}]")


def test_double_hash_is_literal():
    assert compile_ok("\\def{hh}{a##b}\\hh") == compile_ok("a#b")


def test_hash_outside_def_stays_literal():
    assert compile_ok("a#1b") == compile_ok("a#1b")


def test_defs_do_not_leak_into_bytecode():
    bc = compile_ok("\\def{save}{\\ctrl+s}ab")
    assert bc == compile_ok("ab")


def test_def_forwards_compound_arg():
    """Forwarding a multi-element argument into a compound slot (#1 -> {dx, dy})."""
    assert compile_ok("\\def{jump}[1]{\\move{#1}}\\jump{10, 20}") == compile_ok("\\move{10, 20}")


def test_macro_arg_escaped_braces():
    """spec/dsl.md §7: Escaped braces \\{ and \\} are captured verbatim and preserve depth."""
    assert compile_ok("\\def{brace_wrap}[1]{<#1>}\\brace_wrap{a\\{b\\}c}") == compile_ok("<a\\{b\\}c>")


# --- error matrix ---

def test_err_redefine():
    assert "redefined" in compile_err("\\def{save}{a}\\def{save}{b}")


def test_err_conflict_builtin_command():
    assert "conflicts" in compile_err("\\def{delay}{a}")


def test_err_conflict_key_name():
    assert "conflicts" in compile_err("\\def{enter}{a}")


def test_err_conflict_mod_name():
    assert "conflicts" in compile_err("\\def{ctrl}{a}")


def test_err_single_letter_name():
    assert ">=2 chars" in compile_err("\\def{x}{ab}")


def test_err_def_inside_rep():
    assert "top level" in compile_err("\\rep{2}{\\def{save}{a}}")


def test_err_use_before_def():
    # unknown at call time: falls through to key resolution -> unknown key
    compile_err("\\save\\def{save}{\\ctrl+s}")


def test_err_self_recursion():
    assert "recursive" in compile_err("\\def{lo}{\\lo}\\lo")


def test_err_mutual_recursion():
    assert "recursive" in compile_err("\\def{m1}{\\m2}\\def{m2}{\\m1}\\m1")


def test_err_arg_count_mismatch():
    assert "expects 1 arg" in compile_err("\\def{open}[1]{#1}\\open")


def test_err_param_out_of_range():
    assert "#2 used but only 1 arg" in compile_err("\\def{open}[1]{#2}\\open{x}")


def test_macro_expansion_at_limit():
    defs = "".join(f"\\def{{m{i:02d}}}{{\\m{i+1:02d}}}" for i in range(1, MAX_MACRO_EXPAND))
    defs += f"\\def{{m{MAX_MACRO_EXPAND:02d}}}{{a}}"
    compile_ok(defs + "\\m01")


def test_macro_expansion_exceeding_limit_rejected():
    defs = "".join(f"\\def{{m{i:02d}}}{{\\m{i+1:02d}}}" for i in range(1, MAX_MACRO_EXPAND + 1))
    defs += f"\\def{{m{MAX_MACRO_EXPAND + 1:02d}}}{{a}}"
    err = compile_err(defs + "\\m01")
    assert f"macro expansion exceeds {MAX_MACRO_EXPAND} levels" in err


def test_decoupled_macro_and_runtime_nesting():
    # Macro expansion and runtime nesting ceilings are decoupled and independent.
    # A chain of macros containing nested runtime blocks compiles cleanly as long as
    # macro depth <= MAX_MACRO_EXPAND and runtime depth <= MAX_RUNTIME_NEST.
    m_depth = min(MAX_MACRO_EXPAND, 10)
    r_depth = min(MAX_RUNTIME_NEST, 5)
    defs = "".join(f"\\def{{m{i:02d}}}{{\\m{i+1:02d}}}" for i in range(1, m_depth))
    body = "\\set{x}{1}" + "\\rep{2}{" * r_depth + "a" + "}" * r_depth
    defs += f"\\def{{m{m_depth:02d}}}{{{body}}}"
    compile_ok(defs + "\\m01")


def test_error_wrap_chain_preserved():
    # nested failure keeps the "In <ctx>: In <ctx>: ..." chain from the
    # recursive compiler's message shape
    err = compile_err("\\def{mx}{\\bogus{!}}\\rep{2}{\\mx}")
    assert err == "In rep body: In macro mx: @6: Unknown key: bogus"


def test_err_bad_argc():
    compile_err("\\def{open}[x]{a}")


def test_err_unmatched_bracket():
    assert "Unmatched [" in compile_err("\\def{open}[2{a}")
