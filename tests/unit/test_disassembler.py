"""Unit tests for VM.disassemble() v4 upgrade.

Verifies full coverage of all bytecode opcodes, typed operand formatting,
guard descriptors (ALL/ANY with IMM/REG/SLOT predicates and throttles),
and namespace/function symbol reflection.
"""

import pytest
from bytecode import (
    VERSION,
    OP_CHAR, OP_KEY, OP_COMBO, OP_SLEEP, OP_MCLICK, OP_MMOVE, OP_PACE,
    OP_CONSUMER_KEY, OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP,
    OP_MDOWN, OP_MUP, OP_SET, OP_ADD, OP_BRA, OP_JMP, OP_SUB,
    OP_GUARD_ENTER, OP_GUARD_EXIT, OP_READSLOT, OP_WAIT, OP_CALL_EXT,
    OP_TYPEREG, OP_END,
    TAG_IMM, TAG_REG, TAG_NAME,
    REG_ERR, REG_TIMEOUT, REG_RET,
    GUARD_TAG_IMM, GUARD_TAG_REG, GUARD_TAG_SLOT,
)
from keymap import ENTER, BTN_LEFT, BTN_RIGHT, BTN_ALL
from macro import Macro
from mock_hid import MockHID
from runtime import Runtime
from vm import VM


def make_program(body: bytes, strings=()) -> bytes:
    """Build a valid bytecode container with header and string table."""
    hdr = bytearray([VERSION, len(strings)])
    for s in strings:
        raw = s.encode("utf-8")
        hdr.append(len(raw))
        hdr.extend(raw)
    hdr.extend(body)
    return bytes(hdr)


def make_test_runtime():
    rt = Runtime()
    rt.register_ns("sys", {
        "free": ([], "int", lambda: 12345),
    })
    rt.register_ns("net", {
        "send": (["str"], "int", lambda s: 0),
    })
    rt.register_ns("img", {
        "snap": (["rect"], "snap", lambda x, y, w, h: 1),
        "at": (["snap", "point"], "int", lambda s, x, y: 0),
        "diff": (["snap", "snap", "int"], "int", lambda a, b, tol: 0),
        "watch": (["str", "point", "span"], "int", lambda n, x, y, i, d: 0),
        "unwatch": (["str"], "int", lambda n: 0),
    })
    rt.register_signal("net.msg")
    rt.register_signal("go.status")
    return rt


def test_disassemble_header_and_basic_keys():
    m = Macro(MockHID())
    bc, err = m.compile("ab\\enter")
    assert err is None
    dis = m.disassemble(bc)
    assert f"; bytecode v{VERSION}" in dis
    assert "CHAR 'a'" in dis
    assert "CHAR 'b'" in dis
    assert "KEY_TAP ENTER" in dis
    assert "END" in dis


def test_disassemble_sleep_pace_consumer():
    m = Macro(MockHID())
    bc, err = m.compile("\\delay{250ms}\\pace{50ms}\\volup\\voldn\\mute")
    assert err is None
    dis = m.disassemble(bc)
    assert "SLEEP 250ms" in dis
    assert "PACE 50ms" in dis
    assert "CONSUMER VOLUME_UP" in dis
    assert "CONSUMER VOLUME_DOWN" in dis
    assert "CONSUMER MUTE" in dis


def test_disassemble_mouse_operations():
    m = Macro(MockHID())
    bc, err = m.compile("\\click{left}\\click{right,2}\\mdown{left}\\mup{left}\\move{10,-20}")
    assert err is None
    dis = m.disassemble(bc)
    assert "MCLICK LEFT x1" in dis
    assert "MCLICK RIGHT x2" in dis
    assert "MDOWN LEFT" in dis
    assert "MUP LEFT" in dis
    assert "MMOVE (10,-20)" in dis


def test_disassemble_loops_and_nesting():
    m = Macro(MockHID())
    bc, err = m.compile("\\rep{3}{\\rep{2}{x}}")
    assert err is None
    dis = m.disassemble(bc)
    assert "LOOP x3" in dis
    assert "  LOOP x2" in dis
    assert "    CHAR 'x'" in dis
    assert "  }LOOP_END" in dis
    assert "}LOOP_END" in dis


def test_disassemble_arithmetic_and_special_registers():
    m = Macro(MockHID())
    bc, err = m.compile(
        "\\set{x}{100}\\add{x}{50}\\sub{x}{20}"
        "\\set{y}{$err}\\set{z}{$timeout}\\set{w}{$ret}"
        "\\typenum{$x}\\typenum{$ret}"
    )
    assert err is None
    dis = m.disassemble(bc)
    assert "SET $r0, 100" in dis
    assert "ADD $r0, 50" in dis
    assert "SUB $r0, 20" in dis
    assert "SET $r1, $err" in dis
    assert "SET $r2, $timeout" in dis
    assert "SET $r3, $ret" in dis
    assert "TYPEREG $r0" in dis
    assert "TYPEREG $ret" in dis


def test_disassemble_branches_and_jumps():
    m = Macro(MockHID())
    bc, err = m.compile("\\set{x}{5}\\if{ {$x, =, 5} }{a}{b}")
    assert err is None
    dis = m.disassemble(bc)
    assert "BRA $r0 = 5" in dis
    assert "CHAR 'a'" in dis
    assert "JMP" in dis
    assert "CHAR 'b'" in dis


def test_disassemble_guard_enter_and_exit_formatting():
    rt = make_test_runtime()
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile(
        "\\use{net}"
        "\\set{x}{10}"
        "\\guarded[40ms]{ \\any{ {$x, <, 20}, {$slot.net.msg.val, >=, 100} } }{ \\delay{10ms} }"
    )
    assert err is None
    dis = m.disassemble(bc)
    assert "GUARD_ENTER" in dis
    assert "[40ms]" in dis
    assert "ANY(2)" in dis
    assert "{$r0, <, 20}" in dis
    assert "{$slot.net.msg.val, >=, 100}" in dis
    assert "GUARD_EXIT" in dis


def test_disassemble_guard_enter_immediate_lhs_and_all():
    m = Macro(MockHID())
    bc, err = m.compile("\\set{x}{1}\\guarded{ {5, !=, $x} }{ a }")
    assert err is None
    dis = m.disassemble(bc)
    assert "ALL(1)" in dis
    assert "{5, !=, $r0}" in dis


def test_disassemble_readslot_and_wait():
    rt = make_test_runtime()
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile(
        "\\use{go}\\wait[5s]{$slot.go.status.ready}\\set{x}{$go.status.code}"
    )
    assert err is None
    dis = m.disassemble(bc)
    assert "WAIT $slot.go.status.ready 5000ms" in dis
    assert "READSLOT" in dis
    assert "go.status.code" in dis


def test_disassemble_call_ext_symbol_reflection():
    rt = make_test_runtime()
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile(
        "\\use{sys}\\use{net}\\use{img}"
        "\\call{sys.free}"
        "\\call{net.send}{hello}"
        "\\call{img.watch}{pos}{10,20}{100,5000}"
        "\\call{img.unwatch}{pos}"
    )
    assert err is None
    dis = m.disassemble(bc)
    assert "CALL sys.free()" in dis
    assert "CALL net.send('hello')" in dis
    assert "CALL img.watch('pos', 10, 20, 100, 5000)" in dis
    assert "CALL img.unwatch('pos')" in dis


def test_disassemble_call_ext_special_register_args():
    rt = make_test_runtime()
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile(
        "\\use{img}"
        "\\call{img.snap}{0,0,10,10}"
        "\\set{s}{$ret}"
        "\\call{img.diff}{$s}{$ret}{5}"
    )
    assert err is None
    dis = m.disassemble(bc)
    assert "CALL img.diff($r0, $ret, 5)" in dis


def test_disassemble_fallback_without_compiler_activation():
    """Verify that a standalone VM given activated_namespaces
    reflects registered symbols."""
    rt = make_test_runtime()
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile("\\use{img}\\call{img.snap}{0,0,10,10}")
    assert err is None

    # Disassemble using a fresh VM instance specifying activated_namespaces
    fresh_vm = VM(runtime=rt)
    dis = fresh_vm.disassemble(bc, activated_namespaces=["img"])
    assert "CALL img.snap(0, 0, 10, 10)" in dis


def test_disassemble_unresolved_ext_fallback():
    """When an ext call indexes an unknown namespace, fall back to ext[ns_id][fn_id]."""
    # Construct an OP_CALL_EXT with out-of-range ns_id 5, fn_id 2
    body = bytes([
        OP_CALL_EXT, 5, 2, 1, TAG_IMM, 1, 0, 0, 0,
        OP_END
    ])
    bc = make_program(body)
    fresh_vm = VM()
    dis = fresh_vm.disassemble(bc, activated_namespaces=["nonexistent"])
    assert "CALL ext[5][2](1)" in dis
