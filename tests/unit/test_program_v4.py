"""Focused v4 program-container and compiler type-flow tests."""

from bytecode import MAX_CODE_BYTES, read_header
from macro import Macro
from mock_hid import MockHID
from runtime import Runtime


def compile_ok(source, runtime=None):
    macro = Macro(MockHID(), runtime)
    bytecode, error = macro.compile(source)
    assert error is None, error
    return bytecode


def compile_error(source, runtime=None):
    bytecode, error = Macro(MockHID(), runtime).compile(source)
    assert bytecode is None
    assert error
    return error


def opcode_segment(bytecode):
    _, code_start = read_header(bytecode)
    return bytecode[code_start:]


def test_opcode_segment_accepts_exact_max_code_bytes():
    # Each character occupies two opcode bytes; END supplies the final byte.
    bytecode = compile_ok("a" * 32767)
    assert len(opcode_segment(bytecode)) == MAX_CODE_BYTES == 65535


def test_opcode_segment_rejects_exactly_65536_bytes():
    # An empty loop contributes nine bytes, allowing an exact even boundary.
    source = "a" * 32763 + r"\rep{0}{}"
    error = compile_error(source)
    assert "Bytecode limit exceeded" in error


def test_string_metadata_is_independent_at_code_capacity():
    runtime = Runtime()
    runtime.register_ns(
        "net", {"send": (["str"], "int", lambda label: len(label))}
    )
    label = "x" * 255
    source = "\\use{net}" + "a" * 32764 + f"\\call{{net.send}}{{{label}}}"
    bytecode = compile_ok(source, runtime)
    strings, code_start = read_header(bytecode)
    assert len(bytecode) - code_start == MAX_CODE_BYTES
    assert len(bytecode) > MAX_CODE_BYTES
    assert strings == [label]
    assert "net" not in strings
    assert "send" not in strings


def test_pixelrect_makes_ret_a_snap_handle_for_next_call():
    runtime = Runtime()
    runtime.register_ns(
        "img", {"at": (["snap", "point"], "int", lambda snap, x, y: 0)}
    )
    source = r"\use{img}\pixelrect{p}{\row{0}}\call{img.at}{$ret}{0,0}"
    compile_ok(source, runtime)
