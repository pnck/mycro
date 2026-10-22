# MYCRO DSL Specification

**Status: normative.** This document specifies the macro DSL's surface syntax and semantics for **DSL v4**. The reference implementation is the compiler in `src/lib/macro.py`; the bytecode it emits and execution semantics are specified in [vm.md](vm.md); the runtime environment, peripheral abstractions, and extension namespaces are specified in [runtime.md](runtime.md).

---

## 1. Core Design Axioms & System Invariants

The normative semantics and compilation model of MYCRO DSL v4 are governed by four immutable system axioms:

### 1.1 Axiom I: Zero-AST Single-Pass Streaming
The compiler is a single-pass streaming finite state machine with zero Abstract Syntax Tree (AST) allocation:
- **Zero Syntax Tree Overhead**: The compiler neither constructs nor retains heap-allocated AST nodes. The lexical scanner traverses the input ASCII character stream and directly emits binary bytecode into the output buffer in a single traversal.
- **Unidirectional Non-Backtracking Consumption**: The character stream is consumed monotonically forward. Except for the bounded macro inline expansion stack (`_nest_stack`), there is no global lookahead buffer.
- **$O(1)$ Dynamic Compilation Memory**: The compiler's working memory footprint remains strictly $O(1)$ relative to the input script size, operating safely within memory-constrained microcontroller environments.

### 1.2 Axiom II: Atomic Arithmetic & Structured Predication
The surface language is composed exclusively of explicit instructions and flat decision structures:
- Numerical state mutations are expressed strictly through atomic macro instructions (`\set`, `\add`, `\sub`). Infix operators and binary expression trees (e.g., `$x + $y * 2`) are prohibited.
- Logical decisions are structured as flat predicate triplets combined via short-circuit blocks, lowering directly into forward branch bytecode.

### 1.3 Axiom III: Strict Termination & Halting Decidability
The Control Flow Graph (CFG) is strictly constrained to forward DAGs and bounded counting loops:
- Conditional branches (`OP_BRA`) and unconditional jumps (`OP_JMP`) are strictly forward-progressing ($\text{target} > \text{pc}$).
- Loops are exclusively instantiated through bounded counter iterations (`\rep{N}`). General backward branches (`while`, `goto`) do not exist.
- Execution is statically guaranteed to halt in a finite number of cycles, time out deterministically, terminate via external task cancellation (`POST /macro/abort`), or **suspend indefinitely awaiting an external event** (deterministically exitable via external abort and guard circuit-breaking) — what the language guarantees is the absence of unbounded computational divergence, not the absence of event waiting.

### 1.4 Axiom IV: Bounded Resources & Deterministic State
All runtime execution horizons and resource allocations are bounded by static compile-time constants:
- Maximum control flow nesting depth: `MAX_RUNTIME_NEST = 8`.
- Maximum compile-time macro expansion depth: `MAX_MACRO_EXPAND = 16`.
- Maximum concurrent guard nesting depth: `MAX_GUARD_NEST = 4`.
- General-purpose register profile: `MAX_REGS = 32` scalar `i32` registers (within a `u8` addressing space ceiling of 256; fully aligned with [vm.md](vm.md) §2).
- **Deterministic Key State**: Upon normal completion of a code block, physical peripheral state is governed entirely by emitted instructions. Explicitly depressed keys remain physically held until an explicit release instruction (`\kup`, `\mup`) or an emergency fail-safe release (macro termination or guard abort).
- **Deterministic Resource Reclamation**: Managed Handle resources (such as capture buffers) are deterministically reclaimed upon macro exit via `runtime.release_all()`.

---

## 2. Lexical Schema & Primitive Tokens

The MYCRO DSL character stream is partitioned into six closed, deterministic lexical primitive schemas:

1. **Macro Commands (`\cmd`)**: Backslash `\` followed by an alphanumeric identifier sequence (e.g., `\delay`, `\rep`, `\enter`, `\ctrl+c`, user-defined macro `\foo`). Parsed by the compiler to emit corresponding VM opcodes or trigger compile-time macro inlining.
2. **Variable Sigil (`$`)**: Dollar sign `$` followed by an identifier reference (`$var`, `$ret`, `$timeout`, `$slot.<sig>.<field>`). Denotes dynamic value evaluation. Categorized into local general-purpose registers (`$var`), system read-only registers (`$ret`, `$timeout`), and **stateful receptor slots (mandatory-prefixed with `$slot.`**, e.g., `$slot.<sig>.<field>`).
3. **Numeric Literals**: Decimal integers (`120`, `-15`), hexadecimal constants (`0xFF`, `0xFF00FF`), and dimensioned time literals (`500ms`, `0.5s`). Static numeric values folded in-place during lexing into unified `i32` integer scalars.
4. **Brace Pairs (`{}`)**: Balanced enclosing pairs `{ ... }`. Syntactic scope delimiters, classified as either an Executable Code Block or an Argument Group based on host command signature.
5. **Bracket Pairs (`[]`)**: Balanced enclosing pairs `[ ... ]`. Optional configuration modifiers or decorators (e.g., `\def{name}[argc]{body}`, `\guarded[throttle]{...}`, `\wait[timeout]{...}`).
6. **Literal Keystroke Text**: Raw printable ASCII characters (codes 32–126) located in executable text positions. Emitted as physical keystrokes targeting the host operating system.

- **Comments and Shebang Directives (`\#`, `\#!`)**:
  - Standard comments begin with `\#` and span to the end of the line; silently absorbed by the lexical scanner.
  - Compiler configuration directives follow Unix shebang conventions, declared via `\#!syntax: <preset>` in leading comment lines of the script (see §5).

### 2.1 Numeric Representation & Integer Folding

All numerical values within the type system are **normatively unified as signed 32-bit integer scalars (`i32`)**:
$$\mathbb{D}_{\text{int}} = [-2^{31}, 2^{31} - 1] = [-2147483648, 2147483647]$$

#### Lexical Grammar (EBNF)
```ebnf
NumericLiteral  ::= Sign? ( HexLiteral | DimLiteral | DecLiteral )
Sign            ::= "+" | "-"
HexLiteral      ::= ("0x" | "0X") [0-9a-fA-F]+
DecLiteral      ::= [0-9]+
DimLiteral      ::= DecLiteral "ms" | [0-9]+ ("." [0-9]+)? "s"
```

#### Exact Compile-Time Constant Folding Table

| Literal Representation | Syntax Example | Mathematical Constant Folding Formula | Evaluated i32 Scalar | Canonical Use Case |
|---|---|---|---|---|
| **Decimal Integer** | `120`, `-15`, `1000` | $f(\text{raw}) = \text{int}_{10}(\text{raw})$ | `120`, `-15`, `1000` | General data, loop counters, relative coordinates |
| **Hexadecimal Integer** | `0xFF`, `0xFF00FF` | $f(\text{raw}) = \text{int}_{16}(\text{raw})$ | `255`, `16711935` | Bitmasks, packed 24-bit RGB colors |
| **Millisecond Primitive** | `500ms`, `20ms` | $f(\text{digits}) = \text{int}_{10}(\text{digits})$ | `500`, `20` | Delays, pacing intervals, register values |
| **Second Time Dimension** | `0.5s`, `1.2s`, `5s` | $f(\text{num}) = \lfloor \text{float}(\text{num}) \times 1000 + 0.5 \rfloor$ | `500`, `1200`, `5000` | Delays, guard throttling, timeout values |

#### Folding Invariants & Boundary Constraints
1. **Unified Integer Domain & Overflow Checking**: All constant folding arithmetic is calculated in 64-bit integer space. The folded scalar must fall strictly within $[-2^{31}, 2^{31} - 1]$. Values exceeding this range trigger immediate static compilation errors.
2. **Mandatory Suffix for Fractional Quantities**: Any literal containing a decimal point MUST explicitly carry the `s` dimensional suffix (e.g., `0.5s`). Dimensionless values must be pure integers.
3. **Rounding Semantics**: Sub-millisecond fractions are rounded using half-up rounding (e.g., `0.0004s` folds to `0ms`, `0.0006s` folds to `1ms`).
4. **Physical Range Assertions**: Time literals with `ms` or `s` suffixes are purely frontend syntax sugar for integer constant folding. Underlying opcodes strictly enforce physical parameter limits (e.g., `\delay{70s}` folds to $70000 > 65535$, failing `u16` assertions; `\rep{300}` fails `u8` bounds).

---

## 3. Document Grammar & The Binary Brace Taxonomy

Commands sequentially consume trailing brace pairs `{}` according to their static signatures. Every brace pair `{}` belongs to one of two mutually exclusive structures:

### 3.1 Executable Code Blocks (`{ body }`)
An executable sub-stream of macro code whose internal parsing is isomorphic to the top-level macro stream (printable ASCII characters emit keystrokes; backslashes trigger macro commands).
Host contexts: Bodies of control flow structures and user macro definitions (`{ body }` in `\rep{N}{ body }`, `{ then }` and `{ else }` in `\if`, `\def{name}[argc]{ body }`, and `{ body }` and `{ on_abort }` in `\guarded`).

### 3.2 Argument Groups & The Structural Exclusivity Law
Passes named, numeric, or conditional arguments into the host command.
**The Structural Exclusivity Law (`{A}{B} \ne {A, B}`)**: The brace structure expected by a command is statically determined by its signature contract; the two forms cannot be interchanged:
- **Separate Argument Groups `{A}{B}`**: Positional arguments reside in separate, adjacent brace pairs, each bound to an independent parameter category (e.g., `\set{var}{val}`, `\add{var}{val}`, `\sub{var}{val}`).
- **Compound Argument Groups `{A, B}`**: A single brace pair contains multiple comma-separated arguments belonging to the same functional tuple (e.g., `\move{dx, dy}`, `\click{btn, count}`).
- **Extension Call Form (`\call`)**: The initial group is `{ns.fn}`; subsequent brace structures are consumed according to the registered signature of the target extension function.
- **Compound Structures (Argument Group + Code Block)**: Argument groups strictly precede code blocks (e.g., `\rep{count}{ body }`, `\if{pred}{ then }`).

### 3.3 Optional Decorator Brackets (`[]`)
Optional scalar configuration written in literal square brackets.
**Position rule**: A decorator sits immediately adjacent to what it modifies:
- Modifying command behavior: Leads before mandatory groups (`\guarded[50ms]{...}`, `\wait[5s]{...}`).
- Modifying a specific argument: Follows that argument group (`\def{name}[argc]{body}`).

> [!NOTE]
> 1. `[...]` in signatures always denotes literal square brackets. Inside EBNF blocks, `[ x ]` denotes standard grammar optionality.
> 2. Optional code blocks (`else`, `on_abort`) are bare `{}` trailing blocks, NOT bracket decorators.
> 3. Omissible elements inside a compound argument group (e.g., `count` in `\click{btn, count}`) belong to the argument group itself.

### 3.4 Text Blocks (`\text{}` … `\endtext{}`)

```ebnf
TextBlock ::= "\text" ( "{}" | Newline ) Payload "\endtext{}"
Payload   ::= (* any character sequence not containing "\endtext{}"; zero escapes, emitted verbatim *)
```

- **Form**: A text block is formed by the pair of instructions `\text{}` and `\endtext{}` (the empty `{}` is part of each instruction's signature and emits no keystrokes). All characters between them constitute literal payload.
- **Opening Forms**:
  - `\text{}`: Payload begins immediately after `}` (an immediately following space emits Space; an immediately following newline emits Enter).
  - `\text` immediately followed by newline: Heredoc form; that newline is part of the payload (emits Enter).
- **Lexical Closure (Zero Escapes)**: Every character in the payload is emitted verbatim, independent of the active layout mode. No macro commands, comments, escapes, or `$` interpolations are recognized.
- **Termination**: The scanner searches for the literal string `\endtext{}` without backtracking. Unclosed text blocks raise an `Unterminated text block` compile error.
- **Lexical Protection**: `text` and `endtext` are reserved built-in command names. `\endtext{}` outside a text block is a compile error.
- **Full Immunity in `\def`**: Parameter text inside `\def{g}[1]{\text{}#1\endtext{}}` is passed directly into the payload without brace parsing.

---

## 4. Parameter Categories & Static Contracts

```ebnf
SlotRef    ::= "$slot." DottedName
DottedName ::= Identifier "." Identifier
```

Signal names may contain dots; split at the LAST dot: signal channel is everything to the left of the last dot, and field identifier is the last segment (e.g., `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`).

### Parameter Categories

| Parameter Category | Syntactic Form & Evaluation Rules | Compilation & Runtime Mapping |
|---|---|---|
| **Value Category (Value)** | Dynamic variables, numeric immediates, or compile-time constructors:<br>• Variables: Local registers (`$var`), system registers (`$ret`, `$timeout`), receptor slots (`$slot.<sig>.<field>`);<br>• Static literals: Decimal, hex, or time literals (`500ms`, `0.5s`), folded at lex time to `i32`;<br>• Constructors: Packed RGB color `\rgb{r, g, b}`, pixel rectangle `\rect{...}`. | **Physical 3-Tag Closed Operand Architecture**:<br>• Immediate literal $\rightarrow$ `TAG_IMM (0)` (`i32 LE`);<br>• Register $\rightarrow$ `TAG_REG (1)` (`u8 reg`);<br>• Static string $\rightarrow$ `TAG_STR (2)` (`u8 str_idx`, `\call` only);<br>• **Receptor slots (`$slot...`)** $\rightarrow$ Pre-instruction desugaring: compiler emits `0x28 OP_READSLOT` before the host instruction to load slot into an ephemeral register, passing `TAG_REG`. In `\guarded` descriptors, slots are encoded natively via the `SLOT` descriptor tag. |
| **Name Category (Name)** | Static symbolic identifiers: Key names, mouse buttons, namespaces, signal channel names, or variable names. Bare identifiers, strictly prohibiting `$`. | Statically resolved at compile time to usage IDs or string indices (`u8`). |
| **Predicate Category (Predicate)** | Conditional decision structures: Atomic triplets `{lhs, op, rhs}` or composite logic trees `\all` / `\any` (see §9). | Directly lowered by the compiler into forward short-circuit jump sequences (`OP_BRA`). |

### 4.1 Operand Tags & Slot Lowering Discipline
1. **Physical 3-Tag Closure (No 4th `TAG_SLOT`)**: Standard VM instructions permanently retain exactly three physical operand tags (`TAG_IMM=0`, `TAG_REG=1`, `TAG_STR=2`).
2. **Preceding `OP_READSLOT` Desugaring**: When DSL code references a receptor slot variable in a standard Value position (e.g., `\delay{$slot.t.val}`, `\set{x}{$slot.v.val}`), the compiler emits:
   `0x28 OP_READSLOT temp_reg, sig_str_idx, field_str_idx`
   immediately prior to the host instruction, and passes `TAG_REG temp_reg`.
3. **Native Slot in Guard Descriptors (`\guarded`)**: `0x25 GUARD_ENTER` embeds its own 2-bit operand tag (`0: IMM, 1: REG, 2: SLOT`) directly in its predicate descriptor so high-frequency delay-slice polling can inspect slots without repeated bytecode decoding.

### 4.2 Physical Word-Length Contracts

| Target Instruction | Underlying Opcode Width | Valid Immediate Interval | Semantic Invariants & Out-of-Range Handling |
|---|---|---|---|
| `\delay{t}`, `\pace{t}` | `u16` (2 bytes LE) | $[0, 65535]\text{ ms}$ | Hardware timing base. Durations $> 65535\text{ ms}$ must be expressed via `\rep` loops (rejected by static range assertions). |
| `\rep{N}` | `u8` (1 byte) | $[0, 255]$ | Loop counter. Immediates $> 255$ trigger `rep count out of u8 range`. |
| `\move{dx, dy}` | `i16` (2 bytes LE) | $[-32768, 32767]$ | Relative mouse movement offsets. |
| `\wait[timeout]{ $slot.<sig>.<field> }` | `u32` (4 bytes LE) | $[0, 4294967295]\text{ ms}$ | Timeout duration (0 or omitted = indefinite; maps to `OP_WAIT u8 sig, u8 field, typed(u32)`). |
| `\set{var}{val}`, `\add{var}{val}`, `\sub{var}{val}` | `i32` (4 bytes LE) | $[-2^{31}, 2^{31} - 1]$ | General-purpose scalar registers. |

### 4.3 Static Compile Diagnostics

- **Character Set & Delimiters**: Non-printable ASCII (outside 32–126) and unmatched delimiters (`{`, `}`, `[`, `]`, `${`) raise compile errors.
- **Name vs Value Partition**: Name positions strictly forbid `$`. Undefined variables, unknown keys, unknown mouse buttons, unknown namespaces, and unknown signals raise compile errors.
- **Eradication of Silent Truncation**: Immediate values outside valid physical opcode ranges halt compilation immediately.
- **Preamble Directive Ordering**: `\#!syntax:` directives must appear at the head of the file before any executable action token.
- **Predicate Triplet Structure**: Predicate arguments must be formatted as dual-brace triplets `{ {lhs, op, rhs} }` with operators in `{=, !=, <, <=, >, >=}`.
- **Receptor Slot Wait Target**: The argument to `\wait` must be a `$slot.<sig>.<field>` receptor. Passing local variables, system registers, or literals raises a compile error.
- **Guard Nesting Limit**: `\guarded` nesting cannot exceed `MAX_GUARD_NEST = 4`.

---

## 5. Preamble Directives & Layout Modes (`\#!syntax`)

### 5.1 Formal Grammar (EBNF)

```ebnf
PreambleDirective ::= PreamblePrefix "syntax:" Whitespace* SyntaxConfig
PreamblePrefix    ::= "#!"
SyntaxConfig      ::= Preset | ConfigList
Preset            ::= "free" | "typewriter"
ConfigList        ::= ConfigItem ("," Whitespace* ConfigItem)*
ConfigItem        ::= Key Whitespace* "=" Whitespace* Value
Key               ::= "newline" | "space"
Value             ::= "ignore" | "emit"
Whitespace        ::= " " | "\t"
```

#### Valid Forms and Equivalences

| Declaration Form | Internal Equivalent | Scanner Behavior | Target Use Case |
|---|---|---|---|
| `\#!syntax: free` | `newline=ignore, space=ignore` | **Free Layout Mode (explicitly activated)**: Unescaped newlines and spaces are silently absorbed; explicit blanks emitted via `\enter`, `\space`, `\tab`; whitespace-bearing text via `\text{}`…`\endtext{}`. Canonical recognition marker for v4 structured scripts. | Structured macro scripts (loops, branches, user macros, indentation). |
| `\#!syntax: typewriter` | `newline=emit, space=emit` | **Typewriter Mode (default)**: Raw newlines emit `Enter`; raw spaces emit `Space`. Scripts without preamble behave byte-identically to v3. | Raw text typing, automated form filling, legacy v3 scripts. |
| `\#!syntax: newline=ignore, space=emit` | Fine-Grained Config | **Orthogonal Hybrid**: Newlines allow indentation; spaces emit `Space`. | Indented scripts requiring frequent inline whitespace typing. |

- **Comment Orthogonality Law**:
  - `\# <text>`: Standard human-readable comments; silently ignored.
  - `\#!<directive>`: Compiler preamble directive; processed exclusively at the head of the file.

### 5.2 Lexer Scanning State Machine

| Input Stream Character | Context | `free` Mode (`\#!syntax: free`) | `typewriter` Mode (default) |
|---|---|---|---|
| Newline `\n` / `\r\n` | Text Position | **Silently Absorbed** | **Emits Enter Key** (`0x02 KEY enter`) |
| Space `' '` / Tab `\t` | Text Position | **Silently Absorbed** | **Emits Space / Tab Key** |
| Explicit `\enter`, `\space`, `\tab` | Text Position | **Emits Target Keystroke** | **Emits Target Keystroke** |
| Line Continuation `\` + Newline | Text Position | **Silently Absorbed** | **Silently Absorbed** |
| `\text{}`…`\endtext{}` payload | Text block | **Every character emitted literally** | **Every character emitted literally** |
| Whitespace inside `{ a , b }` | Argument Position | **Always Automatically Trimmed** | **Always Automatically Trimmed** |
| Printable ASCII (32–126) | Text Position | **Emits Literal Keystroke** | **Emits Literal Keystroke** |

---

## 6. Base Macro Instruction System

### 6.1 Time Literals & u16 Constraints
1. **Direct Expression & In-Place Folding**: Time values are expressed via dimensioned literals (`50ms`, `0.5s`) or dimensionless integers (ms), folded at compile time into integer millisecond scalars.
2. **VM Physical Operands Strictly Retain `u16`**: Underlying VM opcodes `OP_SLEEP` (`0x04`) and `OP_PACE` (`0x07`) strictly retain `u16 ms` ($0 \le t \le 65535$).
3. **Static Range Assertions**: Folded constants $> 65535\text{ ms}$ or $< 0$ trigger compile errors. Long delays must use bounded loops (e.g., `\rep{100}{ \delay{1s} }`).
4. **Dynamic Variable Range Handling**: When a timing command consumes a register (e.g., `\delay{$d}`), the runtime VM **clamps** the value to $[0, 65535]$. Bitwise masking is forbidden.

### 6.2 Base Instruction Table

```ebnf
DelayStmt ::= "\delay" "{" Duration "}"
PaceStmt  ::= "\pace" "{" Duration "}"
WaitStmt  ::= "\wait" [ "[" Duration "]" ] "{" SlotRef "}"
ClickStmt ::= "\click" "{" Button [ "," Value ] "}"
Duration  ::= DimLiteral | DecLiteral | "$" VarName
Button    ::= "L" | "R" | "M" | "B" | "F"
```

- **Timing & Synchronization**:
  - `\delay{t}`: Sleep for duration `t` ($0 \le t \le 65535\text{ ms}$).
  - `\pace{t}`: Configure inter-keystroke pacing interval ($0 \le t \le 65535\text{ ms}$).
  - `\wait[timeout]{ $slot.<sig>.<field> }`: Suspend until the target receptor slot receives an external projection. If `slot.pending == True`, consumes the event and returns immediately with zero delay. Optional decorator `timeout` in ms (0 or omitted = indefinite). Writes 1 to `$timeout` on timeout, 0 on arrival.
- **Keyboard Operations**:
  - Special Keys: `\enter`, `\esc`, `\tab`, `\space`, `\bs`, `\del`, `\f1`..`\f12`.
  - Key Combos: `\ctrl+c`, `\ctrl+alt+del` (chained with `+`).
  - Key Press / Release: `\kdown{key}`, `\kup{key}` (`key` in Name category; `\kup{0}` releases all).
- **Text Block Output**:
  - `\text{}`…`\endtext{}`: Verbatim text emission pair (see §3.4). Every enclosed character emits physical keys under any layout mode.
- **Mouse Operations**:
  - `\click{btn[, count]}`: Mouse click (`btn` in Name category `L|R|M|B|F`; `count` omissible in-group Value, defaults to 1).
  - `\move{dx, dy}`: Relative mouse move (`dx`, `dy` in Value category, signed `i16`).
  - `\mdown{btn}`, `\mup{btn}`: Mouse button hold / release.
- **Bounded Counting Loops**:
  - `\rep{count}{ body }`: Counter loop ($0 \le count \le 255$, or register `$var`; 0 skips); `body` is an Executable Code Block. Nesting ceiling: `MAX_RUNTIME_NEST = 8`.

---

## 7. State & Atomic Arithmetic Instructions

```ebnf
SetStmt ::= "\set" "{" VarName "}" "{" Value "}"
AddStmt ::= "\add" "{" VarName "}" "{" Value "}"
SubStmt ::= "\sub" "{" VarName "}" "{" Value "}"
RgbLit  ::= "\rgb" "{" Value "," Value "," Value "}"
```

- **Assignment & Arithmetic**:
  - `\set{var}{val}`: Assign `val` to register `var` (VM opcode `0x20 SET`).
  - `\add{var}{val}`: Add `val` to register `var` with $2^{32}$ wraparound (VM opcode `0x21 ADD`).
  - `\sub{var}{val}`: Subtract `val` from register `var` with $2^{32}$ wraparound (VM opcode `0x24 SUB`).
  - State mutations are strictly atomic; no compound expressions are supported.
- **Special Read-Only Variables**:
  - `$ret`: Return value or Handle from the most recent `\call`.
  - `$timeout`: Timeout flag from the most recent `\wait` (1 = timed out, 0 = event arrived).
- **Stateful Receptor Slots (`$slot.<sig>.<field>`)**:
  - Mandatory prefix `$slot.`; explicit field name (no shorthand).
  - Default signal-to-slot projection maps single scalar payloads to `$slot.<sig>.val`.
  - Per-(signal, field) arrival flag `pending`: set to `True` on projection; consumed and reset to `False` by `\wait`.
- **Keystroke Number Formatting**:
  - `\typenum{$var}`: Types the decimal integer value of `$var` as ASCII keystrokes with active pacing. Handles cannot be formatted.
- **Color Constant Packing**:
  - `\rgb{r, g, b}`: Packs three 0–255 integer constants in Value positions into a 24-bit RGB scalar immediate: `(r << 16) | (g << 8) | b`.

---

## 8. Compile-Time Text Macros (`\def`)

```ebnf
DefStmt ::= "\def" "{" Name "}" [ "[" Argc "]" ] "{" CodeBlock "}"
Argc    ::= "0" | "1" | ... | "9"
```

- **Definition & Expansion**:
  - Declared at top level: `\def{name}[argc]{body}`.
  - Parameters `#1`–`#9` are single-pass textual substitutions expanded on the compiler's `_nest_stack`. Substituted text is not rescanned for parameters. `##` emits a literal `#`.
  - Expansion depth bounded by `MAX_MACRO_EXPAND = 16`.
  - Macro expansion bodies directly inherit the script's preamble layout configuration (`\#!syntax`).
  - Parameters forward transparently into compound argument groups and predicate triplets without call-site parsing.

---

## 9. Conditional Control & Guarded Execution (`\if`, `\guarded`)

### 9.1 Predicate Grammar (EBNF)

```ebnf
Predicate      ::= Triplet | LogicBlock
Triplet        ::= "{" Value "," RelOp "," Value "}"
RelOp          ::= "=" | "!=" | "<" | "<=" | ">" | ">="
LogicBlock     ::= "\all" "{" Predicate ("," Predicate)* "}"
                 | "\any" "{" Predicate ("," Predicate)* "}"
```

- **Atomic Predicate Triplet**: `{ lhs, op, rhs }`, where `lhs` and `rhs` are in the Value category and `op` is a relational operator. Delimited by its own brace pair as an indivisible unit.
- **Composite Logic Trees**:
  - `\all{ P1, P2, ... }`: Conjunction (AND); short-circuit evaluated.
  - `\any{ P1, P2, ... }`: Disjunction (OR); short-circuit evaluated.
- **Structural Isomorphism Law**: The first brace group of `\if` or `\guarded` is `{ predicate }`. When containing an atomic triplet, it forms the standard dual-brace structure: `\if{ {lhs, op, rhs} }{ ... }`.

### 9.2 Conditional Branch Instruction (`\if`)

```ebnf
IfStmt    ::= "\if" "{" Predicate "}" CodeBlock [ CodeBlock ]
CodeBlock ::= "{" CodeStream "}"
```

```dsl
\if{ predicate }{ then_body }
\if{ predicate }{ then_body }{ else_body }
```
- `predicate`: Predicate category argument group.
- `then_body`: Executable Code Block executed when predicate is true.
- `else_body` (optional): Bare `{}` trailing block executed when predicate is false. Omissible as a whole.
- Lowered directly into standard `0x22 BRA` (Branch on False) and `0x23 JMP` forward instructions without creating new VM branch opcodes.

### 9.3 State-Guarded Execution (`\guarded`)

```ebnf
GuardedStmt ::= "\guarded" [ "[" Duration "]" ] "{" Predicate "}" CodeBlock [ CodeBlock ]
```

```dsl
\guarded[throttle_ms]{ predicate }{ protected_body }
\guarded[throttle_ms]{ predicate }{ protected_body }{ on_abort }
```
- `[throttle_ms]` (optional decorator): Value category minimum polling interval (defaults to `0ms`, continuous checks at instruction boundaries and delay slices).
- `predicate`: Invariant predicate maintained throughout execution of `protected_body`.
- `protected_body`: Executable Code Block executed under invariant protection.
- `on_abort` (optional): Bare `{}` trailing block executed upon guard failure. Omissible as a whole.
- **Nesting Ceiling**: Verified statically; maximum nesting depth is `MAX_GUARD_NEST = 4`.
- **Lowering Pattern**:
  ```
  GUARD_ENTER abort_off, header, [throttle_ms], [pred_descriptor]
  <bytecode for protected_body>
  GUARD_EXIT
  [OP_JMP skip_abort_off]    # Emitted only if on_abort is present
  <bytecode for on_abort>
  end_guarded:
  ```
- **Fail-Safe Peripheral Release**: Upon guard failure, all pressed keys and mouse buttons are released immediately before entering `on_abort`.

### 9.4 Predicate Lowering Algorithm (Short-Circuit Forward Branches)

Predicate trees lower directly into `OP_BRA` branch sequences via syntax-directed short-circuit translation:
1. **Conjunction Node `\all{ P_1, ..., P_N }`**: The first $N-1$ child tests branch to `false_target` on false and fall through to $P_{i+1}$ on true. The final test inherits the parent targets.
2. **Disjunction Node `\any{ P_1, ..., P_N }`**: The first $N-1$ child tests branch to `true_target` on true (emitting inverted `cc` under Branch-on-False semantics) and fall through to $P_{i+1}$ on false. The final test branches to `false_target` on false.
3. **Atomic Leaf Emission & Commutative Normalization**:
   - If `lhs` is immediate and `rhs` is variable, operands are swapped and relational operator is inverted.
   - If an operand references `$slot.<sig>.<field>`, an `OP_READSLOT` is emitted to an ephemeral register.
   - If both operands are immediate constants, the condition is evaluated statically (dead branches eliminated).

---

## 10. Runtime Extension Calls

```ebnf
UseStmt  ::= "\use" "{" Namespace "}"
CallStmt ::= "\call" "{" ExtensionFn "}" ArgGroup*
```

- `\use{ns}`: Compile-time dependency declaration against the runtime namespace table. Unknown namespace raises a compile error. Emits no bytecode.
- `\call{ns.fn}{arg}...`: Invokes a registered runtime extension function. Arguments are validated against the registered signature. Return value is written to `$ret`.
- Registered namespaces (`sys`, `net`, `img`), signatures, and Handle ownership semantics are normative in [runtime.md](runtime.md).

---

## 11. Boundaries and Version History

### Deliberate Boundaries
- Numbers are `i32` only.
- Strings are not first-class values; string arguments exist only as compile-time string constants for extension calls. Runtime data enters as Handle resources.
- No arithmetic expression trees or operator precedence; arithmetic is atomic via `\set`, `\add`, `\sub`.
- No backward branches or `\while` loops; iteration is strictly bounded via `\rep{N}`.
- Single delay durations bounded by `u16` ($0..65535\text{ ms}$).

### Version History

| Version | Key Additions and Changes |
|---|---|
| **v1** (gist era) | Text typing, basic key symbols, escape characters. |
| **v2** | Mouse controls (`\click`, `\move`, `\mdown`, `\mup`), `\delay`/`\pace` split, `\rep`, `\kdown`/`\kup`, line continuations, comments. |
| **v3** | User macros `\def`; registers/`\set`/`\add`; `$ret`/`$timeout`; signal slots; `\ifnum`; `\typenum{$var}`; `\use`/`\call`/`\wait`; Name vs Value partition; signature typing and Handle ownership; decoupled compile-time expansion (`MAX_MACRO_EXPAND=16`) and runtime nesting (`MAX_RUNTIME_NEST=8`). |
| **v4** | Preamble layout modes (`\#!syntax: free` vs default `typewriter`); text blocks `\text{}`…`\endtext{}`; unified `i32` numeric folding with `ms`/`s` time literals; `u16 ms` physical delay clamping and eradication of silent truncation; Binary Brace Taxonomy & Structural Exclusivity Law; signal/slot orthogonal reset: bare Name projectors vs `$slot.<sig>.<field>` stateful receptors with lossless event latches; `\wait[timeout]{$slot...}` leading bracket decorator; structured first-class predicates (`\if` with bare `{}` else; deprecating `\ifnum`); continuous invariant guarding (`\guarded[throttle]` with delay-slicing and fail-safe release); atomic subtraction `\sub`; compile-time color packing `\rgb`. |
