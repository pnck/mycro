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
    # press, release_all, + final release_all from the B2 finally
    assert hid.events[:2] == [("kp", kc), ("kra",)]


def test_type_shifted_char():
    hid = run("A")
    kc = keymap.letter_keycode("a")
    assert hid.events[:3] == [("kp", keymap.SHIFT), ("kp", kc), ("kra",)]


def test_key_tap():
    hid = run("\\enter")
    assert hid.events[:2] == [("kp", keymap.ENTER), ("kra",)]


def test_combo():
    hid = run("\\ctrl+c")
    kc = keymap.letter_keycode("c")
    assert hid.events[:3] == [("kp", keymap.CONTROL), ("kp", kc), ("kra",)]


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
    bc = bytes([MAGIC, VERSION, OP_LOOP, 0, len(body) + 1, 0]) + body + bytes([
        OP_LOOP_END, OP_CHAR, ord("y"), OP_END,
    ])
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    chars = [e for e in hid.events if e[0] == "kp"]
    # only 'y' typed; 'x' skipped
    assert chars == [("kp", keymap.letter_keycode("y")), ("kra",)] or \
        ("kp", keymap.letter_keycode("y")) in chars and \
        ("kp", keymap.letter_keycode("x")) not in chars


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
        asyncio.run(m.execute(bytes([MAGIC, VERSION, 0x77]), default_delay_ms=0, click_hold_ms=0))
        raised = False
    except RuntimeError as e:
        raised = "opcode" in str(e)
    assert raised
    assert ("kra",) in hid.events
