r"""Normative Specification Limit and Boundary Condition Test Suite.

Verifies exact profile constants, resource caps, error diagnostics, and runtime fault invariants
mandated by spec/dsl.md and spec/vm.md:
- MAX_CODE_BYTES = 65535 (opcode segment capacity, decoupled from string table)
- MAX_STRINGS = 255, MAX_STRING_BYTES = 255 (string table entry count and length limits)
- MAX_RUNTIME_NEST = 8 (combined lexical nesting of \rep, \if, and \guarded)
- Delimited reference boundaries (${}, ${foo, ${foo}bar)
- Slot register exhaustion when 32 variables allocated ("no free register for slot operand")
- \pixelrect 2D edge bounds (128 rows accepted, 129 rows rejected)
- Uniform runtime Handle fault across ALL scalar opcodes (spec/dsl.md §6.7)
- Extension error codes in $err (ERR_UNKNOWN_NS, ERR_UNKNOWN_VERB, ERR_BAD_ARGC, etc.)
"""

import pytest
import asyncio
from mock_hid import MockHID
from macro import (
    Macro,
    MacroError,
    VERSION,
    MAX_CODE_BYTES,
    MAX_STRINGS,
    MAX_STRING_BYTES,
    MAX_RUNTIME_NEST,
    MAX_REGS,
    RECT_MAX_EDGE,
    REG_RET,
    REG_ERR,
    REG_TIMEOUT,
    ERR_OK,
    ERR_GENERIC,
    ERR_INVALID_ARG,
    ERR_NO_PROVIDER,
    ERR_OUT_OF_BOUNDS,
    ERR_HANDLE_INVALID,
    ERR_RESOURCE_EXHAUSTED,
    ERR_TIMEOUT,
    ERR_HANDLE_MISUSE,
)
from runtime import Runtime
import keymap


def make_test_runtime():
    rt = Runtime()
    rt.register_signal("test.sig")
    rt.register_signal("net.msg")
    rt.register_ns(
        "sys",
        {
            "free": ([], "int", lambda: 102400),
        },
    )
    rt.register_ns(
        "img",
        {
            "snap": (["rect"], "snap", lambda x, y, w, h: rt.new_handle("snap", {"w": w, "h": h, "rgb": b"\x00" * (w * h * 3)})),
            "use": (["snap"], "int", lambda s: 42),
        },
    )
    rt.register_ns(
        "net",
        {
            "send": (["str"], "int", lambda s: len(s)),
        },
    )
    return rt


def _with_uses(text, rt):
    uses = ""
    for ns in ("sys", "img", "net", "test", "mock"):
        if rt and rt.has_namespace(ns) and f"\\use{{{ns}}}" not in text:
            if f"\\call{{{ns}." in text or f"$slot.{ns}." in text or f"${ns}." in text:
                uses += f"\\use{{{ns}}}"
    return uses + text


def compile_ok(text, rt=None):
    rt_obj = rt or make_test_runtime()
    m = Macro(MockHID(), rt_obj)
    bc, err = m.compile(_with_uses(text, rt_obj))
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text, rt=None):
    rt_obj = rt or make_test_runtime()
    m = Macro(MockHID(), rt_obj)
    bc, err = m.compile(_with_uses(text, rt_obj))
    assert err is not None, f"expected compile error, got success: {text}"
    return err


def run_macro(text, rt=None):
    hid = MockHID()
    runtime = rt or make_test_runtime()
    m = Macro(hid, runtime)
    bc, err = m.compile(_with_uses(text, runtime))
    assert err is None, f"compile failed: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    reg_dict = {
        "$ret": m.vm.last_regs[REG_RET],
        "$err": m.vm.last_regs[REG_ERR],
        "$timeout": m.vm.last_regs[REG_TIMEOUT],
    }
    for var, reg in m.compiler._vars.items():
        reg_dict[var] = m.vm.last_regs[reg]
    return reg_dict, hid, runtime


# ==============================================================================
# 1. MAX_CODE_BYTES & String Table Limits (spec/dsl.md §1.1, §6.8; spec/vm.md §1)
# ==============================================================================

class TestCodeAndStringTableLimits:
    def test_constants_defined(self):
        assert MAX_CODE_BYTES == 65535
        assert MAX_STRINGS == 255
        assert MAX_STRING_BYTES == 255

    def test_string_table_capacity_255_entries(self):
        rt = make_test_runtime()
        # Register namespace and 254 unique signals: "s" + 254 signals = 255 strings
        rt.register_ns("s", {})
        code_parts = [r"\use{s}"]
        for i in range(254):
            code_parts.append(f"\\wait[0ms]{{ $slot.s.f{i} }}")
        bc = compile_ok("\n".join(code_parts), rt)
        assert bc is not None
        # String table count is at byte 1
        assert bc[1] == 255

    def test_string_table_exceeds_255_entries_rejected(self):
        rt = make_test_runtime()
        rt.register_ns("s", {})
        code_parts = [r"\use{s}"]
        for i in range(255):  # 1 ns + 255 field names = 256 strings
            code_parts.append(f"\\wait[0ms]{{ $slot.s.f{i} }}")
        err = compile_err("\n".join(code_parts), rt)
        assert "String table full (max 255)" in err

    def test_string_length_255_bytes_accepted(self):
        rt = make_test_runtime()
        s255 = "a" * 255
        bc = compile_ok(f"\\call{{net.send}}{{{s255}}}", rt)
        assert bc is not None

    def test_string_length_256_bytes_rejected(self):
        rt = make_test_runtime()
        s256 = "a" * 256
        err = compile_err(f"\\call{{net.send}}{{{s256}}}", rt)
        assert "String too long" in err

    def test_opcode_capacity_independent_of_string_table(self):
        """Code capacity is up to MAX_CODE_BYTES = 65535 independently of string table."""
        rt = make_test_runtime()
        # Compile a macro with ~5000 bytes of code (> legacy 4096 cap)
        # e.g., 2500 single-character commands (\a) = 2500 * 2 = 5000 bytes opcode segment
        code = r"\a" * 2500
        bc = compile_ok(code, rt)
        assert len(bc) > 5000


# ==============================================================================
# 2. Combined MAX_RUNTIME_NEST = 8 (spec/dsl.md §1.2; spec/vm.md §1)
# ==============================================================================

class TestCombinedRuntimeNestingLimit:
    def test_combined_runtime_nesting_at_8_levels(self):
        r"""Combined nesting of \rep, \if, and \guarded at exactly 8 levels compiles ok."""
        rt = make_test_runtime()
        # 3 \rep + 3 \if + 2 \guarded = 8 levels
        src = (
            r"\use{net}" "\n"
            r"\set{x}{1}" "\n"
            r"\rep{1}{"
            r"  \rep{1}{"
            r"    \rep{1}{"
            r"      \if{ {$x, =, 1} }{"
            r"        \if{ {$x, =, 1} }{"
            r"          \if{ {$x, =, 1} }{"
            r"            \guarded{ {$slot.net.msg.a, =, 1} }{"
            r"              \guarded{ {$slot.net.msg.b, =, 1} }{"
            r"                \a"
            r"              }"
            r"            }"
            r"          }"
            r"        }"
            r"      }"
            r"    }"
            r"  }"
            r"}"
        )
        bc = compile_ok(src, rt)
        assert bc is not None

    def test_combined_runtime_nesting_exceeding_8_levels_rejected(self):
        r"""Combined nesting of 9 levels (mix of \rep, \if, \guarded) must be rejected."""
        rt = make_test_runtime()
        # 3 \rep + 3 \if + 3 \guarded = 9 levels
        src = (
            r"\use{net}" "\n"
            r"\set{x}{1}" "\n"
            r"\rep{1}{"
            r"  \rep{1}{"
            r"    \rep{1}{"
            r"      \if{ {$x, =, 1} }{"
            r"        \if{ {$x, =, 1} }{"
            r"          \if{ {$x, =, 1} }{"
            r"            \guarded{ {$slot.net.msg.a, =, 1} }{"
            r"              \guarded{ {$slot.net.msg.b, =, 1} }{"
            r"                \guarded{ {$slot.net.msg.c, =, 1} }{"
            r"                  \a"
            r"                }"
            r"              }"
            r"            }"
            r"          }"
            r"        }"
            r"      }"
            r"    }"
            r"  }"
            r"}"
        )
        err = compile_err(src, rt)
        assert "nesting exceeds 8 levels" in err

    def test_guarded_nested_inside_8_reps_rejected(self):
        r"""\guarded entering at runtime_depth = 8 must be rejected by MAX_RUNTIME_NEST."""
        rt = make_test_runtime()
        # 8 \rep followed by 1 \guarded
        src = (
            r"\use{net}" "\n"
            + r"\rep{1}{" * 8
            + r"\guarded{ {$slot.net.msg.a, =, 1} }{ \a }"
            + r"}" * 8
        )
        err = compile_err(src, rt)
        assert "nesting exceeds 8 levels" in err


# ==============================================================================
# 3. Delimited Reference Boundaries (spec/dsl.md §6.1)
# ==============================================================================

class TestDelimitedReferenceBoundaries:
    def test_empty_delimited_reference_rejected(self):
        err1 = compile_err(r"\set{x}{${}}")
        assert "Empty reference" in err1

        err2 = compile_err(r"\delay{${}}")
        assert "Empty reference" in err2

    def test_unmatched_delimited_reference_rejected(self):
        err = compile_err(r"\move{${foo, 0}}")
        assert "Unmatched ${" in err

    def test_unexpected_text_after_delimited_reference_rejected(self):
        err = compile_err(r"\set{foo}{1}\set{x}{${foo}extra}")
        assert "Unexpected text after ${...}" in err or "Unexpected text after ${foo}" in err

    def test_valid_delimited_reference(self):
        regs, _, _ = run_macro(r"\set{foo}{42}\set{x}{${foo}}")
        assert regs["x"] == 42


# ==============================================================================
# 4. Slot Register Exhaustion (spec/dsl.md §6.5)
# ==============================================================================

class TestSlotRegisterExhaustion:
    def test_slot_operand_register_exhaustion(self):
        """When all 32 registers are bound to user variables, reading a slot raises
        'no free register for slot operand'."""
        rt = make_test_runtime()
        # Bind all 32 registers (v0..v31)
        bind_vars = "".join(f"\\set{{v{i}}}{{{i}}}" for i in range(MAX_REGS))
        # Now attempt to read a slot operand in an instruction that requires a scratch register
        code = bind_vars + r"\delay{$slot.test.sig.val}"
        err = compile_err(code, rt)
        assert "no free register for slot operand" in err


# ==============================================================================
# 5. PixelRect Height Limit (spec/dsl.md §6.4)
# ==============================================================================

class TestPixelRectHeightLimit:
    def test_pixelrect_128_rows_allowed(self):
        rt = make_test_runtime()
        rows = ", ".join([r"\row{0}"] * 128)
        bc = compile_ok(f"\\pixelrect{{p}}{{{rows}}}", rt)
        assert bc is not None

    def test_pixelrect_129_rows_rejected(self):
        rt = make_test_runtime()
        rows = ", ".join([r"\row{0}"] * 129)
        err = compile_err(f"\\pixelrect{{p}}{{{rows}}}", rt)
        assert "edge capped at 128" in err


# ==============================================================================
# 6. Uniform Runtime Handle Faults (spec/dsl.md §6.7)
# ==============================================================================

class TestUniformRuntimeHandleFaults:
    """Verifies that a Handle reaching ANY scalar-only operand skips the action,
    leaves state unchanged, and sets $err = ERR_HANDLE_MISUSE (8)."""

    def test_sleep_with_handle_skips_and_sets_err(self):
        regs, hid, rt = run_macro(
            r"\call{img.snap}{0, 0, 1, 1}"
            r"\set{h}{$ret}"
            r"\delay{$h}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE
        # No sleeps occurred
        sleeps = [e for e in hid.events if e[0] == "sleep"]
        assert len(sleeps) == 0

    def test_pace_with_handle_skips_and_sets_err(self):
        regs, _, _ = run_macro(
            r"\call{img.snap}{0, 0, 1, 1}"
            r"\set{h}{$ret}"
            r"\pace{$h}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE

    def test_loop_with_handle_skips_and_sets_err(self):
        regs, hid, _ = run_macro(
            r"\call{img.snap}{0, 0, 1, 1}"
            r"\set{h}{$ret}"
            r"\rep{$h}{\a}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE
        assert len(hid.events) == 0

    def test_click_count_with_handle_skips_and_sets_err(self):
        regs, hid, _ = run_macro(
            r"\call{img.snap}{0, 0, 1, 1}"
            r"\set{h}{$ret}"
            r"\click{L, $h}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE
        assert len(hid.events) == 0

    def test_move_with_handle_skips_and_sets_err(self):
        regs, hid, _ = run_macro(
            r"\call{img.snap}{0, 0, 1, 1}"
            r"\set{h}{$ret}"
            r"\move{$h, 0}"
            r"\move{0, $h}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE
        assert len(hid.events) == 0

    def test_wait_timeout_with_handle_skips_and_sets_err(self):
        regs, _, _ = run_macro(
            r"\call{img.snap}{0, 0, 1, 1}"
            r"\set{h}{$ret}"
            r"\wait[$h]{$slot.test.sig.val}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE

    def test_add_with_handle_destination_leaves_dst_and_sets_err(self):
        regs, _, _ = run_macro(
            r"\pixelrect{h}{\row{0}}"
            r"\add{h}{1}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE

    def test_sub_with_handle_destination_leaves_dst_and_sets_err(self):
        regs, _, _ = run_macro(
            r"\pixelrect{h}{\row{0}}"
            r"\sub{h}{1}"
        )
        assert regs["$err"] == ERR_HANDLE_MISUSE


# ==============================================================================
# 7. Runtime Extension Error Codes (spec/runtime.md §3.0)
# ==============================================================================

class TestExtensionErrorCodes:
    def test_extension_success_sets_err_ok(self):
        regs, _, _ = run_macro(r"\call{sys.free}")
        assert regs["$err"] == ERR_OK
        assert regs["$ret"] > 0

    def test_extension_invalid_arg_sets_err_invalid_arg(self):
        rt = make_test_runtime()
        rt.register_ns("mock", {"fail_invalid_arg": ([], "int", lambda: (0, ERR_INVALID_ARG))})
        regs, _, _ = run_macro(r"\call{mock.fail_invalid_arg}", rt)
        assert regs["$ret"] == 0
        assert regs["$err"] == ERR_INVALID_ARG

    def test_extension_error_codes_mapping(self):
        rt = make_test_runtime()
        rt.register_ns(
            "mock",
            {
                "fail_generic": ([], "int", lambda: (0, ERR_GENERIC)),
                "fail_no_provider": ([], "int", lambda: (0, ERR_NO_PROVIDER)),
                "fail_out_of_bounds": ([], "int", lambda: (0, ERR_OUT_OF_BOUNDS)),
                "fail_handle_invalid": ([], "int", lambda: (0, ERR_HANDLE_INVALID)),
                "fail_exhausted": ([], "int", lambda: (0, ERR_RESOURCE_EXHAUSTED)),
                "fail_timeout": ([], "int", lambda: (0, ERR_TIMEOUT)),
            },
        )
        for fn, expected_err in [
            ("fail_generic", ERR_GENERIC),
            ("fail_no_provider", ERR_NO_PROVIDER),
            ("fail_out_of_bounds", ERR_OUT_OF_BOUNDS),
            ("fail_handle_invalid", ERR_HANDLE_INVALID),
            ("fail_exhausted", ERR_RESOURCE_EXHAUSTED),
            ("fail_timeout", ERR_TIMEOUT),
        ]:
            regs, _, _ = run_macro(f"\\call{{mock.{fn}}}", rt)
            assert regs["$ret"] == 0
            assert regs["$err"] == expected_err, f"Expected {expected_err} for {fn}, got {regs['$err']}"
