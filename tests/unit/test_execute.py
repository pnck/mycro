"""VM execution tests — CPython + MockHID, no hardware involved."""

import asyncio

import keymap
from macro import Macro, MAGIC, VERSION, OP_LOOP, OP_LOOP_END, OP_CHAR, OP_END
from mock_hid import MockHID


def run(text, hid=None, **kwargs):
    """Compile text and execute it, returning the MockHID with recorded events."""
    hid = hid or MockHID()
    m = Macro(hid)
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    kwargs.setdefault("default_delay_ms", 0)
    kwargs.setdefault("click_hold_ms", 0)
    asyncio.run(m.execute(bc, **kwargs))
    return hid


def test_type_char():
    hid = run("a")
    kc = keymap.letter_keycode("a")
    # press, release, + final release_all from the B2 finally
    assert hid.events[:2] == [("kp", kc), ("kr", kc)]


def test_type_shifted_char():
    hid = run("A")
    kc = keymap.letter_keycode("a")
    assert hid.events[:4] == [("kp", keymap.SHIFT), ("kp", kc), ("kr", kc), ("kr", keymap.SHIFT)]


def test_key_tap():
    hid = run("\\enter")
    assert hid.events[:2] == [("kp", keymap.ENTER), ("kr", keymap.ENTER)]


def test_combo():
    hid = run("\\ctrl+c")
    kc = keymap.letter_keycode("c")
    assert hid.events[:4] == [("kp", keymap.CONTROL), ("kp", kc), ("kr", kc), ("kr", keymap.CONTROL)]


def test_rep():
    hid = run("\\rep{3}{a}")
    kc = keymap.letter_keycode("a")
    presses = [e for e in hid.events if e == ("kp", kc)]
    assert len(presses) == 3


def test_kdown_kup_explicit():
    hid = run("\\kdown{ctrl}c\\kup{}")
    assert ("kp", keymap.CONTROL) in hid.events
    # kdown is released explicitly by kup{}, and the B2 finally still runs
    assert hid.events.count(("kra",)) >= 1


def test_kdown_preserved_across_subsequent_keys():
    """kdown state must persist across OP_CHAR, OP_KEY, OP_COMBO until explicit kup."""
    hid = run(r"\kdown{a}b\enter\ctrl+c\kup{a}")
    ka = keymap.letter_keycode("a")
    kb = keymap.letter_keycode("b")
    kc = keymap.letter_keycode("c")
    k_enter = keymap.ENTER
    k_ctrl = keymap.CONTROL

    assert ("kp", ka) in hid.events
    assert ("kp", kb) in hid.events
    assert ("kr", kb) in hid.events
    assert ("kp", k_enter) in hid.events
    assert ("kr", k_enter) in hid.events
    assert ("kp", k_ctrl) in hid.events
    assert ("kp", kc) in hid.events
    assert ("kr", kc) in hid.events
    assert ("kr", k_ctrl) in hid.events
    assert ("kr", ka) in hid.events

    # Verify 'a' is released ONLY at the end by kup{a}, not after 'b', enter, or ctrl+c
    idx_ka_up = hid.events.index(("kr", ka))
    idx_kb_up = hid.events.index(("kr", kb))
    idx_enter_up = hid.events.index(("kr", k_enter))
    idx_ctrl_up = [i for i, e in enumerate(hid.events) if e == ("kr", k_ctrl)][-1]
    assert idx_ka_up > idx_kb_up
    assert idx_ka_up > idx_enter_up
    assert idx_ka_up > idx_ctrl_up
    # Ensure no release_all ('kra') occurred
    assert ("kra",) not in hid.events


def test_kdown_remains_held_after_script_completion():
    """A standalone \\kdown{a} macro must leave 'a' pressed upon normal completion."""
    hid = MockHID()
    m = Macro(hid)
    bc, err = m.compile(r"\kdown{a}")
    assert err is None
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    ka = keymap.letter_keycode("a")
    assert hid.events == [("kp", ka)]  # strictly only key_press, no key_release or kra!

    # A subsequent macro can release it
    bc_up, err = m.compile(r"\kup{a}")
    assert err is None
    asyncio.run(m.execute(bc_up, default_delay_ms=0, click_hold_ms=0))
    assert hid.events == [("kp", ka), ("kr", ka)]


def test_kdown_kup_combo_execution():
    hid = MockHID()
    m = Macro(hid)
    bc, err = m.compile(r"\kdown{ctrl+c}")
    assert err is None
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    kc = keymap.letter_keycode("c")
    assert hid.events == [("kp", keymap.CONTROL), ("kp", kc)]

    bc_up, err = m.compile(r"\kup{ctrl+c}")
    assert err is None
    asyncio.run(m.execute(bc_up, default_delay_ms=0, click_hold_ms=0))
    assert hid.events == [("kp", keymap.CONTROL), ("kp", kc), ("kr", kc), ("kr", keymap.CONTROL)]


def test_combo_execution_press_hold_release():
    hid = MockHID()
    m = Macro(hid)
    bc, err = m.compile(r"\ctrl+c")
    assert err is None
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    kc = keymap.letter_keycode("c")
    assert hid.events == [
        ("kp", keymap.CONTROL),
        ("kp", kc),
        ("kr", kc),
        ("kr", keymap.CONTROL),
    ]


def test_mdown_remains_held_after_script_completion():
    """A standalone \\mdown{L} macro must leave left mouse button pressed upon normal completion."""
    hid = MockHID()
    m = Macro(hid)
    bc, err = m.compile(r"\mdown{L}")
    assert err is None
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    assert hid.events == [("mp", keymap.BTN_LEFT)]

    bc_up, err = m.compile(r"\mup{L}")
    assert err is None
    asyncio.run(m.execute(bc_up, default_delay_ms=0, click_hold_ms=0))
    assert hid.events == [("mp", keymap.BTN_LEFT), ("mr", keymap.BTN_LEFT)]


def test_mouse_click_and_move():
    hid = run("\\click{L}\\move{10,-5}")
    assert ("mp", keymap.BTN_LEFT) in hid.events
    assert ("mr", keymap.BTN_LEFT) in hid.events
    assert ("mm", 10, -5) in hid.events


# --- DSL v2: delay vs pace semantics ---

def test_pace_does_not_sleep_immediately():
    r"""\pace only changes inter-action delay; it must not block by itself."""
    import time

    start = time.monotonic()
    run("\\pace{0.2}a")
    elapsed = time.monotonic() - start
    # one 'a' with 200ms pace → ~0.2s total, not 0.4s (old dual semantics)
    assert 0.18 < elapsed < 0.35


def test_delay_is_one_shot():
    import time

    start = time.monotonic()
    run("\\delay{0.2}")
    elapsed = time.monotonic() - start
    assert 0.18 < elapsed < 0.35


# --- DSL v2: bytecode header ---

def test_execute_rejects_wrong_version():
    hid = MockHID()
    m = Macro(hid)
    try:
        asyncio.run(m.execute(b"\x00\x01\xff", default_delay_ms=0, click_hold_ms=0))
        raised = False
    except ValueError as e:
        raised = "version" in str(e)
    assert raised


# --- unified block offset: LOOP count=0 skips body via len ---

def test_loop_count_zero_skips_body():
    hid = MockHID()
    m = Macro(hid)
    body = bytes([OP_CHAR, ord("x")])
    bc = bytes([MAGIC, VERSION, 0, OP_LOOP, 0, 0, 0, 0, 0, len(body) + 1, 0]) + body + bytes([
        OP_LOOP_END, OP_CHAR, ord("y"), OP_END,
    ])
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    chars = [e for e in hid.events if e[0] == "kp"]
    # only 'y' typed; 'x' skipped
    assert chars == [("kp", keymap.letter_keycode("y")), ("kra",)] or \
        ("kp", keymap.letter_keycode("y")) in chars and \
        ("kp", keymap.letter_keycode("x")) not in chars


def test_combo_opcode_deprecated_nop():
    hid = MockHID()
    m = Macro(hid)
    # OP_COMBO with dummy mods=0x01, key=0x04 should be a 3-byte NOP in v4
    bc = bytes([MAGIC, VERSION, 0, 0x03, 0x01, 0x04, OP_CHAR, ord("a"), OP_END])
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    # OP_COMBO did not press ctrl, only OP_CHAR pressed and released 'a'
    assert ("kp", keymap.CONTROL) not in hid.events
    assert ("kp", keymap.letter_keycode("a")) in hid.events


# --- B2 cleanup guarantees ---

def test_b2_keys_released_on_runtime_error():
    """Unmappable char mid-macro must still release previously pressed keys."""
    hid = MockHID()
    hid.unmappable.add("~")  # force OP_CHAR failure after kdown
    m = Macro(hid)
    bc, err = m.compile("\\kdown{ctrl}~")
    assert err is None
    try:
        asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    assert ("kp", keymap.CONTROL) in hid.events
    # finally must have released everything despite the exception
    assert hid.events[-2] == ("kra",)
    assert hid.events[-1] == ("mr", keymap.BTN_ALL)


def test_b2_keys_released_on_cancel():
    """Cancellation (abort) during a long delay must release held keys."""
    hid = MockHID()
    m = Macro(hid)
    bc, err = m.compile("\\kdown{ctrl}\\delay{30}")
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
    assert ("kp", keymap.CONTROL) in hid.events
    assert ("kra",) in hid.events
    assert ("mr", keymap.BTN_ALL) in hid.events


def test_unknown_opcode_raises_and_cleans_up():
    hid = MockHID()
    m = Macro(hid)
    try:
        asyncio.run(m.execute(bytes([MAGIC, VERSION, 0, 0x77]), default_delay_ms=0, click_hold_ms=0))
        raised = False
    except RuntimeError as e:
        raised = "opcode" in str(e)
    assert raised
    assert ("kra",) in hid.events


def test_macro_btn_constants():
    assert Macro.BTN_ALL == keymap.BTN_ALL
    assert Macro.BTN_LEFT == keymap.BTN_LEFT
    assert Macro.BTN_RIGHT == keymap.BTN_RIGHT
    assert Macro.BTN_MIDDLE == keymap.BTN_MIDDLE
    assert Macro.BTN_BACK == keymap.BTN_BACK
    assert Macro.BTN_FORWARD == keymap.BTN_FORWARD


def test_execute_rejects_bad_header():
    import pytest
    hid = MockHID()
    m = Macro(hid)
    with pytest.raises(ValueError, match="Bytecode version mismatch"):
        asyncio.run(m.execute(bytes([0xA5, 0x03, 0, OP_END])))
    with pytest.raises(ValueError, match="Bytecode version mismatch"):
        asyncio.run(m.execute(bytes([0x99, VERSION, 0, OP_END])))
