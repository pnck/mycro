r"""Unit tests for Signal Slot Reception and WAIT overhaul (Step 5).

Covers:
- Grammar and AST: \wait[timeout]{ $slot.sig.val }
- Timeout forms: omitted (INT32_MAX), [Nms], [Ns], [-Nms], [$var]
- Target validation: SlotRef enforcement, unknown signal namespace check
- Runtime reception: pending latch consumption, suspension & wake, nonpositive poll
- Value coalescing & independent slots
- MAX_SLOTS capacity enforcement and wire protocol push rejection
"""

import asyncio
import pytest
from mock_hid import MockHID
from macro import (
    Macro, MacroError, TAG_IMM, TAG_REG, OP_WAIT, OP_END, MAX_SLOTS,
    DEFAULT_WAIT_TIMEOUT_MS, OP_READSLOT, OP_ADD, OP_SET, OP_SLEEP
)
import keymap
from runtime import Runtime
from proto import Dispatcher


def make_runtime():
    rt = Runtime()
    rt.register_signal("net.msg")
    rt.register_signal("go")
    rt.register_ns("net", {})
    rt.register_ns("test", {})
    return rt


def compile_ok(text, rt=None):
    rt_obj = rt or make_runtime()
    m = Macro(MockHID(), rt_obj)
    if "\\#!" in text:
        bc, err = m.compile(text)
    else:
        prefix = ""
        if "go" in text and "\\use{go}" not in text:
            prefix += "\\use{go}"
        bc, err = m.compile(prefix + text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text, rt=None):
    m = Macro(MockHID(), rt or make_runtime())
    bc, err = m.compile(text)
    assert err is not None, "expected compile error, got success"
    return err


def header_strs(bc):
    n = bc[1]
    i = 2
    strs = []
    for _ in range(n):
        ln = bc[i]
        i += 1
        strs.append(bytes(bc[i : i + ln]).decode("latin-1"))
        i += ln
    return strs, i


def run(text, rt=None, hid=None):
    hid = hid or MockHID()
    rt = rt or make_runtime()
    m = Macro(hid, rt)
    prefix = ""
    if "$slot.go" in text and "\\use{go}" not in text:
        prefix += "\\use{go}"
    bc, err = m.compile(prefix + text)
    assert err is None, f"compile error: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    return hid


# --- 1. Syntax & Grammar ---

def test_wait_omitted_timeout_defaults_to_max():
    bc = compile_ok(r"\wait{ $slot.go.val }")
    strs, code = header_strs(bc)
    sig_idx = strs.index("go")
    field_idx = strs.index("val")
    # DEFAULT_WAIT_TIMEOUT_MS is 2147483647 (0x7FFFFFFF)
    expected_imm = DEFAULT_WAIT_TIMEOUT_MS.to_bytes(4, "little", signed=True)
    assert bc[code:] == bytes([OP_WAIT, sig_idx, field_idx, TAG_IMM, *expected_imm, OP_END])


def test_wait_timeout_units():
    # ms literal
    bc_ms = compile_ok(r"\wait[250ms]{ $slot.go.val }")
    _, code = header_strs(bc_ms)
    expected_250 = (250).to_bytes(4, "little", signed=True)
    assert bc_ms[code + 3 : code + 8] == bytes([TAG_IMM, *expected_250])

    # second decimal literal
    bc_s = compile_ok(r"\wait[1.5s]{ $slot.go.val }")
    expected_1500 = (1500).to_bytes(4, "little", signed=True)
    assert bc_s[code + 3 : code + 8] == bytes([TAG_IMM, *expected_1500])

    # negative timeout (nonpositive poll)
    bc_neg = compile_ok(r"\wait[-50ms]{ $slot.go.val }")
    expected_neg = (-50).to_bytes(4, "little", signed=True)
    assert bc_neg[code + 3 : code + 8] == bytes([TAG_IMM, *expected_neg])


def test_wait_timeout_scalar_reg():
    bc = compile_ok(r"\set{t}{120}\wait[$t]{ $slot.go.val }")
    strs, code = header_strs(bc)
    tail = bc[code:]
    at = tail.index(OP_WAIT)
    assert tail[at + 1] == strs.index("go")
    assert tail[at + 2] == strs.index("val")
    assert tail[at + 3] == TAG_REG
    assert tail[at + 4] == 0  # $t register


def test_wait_braced_slot_and_whitespace():
    # Typewriter mode with braced slot variable
    bc_tw = compile_ok(r"\wait[100ms]{ ${slot.go.ready} }")
    strs_tw, _ = header_strs(bc_tw)
    assert "ready" in strs_tw
    assert "go" in strs_tw

    # Free syntax mode with whitespace absorption
    bc_free = compile_ok(r"\#!syntax: free" "\n" r"\use{go}\wait [ 100ms ] { ${slot.go.ready} }")
    strs_free, _ = header_strs(bc_free)
    assert "ready" in strs_free
    assert "go" in strs_free


# --- 2. Target Validation & Namespace Checks ---

def test_wait_rejects_non_slot_ref():
    err1 = compile_err(r"\wait{ $var }")
    assert "wait target must be a slot reference" in err1

    err2 = compile_err(r"\wait{ 123 }")
    assert "wait target must be a slot reference" in err2

    err3 = compile_err(r"\wait{ go }")
    assert "wait target must be a slot reference" in err3

    err4 = compile_err(r"\wait{ $go }")
    assert "wait target must be a slot reference" in err4


def test_wait_rejects_unknown_namespace_without_use():
    err = compile_err(r"\wait{ $slot.unknown_ns.msg.val }")
    assert "unknown signal namespace: unknown_ns" in err


def test_wait_accepts_arbitrary_signal_under_used_namespace():
    # \use{net} activates signal namespace 'net', allowing any slot under net.*
    bc = compile_ok(r"\use{net}\wait{ $slot.net.dynamic_sig.val }")
    strs, code = header_strs(bc)
    assert "net.dynamic_sig" in strs


def test_wait_rejects_invalid_timeout_syntax():
    err = compile_err(r"\wait[abc]{ $slot.go.val }")
    assert "Invalid wait timeout: abc" in err

    err2 = compile_err(r"\wait[]{ $slot.go.val }")
    assert "Empty wait timeout" in err2


# --- 3. Latch and Runtime Execution Semantics ---

def test_wait_consumes_pending_latch_immediately():
    rt = make_runtime()
    rt.fire_signal("go", {"val": 42})
    assert rt.slot_pending("go", "val") == 1

    # First wait consumes latch immediately ($timeout = 0)
    hid = run(r"\wait[100ms]{ $slot.go.val }\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("0")) in hid.events
    assert rt.slot_pending("go", "val") == 0

    # Second wait without new projection times out ($timeout = 1)
    hid2 = run(r"\wait[10ms]{ $slot.go.val }\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("1")) in hid2.events


def test_wait_suspends_and_wakes_on_projection():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile(r"\use{go}\wait[500ms]{ $slot.go.val }\typenum{$timeout}\typenum{$slot.go.val}")
    assert err is None

    async def scenario():
        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        await asyncio.sleep(0.02)
        # Verify latch is 0 while waiting
        assert rt.slot_pending("go", "val") == 0
        rt.fire_signal("go", {"val": 9})
        await task

    asyncio.run(scenario())
    # Timeout is 0
    assert ("kp", keymap.digit_keycode("0")) in hid.events
    # Value is 9
    assert ("kp", keymap.digit_keycode("9")) in hid.events
    # Latch was consumed by the wait
    assert rt.slot_pending("go", "val") == 0


def test_wait_nonpositive_timeout_immediate_poll():
    rt = make_runtime()
    # Case A: pending = 0, timeout <= 0 -> immediate timeout ($timeout = 1)
    hid = run(r"\wait[0ms]{ $slot.go.val }\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("1")) in hid.events

    # Case B: pending = 1, timeout <= 0 -> immediate consume ($timeout = 0)
    rt.fire_signal("go", {"val": 7})
    hid2 = run(r"\wait[-10ms]{ $slot.go.val }\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("0")) in hid2.events
    assert rt.slot_pending("go", "val") == 0


def test_slot_read_does_not_consume_latch():
    rt = make_runtime()
    rt.fire_signal("go", {"val": 55})
    assert rt.slot_pending("go", "val") == 1

    # Reading slot via $slot.go.val does NOT consume latch
    assert rt.read_slot("go", "val") == 55
    assert rt.slot_pending("go", "val") == 1

    # Latch still available for wait
    hid = run(r"\wait[10ms]{ $slot.go.val }\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("0")) in hid.events
    assert rt.slot_pending("go", "val") == 0


def test_multiple_projections_coalesce_pending():
    rt = make_runtime()
    # Multiple projections before wait
    rt.fire_signal("go", {"val": 1})
    rt.fire_signal("go", {"val": 2})
    rt.fire_signal("go", {"val": 3})

    assert rt.read_slot("go", "val") == 3
    assert rt.slot_pending("go", "val") == 1

    # One wait consumes the single pending latch
    hid = run(r"\wait[10ms]{ $slot.go.val }\typenum{$timeout}\typenum{$slot.go.val}", rt=rt)
    assert ("kp", keymap.digit_keycode("0")) in hid.events
    assert ("kp", keymap.digit_keycode("3")) in hid.events
    assert rt.slot_pending("go", "val") == 0


def test_empty_fields_fire_signal_projects_val():
    rt = make_runtime()
    rt.fire_signal("go")  # no fields dict
    assert rt.slot_pending("go", "val") == 1
    assert rt.read_slot("go", "val") == 0

    hid = run(r"\wait[10ms]{ $slot.go.val }\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("0")) in hid.events


# --- 4. MAX_SLOTS Capacity & Protocol Rejection ---

def test_max_slots_capacity_enforcement():
    rt = Runtime()
    # Populate exactly MAX_SLOTS = 256 distinct (name, field) pairs
    for i in range(MAX_SLOTS):
        ok = rt.fire_signal("bulk", {f"f{i}": i})
        assert ok is True

    # 257th slot must be rejected
    rejected = rt.fire_signal("bulk", {"overflow": 999})
    assert rejected is False
    assert rt.read_slot("bulk", "overflow") == 0

    # Existing slot update still succeeds
    ok_existing = rt.fire_signal("bulk", {"f0": 1234})
    assert ok_existing is True
    assert rt.read_slot("bulk", "f0") == 1234


def test_proto_push_rejects_when_slot_capacity_exhausted():
    rt = Runtime()
    rt.register_signal("s")
    class DummyCtx:
        def __init__(self, runtime):
            self.runtime = runtime
    # Fill up 256 slots
    for i in range(MAX_SLOTS):
        rt.fire_signal("s", {f"f{i}": i})

    # Push event targeting a new slot via Dispatcher
    ctx = DummyCtx(rt)
    req_env = {"v": 1, "type": "event.push", "id": 42, "ts": 0,
               "payload": {"name": "s", "fields": {"overflow": 1}}}
    resp = Dispatcher().dispatch(req_env, ctx)
    assert resp["type"] == "error"
    assert resp["payload"]["code"] == "resource_exhausted"
    assert resp["payload"]["message"] == "slot capacity exhausted"


# --- 5. Strict Slot Syntax & Dynamic Signal Registration ---

def test_slot_syntax_rejects_bare_dotted_names():
    # Bare dotted names without $slot. must be strictly rejected
    err1 = compile_err(r"\use{go}\set{x}{$go.val}")
    assert "variable names cannot contain '.'" in err1
    assert "$slot.go.val" in err1

    err2 = compile_err(r"\use{net}\if{ {$net.msg.val, =, 0} }{ a }")
    assert "variable names cannot contain '.'" in err2

    err3 = compile_err(r"\use{net}\guarded{ {$net.msg.val, =, 0} }{ a }")
    assert "variable names cannot contain '.'" in err3

    err4 = compile_err(r"\use{go}\wait{ $go.val }")
    assert "wait target must be a slot reference" in err4


def test_dynamic_signal_registration_and_slot_readback():
    rt = Runtime()
    rt.register_ns("dyn", {})
    # Dynamically register a new signal after runtime init
    rt.register_signal("dyn.status")

    # Push event into the dynamic signal's slot
    ok = rt.fire_signal("dyn.status", {"code": 200, "flag": 1})
    assert ok is True
    assert rt.read_slot("dyn.status", "code") == 200
    assert rt.read_slot("dyn.status", "flag") == 1
    assert rt.slot_pending("dyn.status", "code") == 1

    # Read back via compiled script using $slot.dyn.status.code
    m = Macro(MockHID(), rt)
    bc, err = m.compile(r"\use{dyn}\wait[10ms]{$slot.dyn.status.code}\set{c}{$slot.dyn.status.code}")
    assert err is None
    asyncio.run(m.execute(bc))
    assert m.last_regs[0] == 200
    # Pending latch was consumed by wait
    assert rt.slot_pending("dyn.status", "code") == 0
    # Val remains intact
    assert rt.read_slot("dyn.status", "code") == 200


def test_use_top_level_only():
    """spec/dsl.md §9.3: \\use directives are top-level only."""
    assert "use only allowed at top level" in compile_err("\\rep{1}{\\use{sys}}")
    assert "use only allowed at top level" in compile_err("\\if{ {1, =, 1} }{\\use{sys}}")
    assert "use only allowed at top level" in compile_err("\\guarded{ {1, =, 1} }{\\use{sys}}")
    assert "use only allowed at top level" in compile_err("\\def{mm}{\\use{sys}}\\mm")


def test_use_idempotent_and_order_sensitive():
    """spec/dsl.md §9.3: \\use is idempotent and order-sensitive."""
    rt = Runtime()
    rt.register_ns("sys", {"free": ([], "int", lambda: 1000)})
    m = Macro(MockHID(), rt)
    bc, err = m.compile("\\use{sys}\\use{sys}\\call{sys.free}")
    assert err is None
    bc2, err2 = m.compile("\\call{sys.free}\\use{sys}")
    assert err2 is not None and "undeclared namespace: sys" in err2


def test_slot_readslot_emission():
    """spec/vm.md §3, §4: READSLOT desugars into distinct temp register before instruction."""
    rt = Runtime()
    rt.register_signal("go")
    m = Macro(MockHID(), rt)
    bc, err = m.compile("\\use{go}\\set{x}{0}\\add{x}{$slot.go.code}")
    assert err is None
    strs, code = header_strs(bc)
    tail = bc[code:]
    rs = tail.index(OP_READSLOT)
    assert tail[rs + 1] == 1  # temp reg 1 (x holds reg 0)
    assert tail[rs + 2] == strs.index("go")
    assert tail[rs + 3] == strs.index("code")
    assert rs < tail.index(OP_ADD)


def test_slot_set_and_delay_emission_order():
    """spec/vm.md §3: Slot operands desugar prior to instruction opcodes."""
    rt = Runtime()
    rt.register_signal("go")
    m = Macro(MockHID(), rt)
    bc, err = m.compile("\\use{go}\\set{x}{$slot.go.code}")
    assert err is None
    strs, code = header_strs(bc)
    tail = bc[code:]
    assert tail.index(OP_READSLOT) < tail.index(OP_SET)

    bc2, err2 = m.compile("\\use{go}\\delay{$slot.go.t}")
    assert err2 is None
    tail2 = bc2[header_strs(bc2)[1]:]
    assert tail2.index(OP_READSLOT) < tail2.index(OP_SLEEP)


def test_slot_kdown_and_add_target_rejected():
    """spec/dsl.md §5.1, §6.2: Slots cannot be used as key names or arithmetic lvalues."""
    rt = Runtime()
    rt.register_signal("go")
    m = Macro(MockHID(), rt)
    _, err1 = m.compile("\\use{go}\\kdown{$slot.go.k}")
    assert "kdown/kup takes a key name, not a variable" in err1
    _, err2 = m.compile("\\use{go}\\set{x}{0}\\add{$slot.go.f}{1}")
    assert "signal slot" in err2


def test_slot_multi_operand_execution():
    """spec/dsl.md §6.5: Slots can be used in multiple compound and predicate positions."""
    rt = Runtime()
    rt.register_signal("go")
    rt.fire_signal("go", {"x": 10, "y": 20, "a": 5, "b": 6})
    hid = MockHID()
    m = Macro(hid, rt)

    # Move with two slot coordinates
    bc, err = m.compile("\\use{go}\\move{$slot.go.x, $slot.go.y}")
    assert err is None
    asyncio.run(m.execute(bc))
    assert ("mm", 10, 20) in hid.events

    # Predicate with two slot operands
    bc2, err2 = m.compile("\\use{go}\\if{ {$slot.go.a, <, $slot.go.b} }{\\tap{f1}}")
    assert err2 is None
    asyncio.run(m.execute(bc2))
    assert ("kp", keymap.F1) in hid.events


