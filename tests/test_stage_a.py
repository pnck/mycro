r"""Stage A: user macros (\def/\name) — compile-time expansion.

Correctness is checked formally: an expanded macro must compile to the exact
same bytecode as the hand-written equivalent.
"""

import keymap
from macro import Macro, MAGIC, VERSION, OP_END
from mock_hid import MockHID

H = bytes([MAGIC, VERSION])


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
        "\\def{open}[1]{\\win+r\\delay{0.3}#1\\enter}\\open{notepad}"
    ) == compile_ok("\\win+r\\delay{0.3}notepad\\enter")


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


def test_err_expansion_depth_limit():
    # rep in macro in macro in rep would exceed MAX_NEST=2
    compile_err("\\def{m1}[1]{\\rep{2}{#1}}\\rep{2}{\\rep{2}{\\m1{a}}}")


def test_err_bad_argc():
    compile_err("\\def{open}[x]{a}")


def test_err_unmatched_bracket():
    assert "Unmatched [" in compile_err("\\def{open}[2{a}")
