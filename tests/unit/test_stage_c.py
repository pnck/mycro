r"""Stage C: \use / \call / \wait / signal slots / string table / runtime registry."""

import asyncio

import keymap
from macro import (
    Macro,
    MAGIC, VERSION, TAG_IMM, TAG_REG, TAG_NAME, REG_RET, REG_TIMEOUT,
    OP_SET, OP_ADD, OP_SLEEP, OP_KEY_DOWN, OP_READSLOT, OP_WAIT, OP_CALL_EXT,
    OP_TYPEREG, OP_END,
)
from runtime import Runtime
from mock_hid import MockHID


def make_runtime():
    rt = Runtime()
    rt.register_ns("sys", {"free": ([], "int", lambda: 12345)})
    rt.register_ns("img", {
        "load": (["str"], "tpl", lambda name: 7),
        "match": (["tpl"], "int", lambda h: 1 if h == 7 else 0),
        "peek": (["int", "int"], "int", lambda x, y: x + y),
        "at": (["tpl", "point"], "int", lambda h, x, y: h + x + y),
        "crop": (["tpl", "rect"], "int", lambda h, x, y, w, hh: h),
    })
    rt.register_signal("go")
    rt.register_signal("net.msg")
    return rt


def _auto_use(text, rt=None):
    rt_obj = rt or make_runtime()
    uses = ""
    for ns in set(list(rt_obj._ns.keys()) + ["go", "net"]):
        if f"\\call{{{ns}." in text or f"$slot.{ns}." in text or f"${ns}." in text:
            if f"\\use{{{ns}}}" not in text:
                uses += f"\\use{{{ns}}}"
    return uses + text


def compile_ok(text, rt=None):
    rt_obj = rt or make_runtime()
    bc, err = Macro(MockHID(), rt_obj).compile(_auto_use(text, rt_obj))
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text, rt=None):
    bc, err = Macro(MockHID(), rt or make_runtime()).compile(text)
    assert bc is None and err, f"expected compile error, got success: {text}"
    return err


def header_strs(bc):
    """Parse the header: returns (strings, code_start)."""
    assert bc[0] == VERSION
    n = bc[1]
    i = 2
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
    assert strs == []
    assert "sys" not in strs
    assert "free" not in strs
    assert bc[code:] == bytes([OP_CALL_EXT, 0, 0, 0, OP_END])


def test_string_dedup():
    bc = compile_ok("\\call{img.load}{a}\\call{img.load}{a}")
    strs, _ = header_strs(bc)
    # ns and ext fn names are erased; string arg 'a' interned once
    assert strs == ["a"]
    assert "img" not in strs
    assert "load" not in strs


def test_str_arg():
    bc = compile_ok("\\call{img.load}{logo}")
    strs, code = header_strs(bc)
    rt = make_runtime()
    fn_id = rt.get_fn_id("img", "load")
    assert bc[code:] == bytes([
        OP_CALL_EXT, 0, fn_id, 1,
        TAG_NAME, strs.index("logo"), OP_END,
    ])
    assert "img" not in strs
    assert "load" not in strs


def test_handle_arg_must_be_var():
    # form check: a literal can never be a handle
    assert "must be a variable" in compile_err("\\use{img}\\call{img.match}{7}")


def test_handle_type_environment():
    # traceable flow: load returns tpl -> \set copies the type -> match ok
    compile_ok("\\call{img.load}{name}\\set{h}{$ret}\\call{img.match}{$h}")
    # $ret used directly is traceable as well
    compile_ok("\\call{img.load}{name}\\call{img.match}{$ret}")
    # plain int variable is not a tpl handle
    assert "must hold a tpl handle" in compile_err("\\use{img}\\set{h}{0}\\call{img.match}{$h}")
    # stale $ret: an intervening int-returning call retypes $ret
    assert "must hold a tpl handle" in compile_err(
        "\\use{img}\\use{sys}\\call{img.load}{name}\\call{sys.free}\\call{img.match}{$ret}")


def test_add_rejects_handle():
    # In v4 (§6.2), handle in arithmetic compiles ok; at runtime leaves dst unchanged and sets $err=ERR_HANDLE_MISUSE
    bc = compile_ok("\\call{img.load}{name}\\set{h}{$ret}\\add{h}{1}")
    assert bc is not None


def test_if_rejects_handle():
    # In v4 (§6.7, §8.1), handle in predicate compiles ok; at runtime evaluates false and sets $err=ERR_HANDLE_MISUSE
    bc1 = compile_ok("\\call{img.load}{name}\\set{h}{$ret}\\if{ {$h, =, 1} }{a}")
    bc2 = compile_ok("\\call{img.load}{name}\\set{h}{$ret}\\set{x}{1}\\if{ {$x, =, $h} }{a}")
    assert bc1 is not None and bc2 is not None


def test_if_ret_exemption():
    # $ret carries error sentinels alongside results — checking it is the
    # mandatory failure path, exempt from the handle comparison ban
    compile_ok("\\call{img.load}{name}\\if{ {$ret, <, 0} }{a}")
    compile_ok("\\call{img.load}{name}\\set{x}{1}\\if{ {$x, =, $ret} }{a}")


def test_str_arg_unescape_and_strip():
    # brace-matching escapes become literal chars; whitespace stripped
    bc = compile_ok("\\call{img.load}{ a\\}b }")
    strs, _ = header_strs(bc)
    assert "a}b" in strs
    assert "no string variables" in compile_err("\\use{img}\\call{img.load}{$x}")


def test_point_rect_args_expand():
    bc = compile_ok("\\call{img.load}{n}\\set{h}{$ret}\\call{img.at}{$h}{3,4}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    at = tail.rindex(OP_CALL_EXT)  # the img.at call (img.load is first)
    rt = make_runtime()
    assert tail[at + 1] == 0 and tail[at + 2] == rt.get_fn_id("img", "at")
    assert "at" not in strs
    assert "img" not in strs
    assert tail[at + 3] == 3  # handle + point expanded to 2 operands
    bc = compile_ok("\\call{img.load}{n}\\set{h}{$ret}\\call{img.crop}{$h}{1,2,3,4}")
    tail = bc[header_strs(bc)[1]:]
    assert tail[tail.rindex(OP_CALL_EXT) + 3] == 5  # handle + 4 rect operands


def test_point_rect_arg_errors():
    assert "2 comma-separated" in compile_err(
        "\\use{img}\\call{img.load}{n}\\set{h}{$ret}\\call{img.at}{$h}{3}")
    assert "4 comma-separated" in compile_err(
        "\\use{img}\\call{img.load}{n}\\set{h}{$ret}\\call{img.crop}{$h}{1,2,3}")


def test_point_rect_elements_support_vars_and_slots():
    # rect elements are value positions: $var and slot refs compile
    compile_ok("\\set{x}{1}\\call{img.load}{n}\\set{h}{$ret}"
               "\\call{img.crop}{$h}{$x,2,3,4}")
    compile_ok("\\call{img.load}{n}\\set{h}{$ret}"
               "\\call{img.crop}{$h}{$net.msg.x,2,3,4}")


def test_int_args():
    bc = compile_ok("\\set{x}{3}\\call{img.peek}{1}{$x}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    at = tail.index(OP_CALL_EXT)
    rt = make_runtime()
    assert tail[at + 1] == 0
    assert tail[at + 2] == rt.get_fn_id("img", "peek")
    assert "peek" not in strs
    assert "img" not in strs
    assert tail[at + 3] == 2  # argc
    assert tail[at + 4] == TAG_IMM and tail[at + 5 : at + 9] == bytes([1, 0, 0, 0])
    assert tail[at + 9] == TAG_REG and tail[at + 10] == 0


# --- \use / signature validation ---

def test_use_ok():
    compile_ok("\\use{sys}\\call{sys.free}")


def test_use_unknown_ns():
    assert "Unknown namespace" in compile_err("\\use{nope}")


def test_call_unknown_fn():
    assert "Unknown ext function" in compile_err("\\use{sys}\\call{sys.nope}")


def test_call_bad_target():
    assert "ns.fn" in compile_err("\\call{sysfree}")


def test_call_argc_mismatch():
    assert "expects 1 arg" in compile_err("\\use{img}\\call{img.load}")
    assert "expects 1 arg" in compile_err("\\use{img}\\call{img.load}{a}{b}")


def test_call_zero_arg_rejects_extra():
    assert "expects 0 arg" in compile_err("\\use{sys}\\call{sys.free}{x}")


def test_str_arg_rejects_var():
    assert "no string variables" in compile_err("\\use{img}\\set{x}{1}\\call{img.load}{$x}")


def test_call_rejects_undeclared_ns():
    assert "undeclared namespace: sys" in compile_err("\\call{sys.free}")
    assert "undeclared namespace: img" in compile_err("\\call{img.load}{logo}")
    assert "Unknown namespace: bogus" in compile_err("\\call{bogus.fn}")


def test_use_top_level_only():
    assert "use only allowed at top level" in compile_err("\\rep{1}{\\use{sys}}")
    assert "use only allowed at top level" in compile_err("\\if{ {1, =, 1} }{\\use{sys}}")
    assert "use only allowed at top level" in compile_err("\\guarded{ {1, =, 1} }{\\use{sys}}")
    assert "use only allowed at top level" in compile_err("\\def{mm}{\\use{sys}}\\mm")


def test_use_idempotent_and_order_sensitive():
    compile_ok("\\use{sys}\\use{sys}\\call{sys.free}")
    assert "undeclared namespace: sys" in compile_err("\\call{sys.free}\\use{sys}")


def test_pixelrect_lowering_independent_of_use_sys():
    # spec/dsl.md §6.4: Resource lowering activates sys.const independently of source \use
    compile_ok("\\pixelrect{p}{\\row{0x123456}}")


def test_call_requires_runtime():
    bc, err = Macro(MockHID()).compile("\\call{sys.free}")  # no runtime
    assert err and "runtime registry" in err


# --- \wait emission ---

def test_wait_forever():
    bc = compile_ok("\\wait{$slot.go.val}")
    strs, code = header_strs(bc)
    sig_idx = strs.index("go")
    field_idx = strs.index("val")
    # omitted timeout lowers to DEFAULT_WAIT_TIMEOUT_MS = 2147483647 (0x7FFFFFFF)
    assert bc[code:] == bytes([OP_WAIT, sig_idx, field_idx, TAG_IMM, 0xFF, 0xFF, 0xFF, 0x7F, OP_END])


def test_wait_timeout_imm():
    bc = compile_ok("\\wait[2s]{$slot.go.val}")
    strs, code = header_strs(bc)
    assert bc[code + 3 :] == bytes([TAG_IMM, 2000 & 0xFF, 2000 >> 8, 0, 0, OP_END])


def test_wait_timeout_var():
    bc = compile_ok("\\set{t}{500}\\wait[$t]{$slot.go.val}")
    strs, code = header_strs(bc)
    tail = bc[code:]
    at = tail.index(OP_WAIT)
    assert tail[at + 1] == strs.index("go")
    assert tail[at + 2] == strs.index("val")
    assert tail[at + 3] == TAG_REG and tail[at + 4] == 0


def test_wait_unknown_signal():
    assert "wait target must be a slot reference" in compile_err("\\wait{nope}")
    assert "unknown signal namespace" in compile_err("\\wait{$slot.nope.val}").lower()


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
    assert "unknown signal namespace" in compile_err("\\set{x}{0}\\add{x}{$nope.f}").lower()


def test_typenum_slot():
    bc = compile_ok("\\typenum{$go.code}")
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


def test_slot_kdown_rejected():
    err = compile_err("\\kdown{$go.k}")
    assert "kdown/kup takes a key name, not a variable" in err


def test_slot_move_two_slots():
    rt = make_runtime()
    rt.fire_signal("go", {"x": 10, "y": 20})
    hid = run("\\move{$go.x,$go.y}", rt=rt)
    assert ("mm", 10, 20) in hid.events


def test_slot_if_both_operands():
    rt = make_runtime()
    rt.fire_signal("go", {"a": 5, "b": 6})
    hid = run("\\if{ {$go.a, <, $go.b} }{\\call{sys.free}\\typenum{$ret}}", rt=rt)
    for d in "12345":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_slot_call_two_slot_args():
    captured = {}
    rt = Runtime()
    rt.register_ns("pair", {"sum2": (["int", "int"], "int",
                                     lambda a, b: captured.__setitem__("v", (a, b)) or a + b)})
    rt.register_signal("go")
    rt.fire_signal("go", {"x": 3, "y": 4})
    hid = run("\\call{pair.sum2}{$go.x}{$go.y}\\typenum{$ret}", rt=rt)
    assert captured["v"] == (3, 4)
    for d in "7":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_add_slot_target_rejected():
    assert "signal slot" in compile_err("\\set{x}{0}\\add{$go.f}{1}")


def test_wait_consumes_pending_latch():
    # In v4, receptor slot has arrival latch pending=1 upon projection.
    # A prior projection is consumed immediately by \wait ($timeout = 0).
    rt = make_runtime()
    rt.fire_signal("go", {"n": 1})
    hid = run("\\wait[50ms]{$slot.go.n}\\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("0")) in hid.events
    # Subsequent wait on consumed latch will time out if no new fire arrives
    hid2 = run("\\wait[10ms]{$slot.go.n}\\typenum{$timeout}", rt=rt)
    assert ("kp", keymap.digit_keycode("1")) in hid2.events


# --- runtime: ext calls ---

def run(text, rt=None, hid=None):
    hid = hid or MockHID()
    rt = rt or make_runtime()
    m = Macro(hid, rt)
    bc, err = m.compile(_auto_use(text, rt))
    assert err is None, f"compile error: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    return hid


def test_run_call_ret_rendered():
    hid = run("\\call{sys.free}\\typenum{$ret}")
    # 12345 typed out digit by digit
    for d in "12345":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_slot_ref_dotted_signal_name():
    # Signal names may contain dots (net.msg): $sig.field splits at the
    # LAST dot, so $net.msg.x reads field x of signal net.msg
    rt = make_runtime()
    rt.fire_signal("net.msg", {"x": 120})
    hid = run("\\set{r}{$net.msg.x}\\typenum{$r}", rt=rt)
    for d in "120":
        assert ("kp", keymap.digit_keycode(d)) in hid.events


def test_slot_ref_dotted_signal_unknown_is_error():
    assert "unknown signal namespace" in compile_err("\\set{r}{$net.nope.x}").lower()


def test_run_call_str_arg_and_handle_flow():
    captured = {}

    def load(name):
        captured["name"] = name
        return rt.new_handle("tpl", {"name": name})

    rt = make_runtime()
    rt.register_ns("img", {"load": (["str"], "tpl", load), "match": (["tpl"], "int", lambda h: 1)})
    hid = run("\\call{img.load}{logo}\\call{img.match}{$ret}\\typenum{$ret}", rt=rt)
    assert captured["name"] == "logo"
    # match(7) -> 1 rendered
    assert ("kp", keymap.digit_keycode("1")) in hid.events


def test_run_call_async_fn_awaited():
    async def slow():
        await asyncio.sleep(0.01)
        return 42

    rt = make_runtime()
    rt.register_ns("img", {"load": (["str"], "tpl", lambda n: 0), "match": (["tpl"], "int", lambda h: 0),
                           "peek": (["int", "int"], lambda a, b: a + b)})
    rt.register_ns("test", {"slow": ([], "int", slow)})
    hid = run("\\call{test.slow}\\typenum{$ret}", rt=rt)
    assert ("kp", keymap.digit_keycode("4")) in hid.events
    assert ("kp", keymap.digit_keycode("2")) in hid.events


def test_run_call_int_args_compute():
    hid = run("\\call{img.peek}{20}{22}\\typenum{$ret}")
    assert ("kp", keymap.digit_keycode("4")) in hid.events
    assert ("kp", keymap.digit_keycode("2")) in hid.events


# --- runtime: wait + slots ---

def test_run_wait_fired():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile("\\use{go}\\wait{$slot.go.val}\\typenum{$timeout}")
    assert err is None

    async def scenario():
        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        await asyncio.sleep(0.01)
        rt.fire_signal("go")
        await task

    asyncio.run(scenario())
    assert ("kp", keymap.digit_keycode("0")) in hid.events  # timeout == 0


def test_run_wait_timeout():
    hid = run("\\wait[50ms]{$slot.go.val}\\typenum{$timeout}")
    assert ("kp", keymap.digit_keycode("1")) in hid.events  # timed out


def test_run_wait_then_slot():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile("\\use{go}\\wait{$slot.go.code}\\typenum{$go.code}")
    assert err is None

    async def scenario():
        task = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        await asyncio.sleep(0.01)
        rt.fire_signal("go", {"code": 5})
        await task

    asyncio.run(scenario())
    assert ("kp", keymap.digit_keycode("5")) in hid.events


def test_run_slot_missing_field_is_zero():
    hid = run("\\typenum{$go.code}")
    assert ("kp", keymap.digit_keycode("0")) in hid.events


def test_run_abort_during_wait():
    rt = make_runtime()
    hid = MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile("\\use{go}\\kdown{ctrl}\\wait{$slot.go.val}")
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
    # ext args are separate brace groups, wait uses v4 syntax
    bc, err = m.compile("\\use{img}\\use{go}\\call{img.peek}{1}{2}\\wait[3s]{$slot.go.code}\\typenum{$go.code}")
    assert err is None
    out = m.disassemble(bc)
    assert "CALL img.peek(1, 2)" in out
    assert "WAIT $slot.go.code 3000ms" in out
    assert "READSLOT" in out and "go.code" in out


def test_def_forwards_compound_arg():
    # a \def parameter carries compound text into a rect-typed position
    compile_ok("\\call{img.load}{n}\\set{h}{$ret}"
               "\\def{fw}[1]{\\call{img.crop}{$h}{#1}}"
               "\\fw{1,2,3,4}")
    # landing-position validity is checked after substitution
    assert "4 comma-separated" in compile_err(
        "\\use{img}\\call{img.load}{n}\\set{h}{$ret}"
        "\\def{fw}[1]{\\call{img.crop}{$h}{#1}}\\fw{1,2}")
