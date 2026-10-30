r"""Unit tests for Step 6: Handle Type Invariants & $err Fault Routing.

Normative specs:
- spec/dsl.md §6.2-§6.3, §8.1
- spec/vm.md §1.2, §4, §7
- spec/runtime.md §3.0, §4

Covers:
- Arithmetic handle faults (\add, \sub): destination unchanged, $err = ERR_HANDLE_MISUSE (8)
- Rendering handle fault (\typenum): empty output, $err = ERR_HANDLE_MISUSE (8)
- Predicate handle fault (\if): condition evaluates to FALSE, $err = ERR_HANDLE_MISUSE (8)
- Register write mediation: shared references across registers, reference counting, self-assignment
- CALL_EXT $ret / $err contract: success scalar, success handle, error status
"""

import asyncio
import pytest
from mock_hid import MockHID
from macro import (
    Macro, MacroError,
    REG_RET, REG_TIMEOUT, REG_ERR,
    ERR_OK, ERR_GENERIC, ERR_INVALID_ARG, ERR_NO_PROVIDER, ERR_OUT_OF_BOUNDS,
    ERR_HANDLE_INVALID, ERR_RESOURCE_EXHAUSTED, ERR_TIMEOUT, ERR_HANDLE_MISUSE,
)
import keymap
from runtime import Runtime


def make_runtime():
    rt = Runtime()
    rt.register_ns("img", {
        "snap": (["rect"], "snap",
                 lambda x, y, w, h: rt.new_handle("snap", {"x": x, "y": y, "w": w, "h": h})),
        "use": (["snap"], "int",
                lambda h: 1 if rt.handle_data(h, "snap") is not None else -1),
        "fail": ([], "snap", lambda: (0, ERR_RESOURCE_EXHAUSTED)),
    })
    rt.register_ns("sys", {
        "free": ([], "int", lambda: 12345),
    })
    return rt


def run(text, rt=None, hid=None):
    hid = hid or MockHID()
    rt = rt or make_runtime()
    m = Macro(hid, rt)
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    return hid, rt


# --- 1. Arithmetic Handle Misuse Faults ---

def test_add_with_handle_rhs_sets_err_and_preserves_dst():
    # \set{x}{10}\call{img.snap}{0,0,1,1}\add{x}{$ret}
    # $ret holds a snap handle -> \add{x}{$ret} must set $err = ERR_HANDLE_MISUSE
    # and destination x must remain 10
    hid, rt = run(
        r"\set{x}{10}"
        r"\call{img.snap}{0,0,1,1}"
        r"\add{x}{$ret}"
        r"\typenum{$x}"
        r"\typenum{$err}"
    )
    # Keystrokes for x=10 followed by err=8
    events = [e for e in hid.events if e[0] == "kp"]
    # 1, 0, then 8
    assert events == [
        ("kp", keymap.digit_keycode("1")),
        ("kp", keymap.digit_keycode("0")),
        ("kp", keymap.digit_keycode("8")),
    ]


def test_sub_with_handle_rhs_sets_err_and_preserves_dst():
    # \set{x}{20}\call{img.snap}{0,0,1,1}\sub{x}{$ret}
    hid, rt = run(
        r"\set{x}{20}"
        r"\call{img.snap}{0,0,1,1}"
        r"\sub{x}{$ret}"
        r"\typenum{$x}"
        r"\typenum{$err}"
    )
    events = [e for e in hid.events if e[0] == "kp"]
    # 2, 0, then 8
    assert events == [
        ("kp", keymap.digit_keycode("2")),
        ("kp", keymap.digit_keycode("0")),
        ("kp", keymap.digit_keycode("8")),
    ]


# --- 2. Typenum Handle Misuse Fault ---

def test_typenum_handle_renders_empty_and_sets_err():
    # \call{img.snap}{0,0,1,1}\typenum{$ret}
    # $ret holds a handle -> empty keystroke output, $err = 8
    hid, rt = run(
        r"\call{img.snap}{0,0,1,1}"
        r"\typenum{$ret}"
        r"\typenum{$err}"
    )
    events = [e for e in hid.events if e[0] == "kp"]
    # only the digit '8' from \typenum{$err}
    assert events == [("kp", keymap.digit_keycode("8"))]


# --- 3. Predicate Triplet Handle Misuse Fault ---

def test_predicate_with_handle_evaluates_false_and_sets_err():
    # \call{img.snap}{0,0,1,1} (succeeds, $ret is handle)
    # \if{ {$ret, =, 1} }{\set{branch}{1}}{\set{branch}{2}}
    # The condition triplet with handle operand must evaluate to FALSE and set $err = 8.
    # Therefore the else branch (branch=2) is taken!
    hid, rt = run(
        r"\set{branch}{0}"
        r"\call{img.snap}{0,0,1,1}"
        r"\if{ {$ret, =, 1} }{\set{branch}{1}}{\set{branch}{2}}"
        r"\typenum{$branch}"
        r"\typenum{$err}"
    )
    events = [e for e in hid.events if e[0] == "kp"]
    assert events == [
        ("kp", keymap.digit_keycode("2")),  # else branch taken
        ("kp", keymap.digit_keycode("8")),  # $err = 8
    ]


def test_predicate_with_handle_not_equal_also_evaluates_false():
    # Even with != operator, a Handle operand evaluates to FALSE (spec/runtime.md §4 #5)
    hid, rt = run(
        r"\set{branch}{0}"
        r"\call{img.snap}{0,0,1,1}"
        r"\if{ {$ret, !=, 0} }{\set{branch}{1}}{\set{branch}{2}}"
        r"\typenum{$branch}"
        r"\typenum{$err}"
    )
    events = [e for e in hid.events if e[0] == "kp"]
    assert events == [
        ("kp", keymap.digit_keycode("2")),  # else branch taken because cond is FALSE
        ("kp", keymap.digit_keycode("8")),  # $err = 8
    ]


# --- 4. Shared Reference Lifecycle & Overwrite Mediation ---

def test_shared_reference_counting_across_registers():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int", lambda: seen.append(len(rt._handles)) or 0)})

    # 1. Snap allocates handle 1
    # 2. $ret, $a, $b all reference handle 1
    # 3. $ret overwritten by sys.free -> handle 1 still alive ($a, $b)
    # 4. $a overwritten by 0 -> handle 1 still alive ($b)
    # 5. $b overwritten by 0 -> handle 1 released (0 references)
    run(
        r"\call{img.snap}{0,0,1,1}"
        r"\set{a}{$ret}"
        r"\set{b}{$a}"
        r"\call{probe.n}"        # seen[0]: 1 handle alive (ret, a, b)
        r"\call{sys.free}"       # overwrites $ret with scalar
        r"\call{probe.n}"        # seen[1]: 1 handle alive (a, b)
        r"\set{a}{0}"            # overwrites $a
        r"\call{probe.n}"        # seen[2]: 1 handle alive (b)
        r"\set{b}{0}"            # overwrites $b -> 0 refs -> released!
        r"\call{probe.n}",       # seen[3]: 0 handles alive
        rt=rt,
    )
    assert seen == [1, 1, 1, 0]
    assert len(rt._handles) == 0


def test_self_assignment_preserves_handle():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int", lambda: seen.append(len(rt._handles)) or 0)})

    # Self-assignment \set{a}{$a} must NOT release the handle
    run(
        r"\call{img.snap}{0,0,1,1}"
        r"\set{a}{$ret}"
        r"\call{sys.free}"
        r"\set{a}{$a}"
        r"\call{probe.n}"
        r"\call{img.use}{$a}",
        rt=rt,
    )
    assert seen == [1]


# --- 5. CALL_EXT Return and Error Status Contract ---

def test_call_ext_success_scalar():
    hid, rt = run(
        r"\call{sys.free}"
        r"\typenum{$err}"
    )
    # sys.free returns scalar, $err = ERR_OK (0)
    events = [e for e in hid.events if e[0] == "kp"]
    assert events == [("kp", keymap.digit_keycode("0"))]


def test_call_ext_failure_sets_err_and_zero_ret():
    hid, rt = run(
        r"\call{img.fail}"
        r"\typenum{$ret}"
        r"\typenum{$err}"
    )
    # img.fail returns (0, ERR_RESOURCE_EXHAUSTED=6)
    events = [e for e in hid.events if e[0] == "kp"]
    assert events == [
        ("kp", keymap.digit_keycode("0")),  # $ret = 0
        ("kp", keymap.digit_keycode("6")),  # $err = 6 (ERR_RESOURCE_EXHAUSTED)
    ]
