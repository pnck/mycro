"""Focused numeric-folding and built-in operand range tests."""

import pytest

from bytecode import (
    OP_MCLICK,
    OP_MMOVE,
    OP_PACE,
    OP_SET,
    OP_SLEEP,
    OP_WAIT,
    TAG_IMM,
    TAG_REG,
    read_header,
)
from macro import Macro
from mock_hid import MockHID


def compile_ok(source):
    bytecode, error = Macro(MockHID()).compile(source)
    assert error is None, error
    return bytecode


def compile_error(source):
    bytecode, error = Macro(MockHID()).compile(source)
    assert bytecode is None
    assert error
    return error


def code_start(bytecode):
    return read_header(bytecode)[1]


def decode_i32(bytecode, offset):
    return int.from_bytes(bytecode[offset:offset + 4], "little", signed=True)


def set_immediate(literal):
    bytecode = compile_ok(f"\\set{{x}}{{{literal}}}")
    start = code_start(bytecode)
    assert bytecode[start:start + 3] == bytes([OP_SET, 0, TAG_IMM])
    return decode_i32(bytecode, start + 3)


def duration_immediate(command, literal):
    opcode = OP_SLEEP if command == "delay" else OP_PACE
    bytecode = compile_ok(f"\\{command}{{{literal}}}")
    start = code_start(bytecode)
    assert bytecode[start:start + 2] == bytes([opcode, TAG_IMM])
    return decode_i32(bytecode, start + 2)


def move_immediates(dx, dy):
    bytecode = compile_ok(f"\\move{{{dx},{dy}}}")
    start = code_start(bytecode)
    assert bytecode[start] == OP_MMOVE
    assert bytecode[start + 1] == TAG_IMM
    assert bytecode[start + 6] == TAG_IMM
    return (
        decode_i32(bytecode, start + 2),
        decode_i32(bytecode, start + 7),
        len(bytecode) - start,
    )


@pytest.mark.parametrize(
    "literal,expected",
    [
        ("-2147483648", -2147483648),
        ("2147483647", 2147483647),
        ("-0x80000000", -2147483648),
        ("0x7fffffff", 2147483647),
        ("500ms", 500),
        ("1.0005s", 1001),
        ("-0.0004s", 0),
        ("-0.0005s", -1),
    ],
)
def test_numeric_literals_fold_globally_to_i32(literal, expected):
    assert set_immediate(literal) == expected


@pytest.mark.parametrize(
    "literal",
    ["-2147483649", "2147483648", "0x80000000", "0xffffffff"],
)
def test_numeric_literals_outside_i32_are_rejected(literal):
    assert "integer out of i32 range" in compile_error(f"\\set{{x}}{{{literal}}}")


def test_fraction_without_seconds_suffix_is_rejected():
    error = compile_error(r"\set{x}{0.5}")
    assert "fractional number requires unit 's'" in error


@pytest.mark.parametrize("command", ["delay", "pace"])
@pytest.mark.parametrize(
    "literal,expected",
    [
        ("0", 0),
        ("0ms", 0),
        ("0.0004s", 0),
        ("0.0005s", 1),
        ("0.0006s", 1),
        ("1.2345s", 1235),
        ("65.535s", 65535),
        ("65535", 65535),
    ],
)
def test_static_durations_use_exact_half_up_and_keep_zero(command, literal, expected):
    assert duration_immediate(command, literal) == expected


@pytest.mark.parametrize("command", ["delay", "pace"])
@pytest.mark.parametrize("literal", ["-1", "-1ms", "-0.0004s", "-0x1"])
def test_negative_duration_literals_are_explicitly_rejected(command, literal):
    error = compile_error(f"\\{command}{{{literal}}}")
    assert "negative duration not allowed" in error


@pytest.mark.parametrize("command", ["delay", "pace"])
@pytest.mark.parametrize("literal", ["65536", "65.5355s", "70s"])
def test_static_durations_outside_u16_are_rejected(command, literal):
    error = compile_error(f"\\{command}{{{literal}}}")
    assert "duration out of range [0, 65535]" in error


@pytest.mark.parametrize("command", ["delay", "pace"])
def test_dynamic_durations_remain_register_operands(command):
    bytecode = compile_ok(f"\\set{{t}}{{70000}}\\{command}{{$t}}")
    start = code_start(bytecode)
    opcode = OP_SLEEP if command == "delay" else OP_PACE
    assert bytecode[start + 7:] == bytes([opcode, TAG_REG, 0, 0xFF])


def test_wait_timeout_uses_the_same_exact_signed_folding():
    bytecode = compile_ok(r"\wait[-0.0005s]{$slot.test.sig.value}")
    start = code_start(bytecode)
    assert bytecode[start] == OP_WAIT
    assert bytecode[start + 3] == TAG_IMM
    assert decode_i32(bytecode, start + 4) == -1


@pytest.mark.parametrize("count", [1, 65535])
def test_click_static_count_accepts_closed_u16_interval(count):
    bytecode = compile_ok(f"\\click{{L,{count}}}")
    start = code_start(bytecode)
    assert bytecode[start] == OP_MCLICK
    assert bytecode[start + 6] == TAG_IMM
    assert decode_i32(bytecode, start + 7) == count


@pytest.mark.parametrize("count", [0, -1, 65536])
def test_click_static_count_is_rejected_instead_of_clamped(count):
    error = compile_error(f"\\click{{L,{count}}}")
    assert "click count out of range [1, 65535]" in error


def test_click_rejects_extra_arguments():
    source = r"\click{L,1,2}"
    assert "click requires {btn} or {btn,count}" in compile_error(source)


def test_click_dynamic_count_remains_a_register_operand():
    bytecode = compile_ok(r"\set{n}{0}\click{L,$n}")
    start = code_start(bytecode)
    assert bytecode[start + 7] == OP_MCLICK
    assert bytecode[start + 13:start + 15] == bytes([TAG_REG, 0])


@pytest.mark.parametrize("dx,dy", [(-32768, 32767), (32767, -32768), (0, 0)])
def test_move_emits_one_instruction_for_i16_immediates(dx, dy):
    actual_dx, actual_dy, code_length = move_immediates(dx, dy)
    assert (actual_dx, actual_dy) == (dx, dy)
    assert code_length == 12  # MMOVE + two immediate operands + END


@pytest.mark.parametrize(
    "dx,dy",
    [(-32769, 0), (32768, 0), (0, -32769), (0, 32768)],
)
def test_move_rejects_out_of_range_immediates_without_splitting(dx, dy):
    assert "move component out of i16 range" in compile_error(f"\\move{{{dx},{dy}}}")


@pytest.mark.parametrize("source", [r"\move{1}", r"\move{1,2,3}", r"\move{}"])
def test_move_requires_exactly_two_arguments(source):
    assert "move requires {dx,dy}" in compile_error(source)


def test_move_dynamic_components_remain_register_operands():
    bytecode = compile_ok(r"\set{x}{32768}\set{y}{-32769}\move{$x,$y}")
    start = code_start(bytecode)
    assert bytecode[start + 14:] == bytes([OP_MMOVE, TAG_REG, 0, TAG_REG, 1, 0xFF])


@pytest.mark.parametrize("source", [
    r"\delay{0x10}",
    r"\pace{0XFFFF}",
    r"\wait[0x10]{$slot.test.sig.val}",
    r"\guarded[0x10]{{$ret, =, 0}}{}",
])
def test_duration_grammars_reject_hexadecimal_literals(source):
    assert compile_error(source)
