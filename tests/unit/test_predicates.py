import asyncio
import keymap
from bytecode import (
    MAGIC, VERSION,
    OP_CHAR, OP_BRA, OP_JMP, OP_READSLOT,
    TAG_IMM, TAG_REG,
    CC_OPS, CONVERSE_CC, MAX_PRED_NEST, MAX_RUNTIME_NEST,
)
from macro import Macro, MacroError
from mock_hid import MockHID
from runtime import Runtime


def compile_ok(code, rt=None):
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile(code)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(code, rt=None):
    m = Macro(MockHID(), runtime=rt)
    bc, err = m.compile(code)
    assert bc is None and err, f"expected compile error, got success: {code}"
    return err


def run(code, rt=None):
    hid = MockHID()
    m = Macro(hid, runtime=rt)
    bc, err = m.compile(code)
    assert err is None, f"compile error: {err}"
    asyncio.run(m.execute(bc, default_delay_ms=0, click_hold_ms=0))
    return hid


def typed_keys(hid):
    """Return sequence of characters typed via 'kp' events."""
    out = []
    for ev in hid.events:
        if ev[0] == "kp":
            kc = ev[1]
            for ch in "abcdefghijklmnopqrstuvwxyz":
                if keymap.letter_keycode(ch) == kc:
                    out.append(ch)
                    break
            else:
                for ch in "0123456789":
                    if keymap.digit_keycode(ch) == kc:
                        out.append(ch)
                        break
    return "".join(out)


# ==============================================================================
# 1. Lexical and Syntactic Grammar Tests
# ==============================================================================

def test_predicate_atomic_dual_brace():
    """Dual-brace form \\if{ {lhs, op, rhs} }{then}[{else}] compiles."""
    bc = compile_ok("\\set{x}{1}\\if{ {$x, =, 1} }{a}")
    assert OP_BRA in bc
    assert OP_JMP in bc


def test_predicate_atomic_all_relops():
    """All 6 relational operators (=, !=, <, <=, >, >=) compile."""
    for op in ("=", "!=", "<", "<=", ">", ">="):
        bc = compile_ok(f"\\set{{x}}{{1}}\\if{{ {{$x, {op}, 2}} }}{{a}}")
        assert OP_BRA in bc


def test_predicate_compound_all():
    """\\all{ P1, P2 } compiles as conjunction."""
    bc = compile_ok("\\set{x}{1}\\set{y}{2}\\if{ \\all{ {$x, =, 1}, {$y, =, 2} } }{a}")
    assert bc.count(bytes([OP_BRA])) == 2


def test_predicate_compound_any():
    """\\any{ P1, P2 } compiles as disjunction."""
    bc = compile_ok("\\set{x}{1}\\set{y}{2}\\if{ \\any{ {$x, =, 1}, {$y, =, 2} } }{a}")
    assert bc.count(bytes([OP_BRA])) == 2


def test_predicate_syntax_errors():
    """Negative syntax tests rejecting invalid predicate structures."""
    # Missing predicate group
    assert "if requires {predicate}" in compile_err("\\if")
    # Empty predicate
    assert "empty predicate" in compile_err("\\if{}{a}")
    assert "empty predicate" in compile_err("\\if{  }{a}")
    # Triplet missing outer braces
    assert "Invalid predicate" in compile_err("\\set{x}{1}\\if{ $x, =, 1 }{a}")
    # Triplet with wrong element count
    assert "Predicate triplet requires {lhs, op, rhs}" in compile_err("\\set{x}{1}\\if{ {$x, =} }{a}")
    assert "Predicate triplet requires {lhs, op, rhs}" in compile_err("\\set{x}{1}\\if{ {$x, =, 1, 2} }{a}")
    # Invalid relational operator
    assert "Invalid relational operator" in compile_err("\\set{x}{1}\\if{ {$x, ==, 1} }{a}")
    assert "Invalid relational operator" in compile_err("\\set{x}{1}\\if{ {$x, !==, 1} }{a}")
    assert "Invalid relational operator" in compile_err("\\set{x}{1}\\if{ {$x, <>, 1} }{a}")
    # Trailing commas rejected per spec/dsl.md §6.8
    assert "trailing comma" in compile_err("\\set{x}{1}\\if{ {$x, =, 1,} }{a}")
    assert "trailing comma" in compile_err("\\set{x}{1}\\if{ \\all{ {$x, =, 1}, } }{a}")
    assert "trailing comma" in compile_err("\\set{x}{1}\\if{ \\any{ {$x, =, 1}, } }{a}")
    # Empty elements rejected per spec/dsl.md §6.8
    assert "empty element" in compile_err("\\set{x}{1}\\if{ {, =, 1} }{a}")
    assert "empty element" in compile_err("\\set{x}{1}\\if{ {$x, , 1} }{a}")
    assert "trailing comma" in compile_err("\\set{x}{1}\\if{ {$x, =, } }{a}")
    assert "empty element" in compile_err("\\set{x}{1}\\if{ \\all{ {$x, =, 1}, , {$x, =, 2} } }{a}")
    # Empty logic blocks rejected
    assert "\\all requires at least one predicate" in compile_err("\\if{ \\all{} }{a}")
    assert "\\any requires at least one predicate" in compile_err("\\if{ \\any{} }{a}")
    # Logic keywords outside predicate
    assert "\\all is only valid inside a predicate" in compile_err("\\all{ {$x, =, 1} }")
    assert "\\any is only valid inside a predicate" in compile_err("\\any{ {$x, =, 1} }")
    # Decommissioned \\ifnum follows unknown command diagnostic
    assert "Unknown" in compile_err("\\ifnum{$x}{=}{1}{a}")


def test_def_keyword_conflicts():
    """\\def cannot redefine reserved logic or conditional keywords."""
    assert "conflicts with builtin" in compile_err("\\def{if}{a}")
    assert "conflicts with builtin" in compile_err("\\def{all}{a}")
    assert "conflicts with builtin" in compile_err("\\def{any}{a}")


# ==============================================================================
# 2. Nesting Limit Boundaries (MAX_PRED_NEST = 4)
# ==============================================================================

def test_predicate_nesting_depth_boundaries():
    """Test boundary depths 1 through 4 pass; depth 5 raises error."""
    # Depth 1: atomic
    compile_ok("\\set{x}{1}\\if{ {$x, =, 1} }{a}")
    # Depth 2: logic -> atomic
    compile_ok("\\set{x}{1}\\if{ \\all{ {$x, =, 1} } }{a}")
    # Depth 3: logic -> logic -> atomic
    compile_ok("\\set{x}{1}\\if{ \\all{ \\any{ {$x, =, 1} } } }{a}")
    # Depth 4: logic -> logic -> logic -> atomic (limit boundary)
    compile_ok("\\set{x}{1}\\if{ \\all{ \\any{ \\all{ {$x, =, 1} } } } }{a}")
    # Depth 5: exceeds MAX_PRED_NEST (4)
    err = compile_err("\\set{x}{1}\\if{ \\all{ \\any{ \\all{ \\any{ {$x, =, 1} } } } } }{a}")
    assert f"Predicate nesting exceeds {MAX_PRED_NEST} levels" in err


# ==============================================================================
# 3. Normalization, Converse Mapping, and Constant Folding
# ==============================================================================

def test_converse_mapping_byte_inspection():
    """When immediate is on LHS and register on RHS, operands swap and CC inverts per CONVERSE_CC."""
    pairs = [
        ("=", 0, 0),    # converse(=) is =
        ("!=", 1, 1),   # converse(!=) is !=
        ("<", 2, 4),    # converse(<) is >
        ("<=", 3, 5),   # converse(<=) is >=
        (">", 4, 2),    # converse(>) is <
        (">=", 5, 3),   # converse(>=) is <=
    ]
    for op, orig_cc, expected_cc in pairs:
        bc = compile_ok(f"\\set{{x}}{{10}}\\if{{ {{5, {op}, $x}} }}{{a}}")
        # In bytecode: header (3B) + SET (7B) = index 10 is BRA
        bra_idx = 10
        assert bc[bra_idx] == OP_BRA
        assert bc[bra_idx + 1] == expected_cc, f"Expected converse CC {expected_cc} for op {op}, got {bc[bra_idx+1]}"
        assert bc[bra_idx + 2] == 0  # reg $x
        assert bc[bra_idx + 3] == TAG_IMM
        val = bc[bra_idx + 4] | (bc[bra_idx + 5] << 8) | (bc[bra_idx + 6] << 16) | (bc[bra_idx + 7] << 24)
        assert val == 5


def test_static_constant_folding():
    """Triplets with two immediates are statically folded into direct jumps."""
    # Statically TRUE comparisons emit JMP true_target (no OP_BRA)
    true_cases = [
        "{5, <, 10}",
        "{10, >, 5}",
        "{7, =, 7}",
        "{3, !=, 4}",
        "{5, <=, 5}",
        "{6, >=, 6}",
    ]
    for expr in true_cases:
        bc = compile_ok(f"\\if{{ {expr} }}{{a}}{{b}}")
        assert OP_BRA not in bc
        assert OP_JMP in bc
        # Run and verify 'a' is executed
        hid = run(f"\\if{{ {expr} }}{{a}}{{b}}")
        assert typed_keys(hid) == "a"

    # Statically FALSE comparisons emit JMP false_target (no OP_BRA)
    false_cases = [
        "{10, <, 5}",
        "{5, >, 10}",
        "{7, !=, 7}",
        "{3, =, 4}",
        "{6, <=, 5}",
        "{5, >=, 6}",
    ]
    for expr in false_cases:
        bc = compile_ok(f"\\if{{ {expr} }}{{a}}{{b}}")
        assert OP_BRA not in bc
        assert OP_JMP in bc
        # Run and verify 'b' is executed
        hid = run(f"\\if{{ {expr} }}{{a}}{{b}}")
        assert typed_keys(hid) == "b"


def test_register_vs_register_comparison():
    """Register vs register comparison encodes both registers."""
    bc = compile_ok("\\set{a}{10}\\set{b}{20}\\if{ {$a, <, $b} }{y}{z}")
    # SET a: 7B, SET b: 7B -> bra at index 17
    bra_idx = 17
    assert bc[bra_idx] == OP_BRA
    assert bc[bra_idx + 1] == 2  # CC: <
    assert bc[bra_idx + 2] == 0  # reg a
    assert bc[bra_idx + 3] == TAG_REG
    assert bc[bra_idx + 4] == 1  # reg b
    hid = run("\\set{a}{10}\\set{b}{20}\\if{ {$a, <, $b} }{y}{z}")
    assert typed_keys(hid) == "y"


def test_slot_operand_evaluation():
    """Slots emit READSLOT before comparison, respecting source order."""
    rt = Runtime()
    rt.register_signal("net.msg")
    rt.fire_signal("net.msg", {"val": 42})

    bc = compile_ok("\\if{ {$slot.net.msg.val, =, 42} }{y}{z}", rt=rt)
    assert OP_READSLOT in bc
    assert OP_BRA in bc

    hid = run("\\if{ {$slot.net.msg.val, =, 42} }{y}{z}", rt=rt)
    assert typed_keys(hid) == "y"


# ==============================================================================
# 4. Short-Circuit Evaluation and Truth Tables
# ==============================================================================

def test_truth_table_all_2way():
    """Verify complete truth table for \\all{A, B}: T&T->T, T&F->F, F&T->F, F&F->F."""
    table = [
        (1, 1, "y"),  # T & T -> T
        (1, 0, "z"),  # T & F -> F
        (0, 1, "z"),  # F & T -> F
        (0, 0, "z"),  # F & F -> F
    ]
    for a, b, expected in table:
        code = f"\\set{{a}}{{{a}}}\\set{{b}}{{{b}}}\\if{{ \\all{{ {{$a, =, 1}}, {{$b, =, 1}} }} }}{{y}}{{z}}"
        hid = run(code)
        assert typed_keys(hid) == expected, f"Failed for a={a}, b={b}"


def test_truth_table_any_2way():
    """Verify complete truth table for \\any{A, B}: T|T->T, T|F->T, F|T->T, F|F->F."""
    table = [
        (1, 1, "y"),  # T | T -> T
        (1, 0, "y"),  # T | F -> T
        (0, 1, "y"),  # F | T -> T
        (0, 0, "z"),  # F | F -> F
    ]
    for a, b, expected in table:
        code = f"\\set{{a}}{{{a}}}\\set{{b}}{{{b}}}\\if{{ \\any{{ {{$a, =, 1}}, {{$b, =, 1}} }} }}{{y}}{{z}}"
        hid = run(code)
        assert typed_keys(hid) == expected, f"Failed for a={a}, b={b}"


def test_all_short_circuit_skips_second_operand():
    """In \\all{A, B}, if A is False, B MUST NOT be evaluated."""
    rt = Runtime()
    rt.register_signal("net.msg")
    rt.fire_signal("net.msg", {"val": 42})
    reads = []
    orig_read_slot = rt.read_slot

    def spy_read_slot(name, field):
        reads.append((name, field))
        return orig_read_slot(name, field)

    rt.read_slot = spy_read_slot

    # First predicate ($a = 1) is False since $a is 0. Second predicate reads slot.net.msg.val.
    code = "\\set{a}{0}\\if{ \\all{ {$a, =, 1}, {$slot.net.msg.val, =, 42} } }{y}{z}"
    hid = run(code, rt=rt)
    assert typed_keys(hid) == "z"
    assert len(reads) == 0, "Short-circuit failed: second predicate was evaluated in \\all!"


def test_any_short_circuit_skips_second_operand():
    """In \\any{A, B}, if A is True, B MUST NOT be evaluated."""
    rt = Runtime()
    rt.register_signal("net.msg")
    rt.fire_signal("net.msg", {"val": 42})
    reads = []
    orig_read_slot = rt.read_slot

    def spy_read_slot(name, field):
        reads.append((name, field))
        return orig_read_slot(name, field)

    rt.read_slot = spy_read_slot

    # First predicate ($a = 1) is True since $a is 1. Second predicate reads slot.net.msg.val.
    code = "\\set{a}{1}\\if{ \\any{ {$a, =, 1}, {$slot.net.msg.val, =, 42} } }{y}{z}"
    hid = run(code, rt=rt)
    assert typed_keys(hid) == "y"
    assert len(reads) == 0, "Short-circuit failed: second predicate was evaluated in \\any!"


def test_complex_formula_all_of_any():
    """Verify full truth table for \\all{ \\any{A, B}, C } across all 8 combinations."""
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                expected_bool = (bool(a) or bool(b)) and bool(c)
                expected_char = "y" if expected_bool else "z"
                code = (
                    f"\\set{{a}}{{{a}}}\\set{{b}}{{{b}}}\\set{{c}}{{{c}}}"
                    f"\\if{{ \\all{{ \\any{{ {{$a, =, 1}}, {{$b, =, 1}} }}, {{$c, =, 1}} }} }}{{y}}{{z}}"
                )
                hid = run(code)
                assert typed_keys(hid) == expected_char, f"Failed for a={a}, b={b}, c={c}"


def test_complex_formula_any_of_all():
    """Verify full truth table for \\any{ \\all{A, B}, C } across all 8 combinations."""
    for a in (0, 1):
        for b in (0, 1):
            for c in (0, 1):
                expected_bool = (bool(a) and bool(b)) or bool(c)
                expected_char = "y" if expected_bool else "z"
                code = (
                    f"\\set{{a}}{{{a}}}\\set{{b}}{{{b}}}\\set{{c}}{{{c}}}"
                    f"\\if{{ \\any{{ \\all{{ {{$a, =, 1}}, {{$b, =, 1}} }}, {{$c, =, 1}} }} }}{{y}}{{z}}"
                )
                hid = run(code)
                assert typed_keys(hid) == expected_char, f"Failed for a={a}, b={b}, c={c}"


# ==============================================================================
# 5. Control Flow, Backpatching, and Block Edge Cases
# ==============================================================================

def test_if_without_else_branch():
    """\\if without else executes then body on true, skips on false."""
    hid_true = run("\\set{x}{1}\\if{ {$x, =, 1} }{ok}done")
    assert typed_keys(hid_true) == "okdone"

    hid_false = run("\\set{x}{0}\\if{ {$x, =, 1} }{ok}done")
    assert typed_keys(hid_false) == "done"


def test_if_empty_then_block():
    """Empty then block compiles and executes correctly."""
    hid_true = run("\\set{x}{1}\\if{ {$x, =, 1} }{}{else}done")
    assert typed_keys(hid_true) == "done"

    hid_false = run("\\set{x}{0}\\if{ {$x, =, 1} }{}{else}done")
    assert typed_keys(hid_false) == "elsedone"


def test_if_empty_else_block():
    """Empty else block compiles and executes correctly."""
    hid_true = run("\\set{x}{1}\\if{ {$x, =, 1} }{then}{}done")
    assert typed_keys(hid_true) == "thendone"

    hid_false = run("\\set{x}{0}\\if{ {$x, =, 1} }{then}{}done")
    assert typed_keys(hid_false) == "done"


def test_if_nested_in_then_and_else():
    """Nested \\if statements in then and else branches compile and route correctly."""
    code_t_t = "\\set{x}{1}\\set{y}{1}\\if{ {$x, =, 1} }{ \\if{ {$y, =, 1} }{tt}{tf} }{ \\if{ {$y, =, 1} }{ft}{ff} }"
    assert typed_keys(run(code_t_t)) == "tt"

    code_t_f = "\\set{x}{1}\\set{y}{0}\\if{ {$x, =, 1} }{ \\if{ {$y, =, 1} }{tt}{tf} }{ \\if{ {$y, =, 1} }{ft}{ff} }"
    assert typed_keys(run(code_t_f)) == "tf"

    code_f_t = "\\set{x}{0}\\set{y}{1}\\if{ {$x, =, 1} }{ \\if{ {$y, =, 1} }{tt}{tf} }{ \\if{ {$y, =, 1} }{ft}{ff} }"
    assert typed_keys(run(code_f_t)) == "ft"

    code_f_f = "\\set{x}{0}\\set{y}{0}\\if{ {$x, =, 1} }{ \\if{ {$y, =, 1} }{tt}{tf} }{ \\if{ {$y, =, 1} }{ft}{ff} }"
    assert typed_keys(run(code_f_f)) == "ff"


def test_if_inside_rep_loop():
    """\\if inside a \\rep loop evaluates per-iteration."""
    code = (
        "\\set{i}{0}\\set{even}{0}"
        "\\rep{4}{"
        "  \\add{i}{1}"
        "  \\if{ {$i, =, 2} }{ \\add{even}{1} }"
        "  \\if{ {$i, =, 4} }{ \\add{even}{1} }"
        "}"
        "\\typenum{$even}"
    )
    hid = run(code)
    assert typed_keys(hid) == "2"


def test_macro_expansion_forwarding_into_predicate():
    """Text macros forward arguments cleanly into predicate positions."""
    # Define macro asserting equality
    code = (
        "\\def{assert_eq}[2]{\\if{ {#1, =, #2} }{y}{n}}"
        "\\set{x}{42}"
        "\\assert_eq{$x}{42}"
        "\\assert_eq{$x}{99}"
    )
    hid = run(code)
    assert typed_keys(hid) == "yn"
