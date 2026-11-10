"""Deterministic regressions for normative VM v4 edge semantics."""

import asyncio

import pytest

import keymap
import vm as vm_module
from bytecode import (
    DEFAULT_PACE_MS,
    ERR_HANDLE_MISUSE,
    MAX_CODE_BYTES,
    OP_CALL_EXT,
    OP_CHAR,
    OP_END,
    OP_GUARD_ENTER,
    OP_GUARD_EXIT,
    OP_JMP,
    OP_KEY,
    OP_KEY_DOWN,
    OP_KEY_UP,
    OP_LOOP,
    OP_LOOP_END,
    OP_MCLICK,
    OP_MMOVE,
    OP_PACE,
    OP_SET,
    OP_SLEEP,
    OP_WAIT,
    REG_ERR,
    TAG_IMM,
    TAG_REG,
    VERSION,
    read_header,
)
from macro import Macro
from mock_hid import MockHID
from runtime import Runtime
from vm import VM


def imm(value):
    return bytes([TAG_IMM]) + (value & 0xFFFFFFFF).to_bytes(4, "little")


def reg(index):
    return bytes([TAG_REG, index])


def u16(value):
    return value.to_bytes(2, "little")


def container(code, strings=()):
    header = bytes([VERSION, len(strings)])
    for value in strings:
        encoded = value.encode("utf-8")
        header += bytes([len(encoded)]) + encoded
    return header + code


def program(code, strings=()):
    return container(code + bytes([OP_END]), strings)


def char(value):
    return bytes([OP_CHAR, ord(value)])


def key(value):
    return bytes([OP_KEY, value])


def set_reg(index, value):
    return bytes([OP_SET, index]) + imm(value)


def loop(count, body):
    framed_body = body + bytes([OP_LOOP_END])
    return bytes([OP_LOOP]) + count + u16(len(framed_body)) + framed_body


def guarded_slot(throttle_ms, body, abort):
    """Guard net.msg.guard == 1, with a normal jump over the abort block."""
    strings = ("net.msg", "guard", "target")
    normal_exit = bytes([OP_GUARD_EXIT, OP_JMP]) + u16(len(abort))
    abort_off = len(body) + len(normal_exit)
    descriptor = bytes([16, 0, 1]) + (1).to_bytes(4, "little")  # SLOT lhs == IMM rhs
    enter = (
        bytes([OP_GUARD_ENTER])
        + u16(abort_off)
        + bytes([1])  # one ALL triplet, explicit throttle
        + u16(throttle_ms)
        + descriptor
    )
    return program(enter + body + normal_exit + abort, strings)


def execute(bc, hid=None, runtime=None, use_macro=False, **kwargs):
    hid = hid or MockHID()
    runner = Macro(hid, runtime) if use_macro else VM(hid, runtime)
    kwargs.setdefault("click_hold_ms", 0)
    asyncio.run(runner.execute(bc, **kwargs))
    return hid, runner


def typed_chars(hid):
    chars = []
    for event in hid.events:
        if event[0] != "kp":
            continue
        for value in "abc":
            if event[1] == keymap.letter_keycode(value):
                chars.append(value)
                break
    return "".join(chars)


def fake_clock(monkeypatch):
    now = [0.0]
    sleeps = []
    monkeypatch.setattr(vm_module.time, "monotonic", lambda: now[0])

    async def fake_sleep(seconds):
        sleeps.append(round(seconds * 1000))
        now[0] += seconds

    monkeypatch.setattr(vm_module.asyncio, "sleep", fake_sleep)
    return now, sleeps


@pytest.mark.parametrize("use_macro", [False, True])
def test_default_pace_is_zero_for_vm_and_macro(monkeypatch, use_macro):
    _, sleeps = fake_clock(monkeypatch)
    assert DEFAULT_PACE_MS == 0
    execute(program(key(keymap.letter_keycode("a"))), use_macro=use_macro)
    assert sleeps == []


def test_tap_releases_before_automatic_pacing(monkeypatch):
    events = []
    hid = MockHID()
    keycode = keymap.letter_keycode("a")

    def press(value):
        events.append(("press", value))

    def release(value):
        events.append(("release", value))

    async def sleep(seconds):
        events.append(("sleep", round(seconds * 1000)))

    monkeypatch.setattr(hid, "key_press", press)
    monkeypatch.setattr(hid, "key_release", release)
    monkeypatch.setattr(vm_module.asyncio, "sleep", sleep)
    execute(program(key(keycode)), hid=hid, default_delay_ms=7, click_hold_ms=5)
    assert events == [
        ("press", keycode), ("sleep", 5),
        ("release", keycode), ("sleep", 7),
    ]


def test_pace_is_task_global_across_loop_scope(monkeypatch):
    _, sleeps = fake_clock(monkeypatch)
    code = (
        bytes([OP_PACE]) + imm(7)
        + loop(imm(2), key(keymap.letter_keycode("a")) + bytes([OP_PACE]) + imm(9))
        + key(keymap.letter_keycode("b"))
    )
    execute(program(code))
    assert sleeps == [7, 9, 9]


def test_single_iteration_nested_loop_has_its_own_frame():
    code = loop(imm(2), char("a") + loop(imm(1), char("b"))) + char("c")
    hid, _ = execute(program(code))
    assert typed_chars(hid) == "ababc"


def test_zero_count_loop_does_not_disturb_outer_frame():
    code = loop(imm(2), char("a") + loop(imm(0), char("b")) + char("c"))
    hid, _ = execute(program(code))
    assert typed_chars(hid) == "acac"


def test_handle_count_loop_fault_does_not_push_or_pop_a_frame():
    hid = MockHID()
    runtime = Runtime()
    runtime.register_ns(
        "img",
        {
            "snap": (
                ["rect"],
                "snap",
                lambda x, y, w, h: runtime.new_handle(
                    "snap", {"w": w, "h": h, "rgb": b"\x00" * (w * h * 3)}
                ),
            ),
        },
    )
    macro = Macro(hid, runtime)
    bc, err = macro.compile(
        r"\use{img}\call{img.snap}{0, 0, 1, 1}\set{h}{$ret}\rep{2}{\a\rep{$h}{\b}\c}"
    )
    assert err is None
    asyncio.run(macro.execute(bc, default_delay_ms=0, click_hold_ms=0))
    assert typed_chars(hid) == "acac"
    assert macro.vm.last_regs[REG_ERR] == ERR_HANDLE_MISUSE


def test_key_down_zero_is_hid_no_event():
    hid, _ = execute(program(bytes([OP_KEY_DOWN]) + imm(0)))
    assert hid.events == []
    dis = VM().disassemble(program(bytes([OP_KEY_DOWN]) + imm(0)))
    assert "KEY_DOWN 0x00" in dis
    dis_up = VM().disassemble(program(bytes([OP_KEY_UP]) + imm(0)))
    assert "KEY_UP ALL" in dis_up


def test_kdown_digit_zero_presses_the_digit_key():
    hid = MockHID()
    macro = Macro(hid)
    bc, err = macro.compile(r"\kdown{0}")
    assert err is None
    asyncio.run(macro.execute(bc, default_delay_ms=0, click_hold_ms=0))
    assert hid.events == [("kp", keymap.digit_keycode("0"))]


def test_kup_digit_zero_releases_the_digit_key():
    hid = MockHID()
    macro = Macro(hid)
    bc, err = macro.compile(r"\kup{0}")
    assert err is None
    asyncio.run(macro.execute(bc, default_delay_ms=0, click_hold_ms=0))
    assert hid.events == [("kr", keymap.digit_keycode("0"))]


@pytest.mark.parametrize("count", [0, -1])
def test_dynamic_click_count_clamps_to_one(count):
    code = set_reg(0, count) + bytes([OP_MCLICK]) + imm(keymap.BTN_LEFT) + reg(0)
    hid, _ = execute(program(code))
    assert hid.events == [("mp", keymap.BTN_LEFT), ("mr", keymap.BTN_LEFT)]


class CountingHID(MockHID):
    def __init__(self):
        super().__init__()
        self.clicks = 0

    def mouse_press(self, btn):
        self.clicks += 1

    def mouse_release(self, btn):
        pass


def test_dynamic_click_count_clamps_to_65535():
    hid = CountingHID()
    code = set_reg(0, 65536) + bytes([OP_MCLICK]) + imm(keymap.BTN_LEFT) + reg(0)
    execute(program(code), hid=hid)
    assert hid.clicks == 65535


def test_dynamic_mouse_move_clamps_to_full_i16_range():
    code = (
        set_reg(0, -32769)
        + set_reg(1, 32768)
        + bytes([OP_MMOVE])
        + reg(0)
        + reg(1)
    )
    hid, _ = execute(program(code))
    assert ("mm", -32768, 32767) in hid.events


def test_guard_sleep_checks_below_and_at_exact_throttle(monkeypatch):
    now, sleeps = fake_clock(monkeypatch)
    runtime = Runtime()
    runtime.register_signal("net.msg")
    runtime.fire_signal("net.msg", {"guard": 1, "target": 0})
    advances = iter((0.009, 0.010))

    async def early_sleep(seconds):
        sleeps.append(round(seconds * 1000))
        now[0] = next(advances)
        runtime.fire_signal("net.msg", {"guard": 0})

    monkeypatch.setattr(vm_module.asyncio, "sleep", early_sleep)
    body = bytes([OP_SLEEP]) + imm(10) + char("b") + bytes([OP_SLEEP]) + imm(10) + char("c")
    bc = guarded_slot(10, body, char("a"))
    hid, _ = execute(bc, runtime=runtime)
    assert typed_chars(hid) == "ba"
    assert sleeps == [10, 10]


def test_guard_wait_does_not_poll_one_ms_before_throttle(monkeypatch):
    now, _ = fake_clock(monkeypatch)
    runtime = Runtime()
    runtime.register_signal("net.msg")
    runtime.fire_signal("net.msg", {"guard": 1, "target": 0})
    runtime.consume_slot_pending("net.msg", "target")

    async def early_wait_for(awaitable, timeout):
        awaitable.close()
        now[0] = 0.009
        runtime.fire_signal("net.msg", {"guard": 0, "target": 1})
        raise asyncio.TimeoutError

    monkeypatch.setattr(vm_module.asyncio, "wait_for", early_wait_for)
    wait = bytes([OP_WAIT, 0, 2]) + imm(20)
    bc = guarded_slot(10, wait + char("b"), char("a"))
    hid, runner = execute(bc, runtime=runtime)
    assert typed_chars(hid) == "b"
    assert runner.last_regs[REG_ERR] == 0


@pytest.mark.parametrize(
    "code",
    [
        bytes([OP_KEY_DOWN]) + imm(keymap.letter_keycode("a")),
        bytes([OP_KEY_DOWN]) + imm(keymap.letter_keycode("a"))
        + bytes([OP_JMP]) + u16(10) + bytes([OP_END]),
    ],
)
def test_missing_end_or_out_of_bounds_jump_raises_and_cleans_up(code):
    hid = MockHID()
    with pytest.raises(RuntimeError, match="without terminal END"):
        execute(container(code), hid=hid)
    assert hid.events[-2:] == [("kra",), ("mr", keymap.BTN_ALL)]


def test_disassemble_rejects_missing_terminal_end():
    with pytest.raises(ValueError, match="missing terminal END"):
        VM().disassemble(container(char("a")))


@pytest.mark.parametrize(
    "bc",
    [
        program(bytes([OP_SLEEP, 3])),
        program(bytes([OP_CALL_EXT, 0, 0, 1, 3, 3]), ("", "", "", "")),
    ],
)
def test_disassemble_rejects_invalid_typed_operand_tags(bc):
    with pytest.raises(ValueError, match="Invalid operand tag"):
        VM().disassemble(bc)


@pytest.mark.parametrize(
    "bc",
    [
        bytes([VERSION, 1]),
        bytes([VERSION, 1, 3, ord("a")]),
        bytes([VERSION, 1, 1, 0xFF, OP_END]),
    ],
)
def test_read_header_rejects_truncated_or_invalid_utf8_strings(bc):
    with pytest.raises(ValueError):
        read_header(bc)


@pytest.mark.parametrize("operation", ["execute", "disassemble"])
def test_code_segment_over_65535_bytes_is_rejected(operation):
    bc = container(bytes([OP_END]) + bytes(MAX_CODE_BYTES))
    with pytest.raises(ValueError, match="code segment exceeds 65535"):
        if operation == "execute":
            execute(bc)
        else:
            VM().disassemble(bc)
