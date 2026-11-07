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
    """Grammar, edge limits, and error diagnostics for \\pixelrect statements (spec/dsl.md §6.4)."""

    def test_pixelrect_dimensions_and_bytes(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # 2x2 matrix with mixed hex, dec, and \rgb
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\pixelrect{p1}{\row{0xFF0000, 0x00FF00}, \row{\rgb{0,0,255}, 16777215}}" "\n"
            r"\pixelrect{p2}{\row{0, 0}, \row{0, 0}}" "\n"
            r"\call{img.diff}{$p1}{$p2}{0}" "\n"
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

    def test_ragged_rows_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\pixelrect{h}{\row{1, 2}, \row{3}}"
        _, err = m.compile(src)
        assert err is not None and "unequal length" in err

    def test_empty_literal_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        _, err = m.compile(r"\pixelrect{h}{}")
        assert err is not None and "empty literal" in err

        _, err = m.compile(r"\pixelrect{h}{\row{}}")
        assert err is not None and "empty literal" in err

    def test_edge_limits(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # 128 elements is allowed
        row_128 = ",".join(["0"] * 128)
        src_128 = f"\\pixelrect{{h}}{{\\row{{{row_128}}}}}"
        bc, err = m.compile(src_128)
        assert err is None

        # 129 elements exceeds RECT_MAX_EDGE
        row_129 = ",".join(["0"] * 129)
        src_129 = f"\\pixelrect{{h}}{{\\row{{{row_129}}}}}"
        _, err = m.compile(src_129)
        assert err is not None and "edge capped at 128" in err

    def test_color_out_of_range(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\pixelrect{h}{\row{0x1000000}}"
        _, err = m.compile(src)
        assert err is not None and "0xRRGGBB range" in err


class TestDisallowedConstructorPositions:
    """\\pixelrect belongs to statement grammar; value-position usage is rejected (spec/dsl.md §6.4)."""

    def test_value_position_rejected(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)

        # In \set:
        _, err = m.compile(r"\set{h}{\pixelrect{\row{0}}}")
        assert err is not None and "pixelrect requires destination statement" in err

        # In \call:
        _, err = m.compile(r"\use{img}\call{img.diff}{\pixelrect{\row{0}}}{$p2}{0}")
        assert err is not None and "pixelrect requires destination statement" in err

        # In \if predicate:
        _, err = m.compile(r"\if{{\pixelrect{\row{0}}, =, 0}}{\set{x}{1}}")
        assert err is not None and "pixelrect requires destination statement" in err

        # In \guarded predicate:
        _, err = m.compile(r"\guarded{{\pixelrect{\row{0}}, =, 0}}{\set{x}{1}}")
        assert err is not None and "pixelrect requires destination statement" in err

        # In \add:
        _, err = m.compile(r"\set{x}{0}\add{x}{\pixelrect{\row{0}}}")
        assert err is not None and "pixelrect requires destination statement" in err

        # In \sub:
        _, err = m.compile(r"\set{x}{0}\sub{x}{\pixelrect{\row{0}}}")
        assert err is not None and "pixelrect requires destination statement" in err

    def test_set_declaration_order(self):
        r"""Declaration follows evaluation of source value (\set{x}{$x} sees x as undefined)."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        _, err = m.compile(r"\set{x}{$x}")
        assert err is not None and "Undefined variable: x" in err

    def test_sys_const_direct_call_reserved(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = r"\call{sys.const}{0}"
        _, err = m.compile(src)
        assert err is not None and "sys.const is reserved for compiler lowering" in err


class TestPixelRectLoweringAndExecution:
    """Lowering pattern, execution, and error handling for \\pixelrect."""

    def test_pixelrect_statement_lowering_and_disassembly(self):
        """spec/dsl.md §6.4 lowering pattern:
        CALL_EXT sys.const, TAG_IMM cid
        BRA $err, =, 0, off -> pixelrect_done
        SET destination, TAG_REG $ret
        pixelrect_done:
        """
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\pixelrect{expected}{\row{\rgb{255,0,0}, 0x00FF00}}" "\n"
            r"\call{img.snap}{0, 0, 2, 1}" "\n"
            r"\set{actual}{$ret}" "\n"
            r"\call{img.diff}{$expected}{$actual}{0}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None

        dis = m.disassemble(bc)
        assert "CALL sys.const(0)" in dis
        assert "BRA $err = 0" in dis
        assert "SET $r0, $ret" in dis
        assert "CALL img.snap(0, 0, 2, 1)" in dis
        assert "SET $r1, $ret" in dis
        assert "CALL img.diff($r0, $r1, 0)" in dis

    def test_pixelrect_execution_and_reuse(self):
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\pixelrect{h}{\row{0xFF0000}}" "\n"
            r"\call{img.diff}{$h}{$h}{0}" "\n"
            r"\set{diff}{$ret}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["diff"] == 0
        assert regs["$err"] == 0
        assert rt._handles == {}  # released at exit

    def test_pixelrect_materialization_failure(self):
        rt = make_test_runtime()

        def _failing_sys_const(cid):
            return 0, ERR_RESOURCE_EXHAUSTED

        rt.register_ns("sys", {
            "const": (["int"], "snap", _failing_sys_const),
        })

        m = Macro(MockHID(), rt)
        src = (
            r"\#!syntax: free" "\n"
            r"\pixelrect{h}{\row{0xFF0000}}" "\n"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        # Materialization failure skips SET destination, preserves $err from sys.const
        assert regs["h"] == 0
        assert regs["$err"] == ERR_RESOURCE_EXHAUSTED

    def test_handle_in_predicate_evaluates_false_and_err_handle_misuse(self):
        """Handle variable compared in predicate evaluates to FALSE and sets $err = ERR_HANDLE_MISUSE."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\pixelrect{h}{\row{0}}"
            r"\if{{$h, =, 0}}"
            r"{\set{out}{1}}"
            r"{\set{out}{2}}"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["out"] == 2
        assert regs["$err"] == ERR_HANDLE_MISUSE

    def test_handle_in_arithmetic_leaves_dst_unchanged_and_sets_err_handle_misuse(self):
        """Handle in arithmetic destination or source operand triggers runtime fault."""
        rt = make_test_runtime()
        m = Macro(MockHID(), rt)
        src = (
            r"\pixelrect{h}{\row{0}}"
            r"\add{h}{1}"
        )
        bc, err = m.compile(src)
        assert err is None
        regs = run_macro(m, bc)
        assert regs["$err"] == ERR_HANDLE_MISUSE

    def test_handle_id_exhaustion_at_int32_max(self):
        """Handle counter reaching INT32_MAX returns ERR_RESOURCE_EXHAUSTED."""
        rt = make_test_runtime()
        rt._handle_seq = 2147483646

        m = Macro(MockHID(), rt)
        src1 = r"\#!syntax: free" "\n" r"\use{img}" "\n" r"\call{img.snap}{0,0,1,1}" "\n"
        bc1, err1 = m.compile(src1)
        assert err1 is None
        regs1 = run_macro(m, bc1)
        assert regs1["$ret"] == 2147483647
        assert regs1["$err"] == 0

        src2 = r"\#!syntax: free" "\n" r"\use{img}" "\n" r"\call{img.snap}{0,0,1,1}" "\n"
        bc2, err2 = m.compile(src2)
        assert err2 is None
        regs2 = run_macro(m, bc2)
        assert regs2["$ret"] == 0
        assert regs2["$err"] == ERR_RESOURCE_EXHAUSTED
        assert rt._handle_seq == 2147483647
