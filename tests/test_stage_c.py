r"""Stage C: \use / \call / \wait / signal slots / string table / runtime registry."""

import asyncio

import keymap
from macro import (
    Macro,
    MAGIC, VERSION, TAG_IMM, TAG_REG, TAG_STR, REG_RET, REG_TIMEOUT,
    OP_SET, OP_ADD, OP_SLEEP, OP_KEY_DOWN, OP_READSLOT, OP_WAIT, OP_CALL_EXT,
    OP_TYPEREG, OP_END,
)
from runtime import Runtime
from mock_hid import MockHID


def make_runtime():
    rt = Runtime()
    rt.register_ns("sys", {"free": ([], lambda: 12345)})
    rt.register_ns("img", {
        "load": (["str"], lambda name: 7),
        "match": (["tpl"], lambda h: 1 if h == 7 else 0),
        "peek": (["int", "int"], lambda x, y: x + y),
    })
    rt.register_signal("go")
    rt.register_signal("net.msg")
    return rt


def compile_ok(text, rt=None):
    bc, err = Macro(MockHID(), rt or make_runtime()).compile(text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text, rt=None):
    bc, err = Macro(MockHID(), rt or make_runtime()).compile(text)
    assert bc is None and err, f"expected compile error, got success: {text}"
    return err


def header_strs(bc):
    """Parse the v3 header: returns (strings, code_start)."""
    assert bc[0] == MAGIC and bc[1] == VERSION
    n = bc[2]
    i = 3
    strs = []
    for _ in range(n):
        ln = bc[i]
        i += 1
        strs.append(bytes(bc[i : i + ln]).decode())
        i += ln
    return strs, i


# --- string table ---

def test_call_ext_string_table():
    bc = compile_ok("\\call{sys.free}")
    strs, code = header_strs(bc)
    assert strs == ["sys", "free"]
    assert bc[code:] == bytes([OP_CALL_EXT, 0, 1, 0, OP_END])


def test_string_dedup():
    bc = compile_ok("\\call{img.load}{a}\\call{img.load}{a}")
    strs, _ = header_strs(bc)
    # ns/fn names dedup across calls; 'a' interned once
    assert strs.count("a") == 1
    assert strs.count("img") == 1
    assert strs.count("load") == 1


def test_str_arg():
    bc = compile_ok("\\call{img.load}{logo}")
    strs, code = header_strs(bc)
    assert bc[code:] == bytes([
        OP_CALL_EXT, strs.index("img"), strs.index("load"), 1,
        TAG_STR, strs.index("logo"), OP_END,
    ])


def test_handle_arg_must_be_var():
    compile_ok("\\set{h}{0}\\call{img.match}{$h}")
    assert "must be a variable" in compile_err("\\call{img.match}{7}")


def test_int_args():
    bc = compile_ok("\\set{x}{3}\\call{img.peek}{1}{$x}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    at = tail.index(OP_CALL_EXT)
    assert tail[at + 1] == strs.index("img")
    assert tail[at + 2] == strs.index("peek")
    assert tail[at + 3] == 2  # argc
    assert tail[at + 4] == TAG_IMM and tail[at + 5 : at + 9] == bytes([1, 0, 0, 0])
    assert tail[at + 9] == TAG_REG and tail[at + 10] == 0


# --- \use / signature validation ---

def test_use_ok():
    compile_ok("\\use{sys}\\call{sys.free}")


def test_use_unknown_ns():
    assert "Unknown namespace" in compile_err("\\use{nope}")


def test_call_unknown_fn():
    assert "Unknown ext function" in compile_err("\\call{sys.nope}")


def test_call_bad_target():
    assert "ns.fn" in compile_err("\\call{sysfree}")


def test_call_argc_mismatch():
    assert "expects 1 arg" in compile_err("\\call{img.load}")
    assert "expects 1 arg" in compile_err("\\call{img.load}{a}{b}")


def test_call_zero_arg_rejects_extra():
    assert "expects 0 arg" in compile_err("\\call{sys.free}{x}")


def test_str_arg_rejects_var():
    assert "no string variables" in compile_err("\\set{x}{1}\\call{img.load}{$x}")


def test_call_requires_runtime():
    bc, err = Macro(MockHID()).compile("\\call{sys.free}")  # no runtime
    assert err and "runtime registry" in err


# --- \wait emission ---

def test_wait_forever():
    bc = compile_ok("\\wait{go}")
    strs, code = header_strs(bc)
    idx = strs.index("go")
    assert bc[code:] == bytes([OP_WAIT, idx, TAG_IMM, 0, 0, 0, 0, OP_END])


def test_wait_timeout_imm():
    bc = compile_ok("\\wait{go}{2}")
    strs, code = header_strs(bc)
    assert bc[code + 2 :] == bytes([TAG_IMM, 2000 & 0xFF, 2000 >> 8, 0, 0, OP_END])


def test_wait_timeout_var():
    bc = compile_ok("\\set{t}{500}\\wait{go}{$t}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    at = tail.index(OP_WAIT)
    assert tail[at + 1] == strs.index("go")
    assert tail[at + 2] == TAG_REG and tail[at + 3] == 0


def test_wait_unknown_signal():
    assert "Unknown signal" in compile_err("\\wait{nope}")


# --- slots ---

def test_slot_readslot_emission():
    bc = compile_ok("\\set{x}{0}\\add{x}{$go.code}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    # READSLOT targets a temp register (first free beyond the var block:
    # x holds reg 0, so the slot temp is reg 1) and must precede the whole
    # ADD instruction — a desugar landing mid-instruction corrupts the stream
    rs = tail.index(OP_READSLOT)
    assert tail[rs + 1] == 1
    assert tail[rs + 2] == strs.index("go")
    assert tail[rs + 3] == strs.index("code")
    assert rs < tail.index(OP_ADD)


def test_slot_unknown_ns():
    assert "Unknown signal" in compile_err("\\set{x}{0}\\add{x}{$nope.f}")


def test_val_slot():
    bc = compile_ok("\\val{go.code}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    assert tail[0] == OP_READSLOT and tail[1] == 0  # temp reg 0 (no vars)
    assert tail[4] == OP_TYPEREG and tail[5] == 0


# --- slot operand codegen: placement + distinct temps ---

def test_slot_set_emission_order():
    bc = compile_ok("\\set{x}{$go.code}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    assert tail.index(OP_READSLOT) < tail.index(OP_SET)


def test_slot_delay_emission_order():
    bc = compile_ok("\\delay{$go.t}")
    tail = bc[header_strs(bc)[1]:]
    assert tail.index(OP_READSLOT) < tail.index(OP_SLEEP)


def test_slot_kdown_emission_order():
    bc = compile_ok("\\kdown{$go.k}")
    tail = bc[header_strs(bc)[1]:]
    assert tail.index(OP_READSLOT) < tail.index(OP_KEY_DOWN)


def test_slot_move_two_slots():
    rt = make_runtime()
    rt.fire_signal("go", {"x": 10, "y": 20})
    hid = run("\\move{$go.x,$go.y}", rt=rt)
    assert ("mm", 10, 20) in hid.events


def test_slot_ifnum_both_operands():
    rt = make_runtime()
    rt.fire_signal("go", {"a": 5, "b": 6})
    hid = run("\\ifnum{$go.a}{<}{$go.b}{\\call{sys.free}\\val{ret}}", rt=rt)
    for d in "12345":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_slot_call_two_slot_args():
    captured = {}
    rt = Runtime()
    rt.register_ns("pair", {"sum2": (["int", "int"],
                                     lambda a, b: captured.__setitem__("v", (a, b)) or a + b)})
    rt.register_signal("go")
    rt.fire_signal("go", {"x": 3, "y": 4})
    hid = run("\\call{pair.sum2}{$go.x}{$go.y}\\val{ret}", rt=rt)
    assert captured["v"] == (3, 4)
    for d in "7":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_add_slot_target_rejected():
    assert "signal slot" in compile_err("\\set{x}{0}\\add{$go.f}{1}")


def test_wait_ignores_stale_latch():
    # A fire with no waiter must NOT wake a later \wait: waits observe the
    # next fire only (slot values stay readable as last-published state)
    rt = make_runtime()
    rt.fire_signal("go", {"n": 1})
    hid = run("\\wait{go}{0.05}\\val{timeout}", rt=rt)  # must time out
    assert ("kp", keymap.digit_keycode("1")) in hid.events


# --- runtime: ext calls ---

def run(text, rt=None, hid=None):
    hid = hid or MockHID()
    rt = rt or make_runtime()
    m = Macro(hid, rt)
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    return hid


def test_run_call_ret_rendered():
    hid = run("\\call{sys.free}\\val{ret}")
    # 12345 typed out digit by digit
    for d in "12345":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_slot_ref_dotted_signal_name():
    # Signal names may contain dots (net.msg): $sig.field splits at the
    # LAST dot, so $net.msg.x reads field x of signal net.msg
    rt = make_runtime()
    rt.fire_signal("net.msg", {"x": 120})
    hid = run("\\set{r}{$net.msg.x}\\val{r}", rt=rt)
    for d in "120":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_slot_ref_dotted_signal_unknown_is_error():
    assert "Unknown signal" in compile_err("\\set{r}{$net.nope.x}")


def test_run_call_str_arg_and_handle_flow():
    captured = {}

    def load(name):
        captured["name"] = name
        return 7

    rt = make_runtime()
    rt.register_ns("img", {"load": (["str"], load), "match": (["tpl"], lambda h: 1)})
    hid = run("\\call{img.load}{logo}\\call{img.match}{$ret}\\val{ret}", rt=rt)
    assert captured["name"] == "logo"
    # match(7) -> 1 rendered
    assert ("kp", keymap.digit_keycode("1")) in hid.events


def test_run_call_async_fn_awaited():
    async def slow():
        await asyncio.sleep(0.01)
        return 42

    rt = make_runtime()
    rt.register_ns("img", {"load": (["str"], lambda n: 0), "match": (["tpl"], lambda h: 0),
                           "peek": (["int", "int"], lambda a, b: a + b)})
    rt.register_ns("test", {"slow": ([], slow)})
    hid = run("\\call{test.slow}\\val{ret}", rt=rt)
    assert ("kp", keymap.digit_keycode("4")) in hid.events
    assert ("kp", keymap.digit_keycode("2")) in hid.events


def test_run_call_int_args_compute():
    hid = run("\\call{img.peek}{20}{22}\\val{ret}")
    assert ("kp", keymap.digit_keycode("4")) in hid.events
    assert ("kp", keymap.digit_keycode("2")) in hid.events


# --- runtime: wait + slots ---

def test_run_wait_fired():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile("\\wait{go}\\val{timeout}")
    assert err is None

    async def scenario():
        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        await asyncio.sleep(0.01)
        rt.fire_signal("go")
        await task

    asyncio.run(scenario())
    assert ("kp", keymap.digit_keycode("0")) in hid.events  # timeout == 0


def test_run_wait_timeout():
    hid = run("\\wait{go}{0.05}\\val{timeout}")
    assert ("kp", keymap.digit_keycode("1")) in hid.events  # timed out


def test_run_wait_then_slot():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile("\\wait{go}\\val{go.code}")
    assert err is None

    async def scenario():
        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        await asyncio.sleep(0.01)
        rt.fire_signal("go", {"code": 5})
        await task

    asyncio.run(scenario())
    assert ("kp", keymap.digit_keycode("5")) in hid.events


def test_run_slot_missing_field_is_zero():
    hid = run("\\val{go.code}")
    assert ("kp", keymap.digit_keycode("0")) in hid.events


def test_run_abort_during_wait():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile("\\kdown{ctrl}\\wait{go}")
    assert err is None

    async def scenario():
        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        await asyncio.sleep(0.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    # B2: finally must have released everything even mid-wait
    assert ("kra",) in hid.events
    assert ("mr", keymap.BTN_ALL) in hid.events


def test_disassemble_stage_c():
    m = Macro(MockHID(), make_runtime())
    bc, err = m.compile("\\call{img.peek}{1,2}\\wait{go}{3}\\val{go.code}")
    # note: ext args are separate brace groups, not comma
    bc, err = m.compile("\\call{img.peek}{1}{2}\\wait{go}{3}\\val{go.code}")
    assert err is None
    out = m.disassemble(bc)
    assert "CALL img.peek(1, 2)" in out
    assert "WAIT go 3000ms" in out
    assert "READSLOT" in out and "go.code" in out
