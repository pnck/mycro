"""Unit tests for Step 8: Static constructors and system intrinsic materialization.

Tests normative specifications:
- spec/dsl.md §6.4: Static Constructors (\\rgb, \\pixelrect, \\rect)
- spec/dsl.md §6.5: Parameter Categories and Lowering
- spec/dsl.md §6.8: sys.const reservation
- spec/runtime.md §3.1: sys.const intrinsic
- spec/runtime.md §3.3: img.diff behavior
- spec/runtime.md §4.1: Handle ID allocation and exhaustion at INT32_MAX
"""

import asyncio
import pytest

from macro import Macro
from bytecode import (
    MacroError,
    OP_CALL_EXT,
    OP_BRA,
    OP_SET,
    OP_END,
    REG_RET,
    REG_TIMEOUT,
    REG_ERR,
    ERR_OK,
    ERR_INVALID_ARG,
    ERR_RESOURCE_EXHAUSTED,
    ERR_HANDLE_MISUSE,
)
from runtime import Runtime
from mock_hid import MockHID


def make_test_runtime():
    """Create a runtime equipped with image manipulation extensions."""
    rt = Runtime()

    def _diff(a, b, tol):
        da = rt.handle_data(a, "snap")
        db = rt.handle_data(b, "snap")
        if da is None or db is None:
            return 0, 2  # ERR_HANDLE_INVALID
        if da["w"] != db["w"] or da["h"] != db["h"]:
            return 0, ERR_INVALID_ARG
        tol = max(0, min(255, tol))
        rgb_a = da["rgb"]
        rgb_b = db["rgb"]
        diff_count = 0
        num_pixels = da["w"] * da["h"]
        for p in range(num_pixels):
            idx = p * 3
            dr = abs(rgb_a[idx] - rgb_b[idx])
            dg = abs(rgb_a[idx + 1] - rgb_b[idx + 1])
            db_ch = abs(rgb_a[idx + 2] - rgb_b[idx + 2])
            if max(dr, dg, db_ch) > tol:
                diff_count += 1
        return diff_count, ERR_OK

    def _snap(x, y, w, h):
        hid = rt.new_handle("snap", {"w": w, "h": h, "rgb": bytes(w * h * 3)})
        if hid <= 0:
            return 0, ERR_RESOURCE_EXHAUSTED
        return hid, ERR_OK

    rt.register_ns("img", {
        "diff": (["snap", "snap", "int"], "int", _diff),
        "snap": (["rect"], "snap", _snap),
    })
    return rt


def run_macro(m, bc):
    """Execute bytecode and return a dict of user variables and special registers."""
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    reg_vals = {}
    if hasattr(m.compiler, "_vars"):
        for var, reg in m.compiler._vars.items():
            reg_vals[var] = m.vm.last_regs[reg]
    reg_vals["$ret"] = m.vm.last_regs[REG_RET]
    reg_vals["$err"] = m.vm.last_regs[REG_ERR]
    reg_vals["$timeout"] = m.vm.last_regs[REG_TIMEOUT]
    return reg_vals


class TestRgbLiteral:
    """Grammar, folding, and range tests for \\rgb{r,g,b}."""

    def test_rgb_scalar_folding(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # Black
        bc, err = m.compile(r"\set{x}{\rgb{0, 0, 0}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0

        # Red
        bc, err = m.compile(r"\set{x}{\rgb{255, 0, 0}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0xFF0000

        # Green
        bc, err = m.compile(r"\set{x}{\rgb{0, 255, 0}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0x00FF00

        # Blue
        bc, err = m.compile(r"\set{x}{\rgb{0, 0, 255}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0x0000FF

        # White
        bc, err = m.compile(r"\set{x}{\rgb{255, 255, 255}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0xFFFFFF

        # Arbitrary packed
        bc, err = m.compile(r"\set{x}{\rgb{18, 52, 86}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0x123456

    def test_rgb_in_arithmetic_and_predicates(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # \add
        bc, err = m.compile(r"\set{x}{1}\add{x}{\rgb{0, 1, 0}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 1 + 0x000100

        # \sub
        bc, err = m.compile(r"\set{x}{0x010000}\sub{x}{\rgb{1, 0, 0}}")
        assert err is None
        regs = run_macro(m, bc)
        assert regs["x"] == 0

        # Predicate comparison
        src = (
            r"\set{c}{0xFF0000}"
            r"\if{{$c, =, \rgb{255, 0, 0}}}{\set{out}{1}}{\set{out}{2}}"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["out"] == 1

    def test_rgb_in_guards(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\set{c}{0x00FF00}"
            r"\guarded{{$c, =, \rgb{0, 255, 0}}}{\set{out}{42}}"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["out"] == 42

    def test_rgb_range_errors(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # Negative component
        _, err = m.compile(r"\set{x}{\rgb{-1, 0, 0}}")
        assert err is not None and "out of range" in err

        # Component > 255
        _, err = m.compile(r"\set{x}{\rgb{256, 0, 0}}")
        assert err is not None and "out of range" in err

        # Missing component
        _, err = m.compile(r"\set{x}{\rgb{1, 2}}")
        assert err is not None and "requires {r, g, b}" in err

        # Extra component
        _, err = m.compile(r"\set{x}{\rgb{1, 2, 3, 4}}")
        assert err is not None and "requires {r, g, b}" in err

        # Non-integer component
        _, err = m.compile(r"\set{x}{\rgb{red, 0, 0}}")
        assert err is not None


class TestPixelRectParsing:
    """Grammar, edge limits, and error diagnostics for \\pixelrect and \\rect."""

    def test_pixelrect_dimensions_and_bytes(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # 2x2 matrix with mixed hex, dec, and \rgb
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\call{img.diff}"
            r"{\pixelrect{\row{0xFF0000, 0x00FF00}, \row{\rgb{0,0,255}, 16777215}}}"
            r"{\pixelrect{\row{0, 0}, \row{0, 0}}}"
            r"{0}"
        )
        bc, err = m.compile(src)
        assert err is None
        # Verify const store entries
        entry0 = rt.const_entry(0)
        assert entry0["kind"] == "snap"
        assert entry0["data"]["w"] == 2
        assert entry0["data"]["h"] == 2
        expected_bytes = bytes([
            255, 0, 0,    0, 255, 0,
            0, 0, 255,    255, 255, 255,
        ])
        assert entry0["data"]["rgb"] == expected_bytes

    def test_rect_alias_compatibility(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\call{img.diff}"
            r"{\rect{\row{0x123456}}}"
            r"{\rect{\row{0x123456}}}"
            r"{0}"
        )
        bc, err = m.compile(src)
        assert err is None
        entry = rt.const_entry(0)
        assert entry["data"]["rgb"] == bytes([0x12, 0x34, 0x56])

    def test_ragged_rows_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\set{h}{\pixelrect{\row{1, 2}, \row{3}}}"
        _, err = m.compile(src)
        assert err is not None and "ragged rows" in err

    def test_empty_literal_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        _, err = m.compile(r"\set{h}{\pixelrect{}}")
        assert err is not None and "empty literal" in err

        _, err = m.compile(r"\set{h}{\pixelrect{\row{}}}")
        assert err is not None and "empty literal" in err

    def test_edge_limits(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # 128 elements is allowed
        row_128 = ",".join(["0"] * 128)
        src_128 = f"\\set{{h}}{{\\pixelrect{{\\row{{{row_128}}}}}}}"
        bc, err = m.compile(src_128)
        assert err is None

        # 129 elements exceeds RECT_MAX_EDGE
        row_129 = ",".join(["0"] * 129)
        src_129 = f"\\set{{h}}{{\\pixelrect{{\\row{{{row_129}}}}}}}"
        _, err = m.compile(src_129)
        assert err is not None and "edge capped at 128" in err

    def test_color_out_of_range(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\set{h}{\pixelrect{\row{0x1000000}}}"
        _, err = m.compile(src)
        assert err is not None and "out of 0xRRGGBB range" in err


class TestDisallowedConstructorPositions:
    """Constructors cannot be used at top level, in guards, or as non-snap args."""

    def test_top_level_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        _, err = m.compile(r"\pixelrect{\row{0}}")
        assert err is not None and "value-position literal" in err

        _, err = m.compile(r"\rect{\row{0}}")
        assert err is not None and "value-position literal" in err

        _, err = m.compile(r"\rgb{0,0,0}")
        assert err is not None and "value-position literal" in err

    def test_guard_predicate_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\guarded{{\pixelrect{\row{0}}, =, 0}}{\set{x}{1}}"
        _, err = m.compile(src)
        assert err is not None and "guards do not support constructors" in err

    def test_sys_const_direct_call_reserved(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\call{sys.const}{0}"
        _, err = m.compile(src)
        assert err is not None and "sys.const is reserved for compiler lowering" in err

    def test_constructor_as_non_snap_arg_rejected(self):
        rt = make_test_runtime()
        rt.register_ns("test", {
            "intfn": (["int"], "int", lambda x: (x, ERR_OK)),
        })
        m = Macro(MockHID(), rt)
        src = r"\call{test.intfn}{\pixelrect{\row{0}}}"
        _, err = m.compile(src)
        assert err is not None and "pixelrect literal is a snap handle" in err


class TestSpecVectors:
    """Acceptance tests matching normative vectors in tests/spec/vectors.json."""

    def test_vector_compile_pixelrect_scratch_exhausted(self):
        """Vector compile.pixelrect_scratch_exhausted:
        All 32 registers bound to user variables leaves no scratch for constructor."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        lines = [r"\#!syntax: free", r"\use{img}"]
        for i in range(32):
            lines.append(f"\\set{{v{i}}}{{0}}")
        lines.append(r"\call{img.diff}{\pixelrect{\row{0}}}{$v0}{0}")
        src = "\n".join(lines) + "\n"

        _, err = m.compile(src)
        assert err is not None
        assert "no free register for value operand" in err

    def test_vector_compile_pixelrect_dual_constructors_symbolic(self):
        """Vector compile.pixelrect_dual_constructors_symbolic:
        Consecutive scratch registers r0, r1 with shared consumer_skip."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\call{img.diff}"
            r"{\pixelrect{\row{\rgb{255,0,0},0x00FF00}}}"
            r"{\pixelrect{\row{0x0000FF,0xFFFFFF}}}"
            r"{0}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None

        # Verify const store entries
        e0 = rt.const_entry(0)
        assert e0["data"]["rgb"].hex() == "ff000000ff00"
        e1 = rt.const_entry(1)
        assert e1["data"]["rgb"].hex() == "0000ffffffff"

        # Disassemble to inspect symbolic structure
        dis = m.disassemble(bc)
        assert "CALL sys.const(0)" in dis
        assert "CALL sys.const(1)" in dis
        assert "SET $r0, $ret" in dis
        assert "SET $r1, $ret" in dis
        assert "CALL img.diff($r0, $r1, 0)" in dis

    def test_vector_vm_pixelrect_dual_constructors_success(self):
        """Vector vm.pixelrect_dual_constructors_success:
        Constructors materialize handles 1 and 2, img.diff returns 2, handles released at exit."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\call{img.diff}"
            r"{\pixelrect{\row{\rgb{255,0,0},0x00FF00}}}"
            r"{\pixelrect{\row{0x0000FF,0xFFFFFF}}}"
            r"{0}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["$ret"] == 2
        assert regs["$err"] == 0
        assert rt._handles == {}
        assert rt._handle_seq == 2

    def test_vector_vm_pixelrect_materialization_failure_skips_remaining(self):
        """Vector vm.pixelrect_materialization_failure_skips_remaining:
        First constructor failure skips remaining constructor and consumer call."""
        rt = make_test_runtime()
        calls = []

        def _failing_sys_const(cid):
            calls.append(cid)
            return 0, ERR_RESOURCE_EXHAUSTED

        rt.register_ns("sys", {
            "const": (["int"], "snap", _failing_sys_const),
        })

        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\call{img.diff}{\pixelrect{\row{0}}}{\pixelrect{\row{1}}}{0}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)

        # Only first constructor was called
        assert calls == [0]
        assert regs["$ret"] == 0
        assert regs["$err"] == ERR_RESOURCE_EXHAUSTED

    def test_vector_vm_handle_id_exhaustion(self):
        """Vector vm.handle_id_exhaustion:
        After allocating INT32_MAX, subsequent handle allocation returns ERR_RESOURCE_EXHAUSTED."""
        rt = make_test_runtime()
        # Set handle counter near INT32_MAX
        rt._handle_seq = 2147483646

        m = Macro(MockHID(), rt)
        # First call allocates 2147483647 (INT32_MAX)
        src1 = r"\#!syntax: free" "\n" r"\use{img}" "\n" r"\call{img.snap}{0,0,1,1}" "\n"
        bc1, err1 = m.compile(src1)
        assert err1 is None
        regs1 = run_macro(m, bc1)
        assert regs1["$ret"] == 2147483647
        assert regs1["$err"] == 0

        # Second call exhausts handle IDs
        src2 = r"\#!syntax: free" "\n" r"\use{img}" "\n" r"\call{img.snap}{0,0,1,1}" "\n"
        bc2, err2 = m.compile(src2)
        assert err2 is None
        regs2 = run_macro(m, bc2)
        assert regs2["$ret"] == 0
        assert regs2["$err"] == ERR_RESOURCE_EXHAUSTED
        assert rt._handle_seq == 2147483647


class TestConstructorIntegration:
    """Integration behaviors in variables and predicates."""

    def test_constructor_in_set_and_reuse(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\set{h}{\pixelrect{\row{0xFF0000}}}" "\n"
            r"\call{img.diff}{$h}{$h}{0}" "\n"
            r"\set{diff}{$ret}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["diff"] == 0
        assert regs["$err"] == 0

    def test_constructor_in_predicate_handle_fault(self):
        """Evaluating a constructor in a predicate then comparing as scalar causes ERR_HANDLE_MISUSE."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\if{{\pixelrect{\row{0}}, =, 0}}"
            r"{\set{out}{1}}"
            r"{\set{out}{2}}"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        # Handle fault: condition is false ($err = ERR_HANDLE_MISUSE)
        assert regs["out"] == 2
        assert regs["$err"] == ERR_HANDLE_MISUSE
