r"""Unit tests for Flat Iterative Guard \guarded (Step 7).

Normative references:
- spec/dsl.md §6.2, §8.1, §8.3
- spec/vm.md §4.1, §4.2, §6.1, §6.2, §6.3
"""

import asyncio
import pytest
from mock_hid import MockHID
from macro import (
    Macro, MacroError,
    OP_GUARD_ENTER, OP_GUARD_EXIT, OP_JMP, OP_END,
    MAX_GUARD_NEST, DELAY_SLICE_QUANTUM,
    ERR_HANDLE_MISUSE, REG_ERR, REG_TIMEOUT,
)
import keymap
from runtime import Runtime


def make_runtime():
    rt = Runtime()
    rt.register_signal("net.msg")
    rt.register_signal("sensor.temp")
    rt.register_ns("net", {})
    rt.register_ns("sensor", {})
    return rt


def compile_ok(text, rt=None):
    m = Macro(MockHID(), rt or make_runtime())
    bc, err = m.compile(text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text, rt=None):
    m = Macro(MockHID(), rt or make_runtime())
    bc, err = m.compile(text)
    assert err is not None, f"expected compile error, got success: {text}"
    return err


def run(text, rt=None, **kwargs):
    hid = MockHID()
    m = Macro(hid, rt or make_runtime())
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    kwargs.setdefault("default_delay_ms", 0)
    kwargs.setdefault("click_hold_ms", 0)
    asyncio.run(m.execute(bc, **kwargs))
    return hid


def typed_keys(hid):
    """Return sequence of characters typed via 'kp' events."""
    out = []
    for ev in hid.events:
        if ev[0] == "kp":
            kc = ev[1]
            for ch in "abcdefghijklmnopqrstuvwxyz":
                if keymap.letter_keycode(ch) == kc:
                    out.append(ch)
                    break
            else:
                for ch in "0123456789":
                    if keymap.digit_keycode(ch) == kc:
                        out.append(ch)
                        break
    return "".join(out)


# --- 1. Grammar & Parsing Tests ---

def test_guarded_single_triplet():
    bc = compile_ok(r"\set{x}{1}\guarded{ {$x, =, 1} }{ a }")
    assert OP_GUARD_ENTER in bc
    assert OP_GUARD_EXIT in bc


def test_guarded_dual_brace():
    bc = compile_ok(r"\set{x}{1}\guarded{ {{$x}, {=}, {1}} }{ a }")
    assert OP_GUARD_ENTER in bc
    assert OP_GUARD_EXIT in bc


def test_guarded_all_multiple_triplets():
    bc = compile_ok(r"\set{a}{1}\set{b}{2}\set{c}{3}\guarded{ \all{ {$a, =, 1}, {$b, !=, 2}, {$c, <, 3} } }{ a }")
    assert OP_GUARD_ENTER in bc
    m = Macro(MockHID(), make_runtime())
    dis = m.disassemble(bc)
    assert "ALL(3)" in dis


def test_guarded_any_multiple_triplets():
    bc = compile_ok(r"\set{a}{1}\set{b}{2}\guarded{ \any{ {$a, =, 1}, {$b, >, 10} } }{ a }")
    assert OP_GUARD_ENTER in bc
    m = Macro(MockHID(), make_runtime())
    dis = m.disassemble(bc)
    assert "ANY(2)" in dis


def test_guarded_immediate_lhs():
    bc = compile_ok(r"\set{x}{1}\guarded{ {1, =, $x} }{ a }")
    assert OP_GUARD_ENTER in bc


def test_guarded_slot_operand():
    bc = compile_ok(r"\use{net}\guarded{ {$slot.net.msg.val, >, 10} }{ a }")
    assert OP_GUARD_ENTER in bc


def test_guarded_throttle_formats():
    # 50ms
    bc1 = compile_ok(r"\set{x}{1}\guarded[50ms]{ {$x, =, 1} }{ a }")
    m = Macro(MockHID(), make_runtime())
    assert "[50ms]" in m.disassemble(bc1)

    # 0.1s -> 100ms
    bc2 = compile_ok(r"\set{x}{1}\guarded[0.1s]{ {$x, =, 1} }{ a }")
    assert "[100ms]" in m.disassemble(bc2)

    # 0ms -> explicit [0ms]
    bc3 = compile_ok(r"\set{x}{1}\guarded[0ms]{ {$x, =, 1} }{ a }")
    assert "[0ms]" in m.disassemble(bc3)

    # bare integer
    bc4 = compile_ok(r"\set{x}{1}\guarded[25]{ {$x, =, 1} }{ a }")
    assert "[25ms]" in m.disassemble(bc4)


def test_guarded_no_throttle_default():
    bc = compile_ok(r"\set{x}{1}\guarded{ {$x, =, 1} }{ a }")
    m = Macro(MockHID(), make_runtime())
    dis = m.disassemble(bc)
    assert "GUARD_ENTER" in dis
    assert "ms]" not in dis


def test_guarded_dynamic_throttle_rejected():
    err = compile_err(r"\set{x}{1}\guarded[$t]{ {$x, =, 1} }{ a }")
    assert "guarded throttle must be static literal" in err

    err2 = compile_err(r"\set{x}{1}\guarded[${t}]{ {$x, =, 1} }{ a }")
    assert "guarded throttle must be static literal" in err2


def test_guarded_nested_logic_rejected():
    # \all inside \any
    err1 = compile_err(r"\set{a}{1}\set{b}{2}\guarded{ \any{ \all{ {$a,=,1}, {$b,=,2} } } }{ a }")
    assert "guarded does not support nested predicate logic; max 8 flat triplets" in err1

    # \any inside \all
    err2 = compile_err(r"\set{a}{1}\set{b}{2}\guarded{ \all{ \any{ {$a,=,1}, {$b,=,2} } } }{ a }")
    assert "guarded does not support nested predicate logic; max 8 flat triplets" in err2


def test_guarded_max_triplets_rejected():
    # 9 triplets in \all
    init = "".join(f"\\set{{x{i}}}{{{i}}}" for i in range(9))
    triplets = ", ".join(f"{{$x{i}, =, {i}}}" for i in range(9))
    err = compile_err(f"{init}\\guarded{{ \\all{{ {triplets} }} }}{{ a }}")
    assert "guarded does not support nested predicate logic; max 8 flat triplets" in err


def test_guarded_nesting_limit_rejected():
    # 5 levels of \guarded nesting (limit is 4)
    code = (
        r"\use{net}"
        r"\guarded{ {$slot.net.msg.a,=,1} }{"
        r"\guarded{ {$slot.net.msg.b,=,1} }{"
        r"\guarded{ {$slot.net.msg.c,=,1} }{"
        r"\guarded{ {$slot.net.msg.d,=,1} }{"
        r"\guarded{ {$slot.net.msg.e,=,1} }{ a }"
        r"}}}}"
    )
    err = compile_err(code)
    assert "guard nesting exceeds 4 levels" in err


# --- 2. Runtime Execution & Scheduling Tests ---

def test_guarded_normal_completion():
    """When guard condition holds throughout execution, protected body executes,
    on_abort is skipped, and following statements execute."""
    hid = run(r"\set{x}{1}\guarded{ {$x, =, 1} }{ a }{ b }c")
    assert typed_keys(hid) == "ac"


def test_guarded_entry_abort_with_on_abort():
    """When guard condition is false on entry, protected body is skipped,
    jumps directly to on_abort."""
    hid = run(r"\set{x}{0}\guarded{ {$x, =, 1} }{ a }{ b }c")
    assert typed_keys(hid) == "bc"


def test_guarded_entry_abort_without_on_abort():
    """When guard condition is false on entry and on_abort is omitted,
    body is skipped and execution resumes after guarded block."""
    hid = run(r"\set{x}{0}\guarded{ {$x, =, 1} }{ a }c")
    assert typed_keys(hid) == "c"


def test_guarded_instruction_boundary_abort():
    """Guard condition becomes false due to a variable assignment inside body;
    aborts before next instruction and jumps to on_abort."""
    hid = run(r"\set{x}{1}\guarded{ {$x, =, 1} }{ a\set{x}{2}b }{ f }c")
    # 'a' executes, then \set{x}{2} executes, then instruction boundary check fails!
    # 'b' is never executed. Jumps to on_abort ('f'), then continues ('c').
    assert typed_keys(hid) == "afc"


def test_guarded_outer_guard_entry_precedence():
    """Outer guard evaluation fails during entry inspection of inner guard;
    outer guard's abort handler is taken."""
    code_free = (
        "\\#!syntax: free\n"
        r"\set{x}{1}\set{y}{1}"
        r"\guarded{ {$x, =, 1} }{"
        r"  a"
        r"  \set{x}{0}"
        r"  \guarded{ {$y, =, 1} }{"
        r"    b"
        r"  }{"
        r"    d"
        r"  }"
        r"}{"
        r"  e"
        r"}"
    )
    hid = run(code_free)
    # 'a' executes. Then \set{x}{0} executes.
    # At \guarded entry, outermost guard ($x == 1) is evaluated first!
    # Outermost guard fails -> outer_abort ('e') is taken.
    assert typed_keys(hid) == "ae"


def test_guarded_sleep_slicing_abort():
    r"""During \delay, an asynchronous slot projection makes guard false;
    suspension slicing wakes up within DELAY_SLICE_QUANTUM (20ms) and aborts."""
    rt = make_runtime()
    rt.fire_signal("net.msg", {"stop": 0})

    code = (
        "\\#!syntax: free\n"
        r"\use{net}"
        r"\guarded{ {$slot.net.msg.stop, =, 0} }{"
        r"  a"
        r"  \delay{0.2s}"
        r"  b"
        r"}{"
        r"  c"
        r"}"
    )

    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile(code)
    assert err is None

    async def scenario():
        # Fire signal stop=1 after 30ms to interrupt the 200ms sleep
        async def background_project():
            await asyncio.sleep(0.03)
            rt.fire_signal("net.msg", {"stop": 1})

        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        bg = asyncio.create_task(background_project())
        await asyncio.gather(task, bg)

    asyncio.run(scenario())
    # 'a' executed, then sleep was sliced and aborted at ~40ms, 'b' skipped, 'c' executed!
    assert typed_keys(hid) == "ac"


def test_guarded_wait_slicing_abort():
    r"""During \wait with active guard:
    When guard becomes false during wait:
    - waiter is unregistered
    - target slot's pending latch is preserved
    - prior $timeout is preserved
    - abort handler is executed"""
    rt = make_runtime()
    rt.fire_signal("net.msg", {"guard_val": 1, "target": 0})
    # Reset pending latches
    rt.consume_slot_pending("net.msg", "target")

    code = (
        "\\#!syntax: free\n"
        r"\use{net}"
        r"\wait[0ms]{ $slot.net.msg.missing }"  # sets $timeout = 1
        r"\guarded{ {$slot.net.msg.guard_val, =, 1} }{"
        r"  a"
        r"  \wait[500ms]{ $slot.net.msg.target }"
        r"  b"
        r"}{"
        r"  c\typenum{$timeout}"
        r"}"
    )

    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile(code)
    assert err is None

    async def scenario():
        async def background_violation():
            await asyncio.sleep(0.03)
            # Both project target (with pending=1) AND violate guard (guard_val=0)
            rt.fire_signal("net.msg", {"guard_val": 0, "target": 100})

        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        bg = asyncio.create_task(background_violation())
        await asyncio.gather(task, bg)

    asyncio.run(scenario())

    # 1. Output shows 'a' then abort 'c1' ('b' was not reached, $timeout remained 1)
    assert typed_keys(hid) == "ac1"

    # 2. Target slot's pending latch MUST be preserved (1, not consumed)!
    assert rt.slot_pending("net.msg", "target") == 1


def test_guarded_wait_preserves_prior_timeout():
    """Verify that guard abort during wait preserves prior $timeout value (0)."""
    rt = make_runtime()
    rt.fire_signal("net.msg", {"active": 1, "dummy": 1})
    # dummy arrives, so \wait[0ms]{$slot.net.msg.dummy} sets $timeout = 0
    rt.consume_slot_pending("net.msg", "target")

    code = (
        "\\#!syntax: free\n"
        r"\use{net}"
        r"\wait[0ms]{ $slot.net.msg.dummy }"  # sets $timeout = 0
        r"\guarded{ {$slot.net.msg.active, =, 1} }{"
        r"  \wait[500ms]{ $slot.net.msg.target }"
        r"}{"
        r"  \typenum{$timeout}"
        r"}"
    )

    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile(code)
    assert err is None

    async def scenario():
        async def bg_viol():
            await asyncio.sleep(0.03)
            rt.fire_signal("net.msg", {"active": 0})

        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        bg = asyncio.create_task(bg_viol())
        await asyncio.gather(task, bg)

    asyncio.run(scenario())
    # on_abort typed $timeout which preserved prior 0!
    assert typed_keys(hid) == "0"


def test_guarded_loop_rollback():
    r"""Inner loop frames inside \guarded are deleted on guard abort,
    while outer loop frames remain intact and continue repeating."""
    code = (
        "\\#!syntax: free\n"
        r"\set{outer}{0}"
        r"\rep{3}{"
        r"  \set{x}{1}"
        r"  \guarded{ {$x, =, 1} }{"
        r"    a"
        r"    \rep{5}{"
        r"      \set{x}{2}"
        r"      b"
        r"    }"
        r"  }{"
        r"    c"
        r"  }"
        r"}"
    )
    hid = run(code)
    # In each of the 3 outer iterations:
    # 'a' is typed, inner rep starts, \set{x}{2} sets x to 2.
    # Instruction boundary check aborts! 'b' is never typed!
    # Inner loop is rolled back. Jumps to on_abort ('c').
    # Outer loop completes iteration 1, then repeats for iteration 2, then iteration 3!
    # Result: "acacac"
    assert typed_keys(hid) == "acacac"


def test_guarded_handle_misuse_fault():
    r"""Predicate triplet with a Handle operand in \guarded evaluates to False
    and sets $err = ERR_HANDLE_MISUSE (8)."""
    rt = make_runtime()
    rt.register_ns("img", {
        "snap": ((), "img_handle", lambda: rt.new_handle("snap", b"img_data")),
    })

    code = (
        "\\#!syntax: free\n"
        r"\use{img}"
        r"\call{img.snap}"
        r"\set{h}{$ret}"
        r"\guarded{ {$h, =, 1} }{"
        r"  a"
        r"}{"
        r"  b\typenum{$err}"
        r"}"
    )
    hid = run(code, rt)
    # Handle in guard comparison -> evaluates to False, sets $err = 8, aborts to 'b8'!
    assert typed_keys(hid) == "b8"


def test_guarded_hardware_reset_on_abort():
    """Guard abort performs hardware reset: keys_release_all() and mouse_release(31)."""
    rt = make_runtime()
    rt.fire_signal("net.msg", {"stop": 0})

    code = (
        "\\#!syntax: free\n"
        r"\use{net}"
        r"\guarded{ {$slot.net.msg.stop, =, 0} }{"
        r"  \kdown{ctrl}"
        r"  \delay{0.1s}"
        r"}{"
        r"  x"
        r"}"
    )
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile(code)
    assert err is None

    async def scenario():
        async def bg():
            await asyncio.sleep(0.02)
            rt.fire_signal("net.msg", {"stop": 1})

        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        bg_task = asyncio.create_task(bg())
        await asyncio.gather(task, bg_task)

    asyncio.run(scenario())
    # Events must include 'kra' (keys_release_all) and 'mr' (mouse_release 31)
    ev_types = [ev[0] for ev in hid.events]
    assert "kp" in ev_types  # from kdown or 'x'
    assert "kra" in ev_types  # keys_release_all from abort
    assert any(ev[0] == "mr" and ev[1] == 31 for ev in hid.events)
