# MYCRO DSL v4 Specification Delta Draft

**Status: Draft / Normative Delta Candidate**  
**Target Versions: DSL v4.0 / VM v4.0 / Runtime v4.0**  
**Preceding Specification Baselines:** [spec/dsl.md](../spec/dsl.md) (v3), [spec/vm.md](../spec/vm.md) (v3), [docs/protocol.md](protocol.md) (Protocol v1).

> [!IMPORTANT]
> **Normative Baseline Invariants & Evolutionary Scope**:
> This document is an **incremental evolutionary specification delta** advancing [spec/dsl.md](../spec/dsl.md) and [spec/vm.md](../spec/vm.md). It is not an ungrounded rewrite:
> 1. **Complete Baseline Inheritance**: Well-defined v3 infrastructure—including literal ASCII keycode emission, HID usage table mapping (Page 0x07), `\#` comments, `\\` escapes, the read-only shared string constant pool, middleware slot bus and data access mechanisms, linear register machine execution, `runtime.reg_write` register mediation, and opaque Handle resource lifecycles—**remains 100% active and normative** unless explicitly amended herein.
> 2. **Targeted Problem Solving**: Every revision directly addresses concrete engineering limitations, expressiveness bottlenecks, or safety risks identified during real-world embedded deployments on the ESP32-S3 microcontroller.
> 3. **Non-Negotiable Axiomatic Discipline**: All revisions strictly obey the four foundational design axioms (Zero-AST single-pass streaming compilation, atomic arithmetic, strict termination/halting decidability, and bounded memory resources). Any proposal requiring backtrack parsing, AST heap allocation, or implicit expression trees is strictly invalid.
> 4. **Tripartite Specification Restructuring Plan**:
>    Following the maturation of this proposal, the normative v4 specification will be formally partitioned across **three dedicated specification files**:
>    - **Part I: Surface Syntax, Document Grammar & Frontend Compiler** $\rightarrow$ [spec/dsl.md](../spec/dsl.md)
>    - **Part II: Linear Register Machine, Bytecode Container & VM Core** $\rightarrow$ [spec/vm.md](../spec/vm.md)
>    - **Part III: Middleware Registry, Signal/Slot Event Latch, HID Provider & Standard Library** $\rightarrow$ `spec/runtime.md`

---

## 0. v3 Baseline Audit & v4 Specification Roadmap

Drawing from embedded microcontroller execution constraints and user feedback on complex macro scripting, the v3 specification exhibits six core architectural bottlenecks. v4 defines the following normative evolutionary tracks:

| Architectural Dimension | v3 Baseline Limitations & Bottlenecks | v4 Normative Evolution & Design Delta | Target Section & Spec Partition |
|---|---|---|---|
| **Document Layout Model** | `spec/dsl.md §1` enforces an uncompromising typewriter output mode (raw newlines emit `Enter`, raw spaces emit `Space`). Long scripts, nested control flow, and user macros cannot use code indentation or blank-line paragraphing, severely compromising script readability and maintainability. | Introduces the shebang-style preamble directive `\#!syntax`, establishing `free` layout as the normative v4 default (whitespace is silently absorbed; explicit blanks are emitted via `\enter`, `\space`, `\tab`). The historical typewriter behavior is fully preserved as the `typewriter` preset for backwards compatibility. | §5 (Part I $\rightarrow$ `spec/dsl.md`) |
| **Temporal Dimensions** | `spec/dsl.md §2, §6` accepts floating-point seconds in `\delay` and `\pace`, while the underlying `spec/vm.md §4` consumes integer milliseconds (`u16 ms` / `u32 ms`) and `$var` stores integer milliseconds. The reference implementation (`macro.py:883`) silently truncates values exceeding 65535 ms (`max(1, min(65535, ms))`), introducing quantization jitter and masking serious timing bugs. | Eliminates frontend floating-point second parsing. Introduces single-pass in-place folded time literals (`500ms`, `0.5s`) unified into the scalar integer `i32` domain. VM physical operands (`0x04 SLEEP`, `0x07 PACE`) strictly retain `u16 ms` with mandatory static range assertion diagnostics. | §2.1, §6.1 (Part I $\rightarrow$ `spec/dsl.md`) |
| **Delimiter Taxonomy & Braces** | `spec/dsl.md §1` loosely categorizes code into Text, Command, and Argument positions without formal structural grammar rules for compound arguments (`{dx, dy}`) versus separate arguments (`{var}{val}`), while conflating formal parameter positions with runtime Slot entities. | Formally establishes the **Binary Brace Taxonomy** (Executable Code Blocks vs Argument Groups) and the **Structural Exclusivity Law** (`{A}{B} \ne {A, B}`). Input parameters are orthogonally partitioned into Value, Name, and Predicate categories, completely decoupled from runtime Slots. | §3, §4 (Part I $\rightarrow$ `spec/dsl.md`) |
| **Signals & Slots** | `spec/dsl.md §1, §6` exhibits semantic inversion: `\wait{sig}` consumes a bare signal name as an event trigger, whereas slots are relegated to passive, read-only dictionaries (`$sig.field`). This severed relationship caused transient race-condition event loss in `runtime.py:273`. Furthermore, slot variables lacked a dedicated scoping prefix. | Fully resets the Signal/Slot paradigm: **Signals are strictly Name-position projection channels** (pure static identifiers, never prefixed with `$`); **Slots are strictly Value-position state receptors mandatory-prefixed with `$slot.`** (e.g., `$slot.<sig>.<field>`, with default projection to `$slot.<sig>.val`, serving as the **sole observable entities for wait and guard**). `\wait` is standardized as `\wait{ $slot.<sig>.val }[timeout]` and backed by a lossless **Event Latch** model. | §4, §6.2 (Part I $\rightarrow$ `spec/dsl.md`), Part III §1 (`spec/runtime.md`) |
| **Predicates & Guarding** | `spec/dsl.md §5` relies on the rigid 4/5-brace `\ifnum{$a}{op}{b}{then}`, which cannot express compound Boolean logic (AND/OR). Crucially, the system lacks runtime invariant guarding during long macro executions, exposing peripherals to uncontrolled state divergence during multi-second delays. | Deprecates `\ifnum` in favor of first-class structured predicates `\if{pred}{then}` (zero new VM jump opcodes; lowered to `0x22 BRA` via syntax-directed short-circuiting). Introduces continuous invariant monitoring via `\guarded` with delay-sliced polling (VM opcodes `0x25 GUARD_ENTER` / `0x26 GUARD_EXIT`). | §9 (Part I $\rightarrow$ `spec/dsl.md`), Part II (`spec/vm.md`) |
| **Visual Telemetry** | `spec/dsl.md §6` only supports polling-based RPC captures via `\call{img.snap}`. Repetitive round-trip network latency across ESP32-S3 WiFi creates severe frame bottlenecks that prevent real-time feedback loops. | Introduces push-based telemetry subscriptions via `\call{img.watch}`. The capture provider continuously streams sampled data into `$slot.img.<name>.val` and trips the `img.<name>` event signal, eliminating network round-trip stalls. | Part III §3.3 (`spec/runtime.md`) |
| **State & Atomic Arithmetic** | `spec/dsl.md §3` only provides `\set` and `\add`. Decrementing a variable requires adding a negative integer literal, making register-to-register subtraction impossible without expressions. | Upholds the Atomic Arithmetic Axiom (no expression trees) while introducing a fully symmetrical atomic decrement macro: `\sub{var}{val}` backed by native VM opcode `0x24 SUB u8 reg, typed(i32 rhs)`. | §7 (Part I $\rightarrow$ `spec/dsl.md`), Part II §3 (`spec/vm.md`) |

---

# Part I: DSL Syntax Specification Delta (`spec/dsl.md`)

## 1. Core Design Axioms & System Invariants

The normative semantics and compilation model of MYCRO DSL v4 are governed by four immutable system axioms:

### 1.1 Axiom I: Zero-AST Single-Pass Streaming
The compiler is implemented as a strict, single-pass streaming finite state machine with zero Abstract Syntax Tree (AST) allocation:
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
- Execution is statically guaranteed to halt in a finite number of cycles, time out deterministically, or terminate via external task cancellation (`POST /macro/abort`).

### 1.4 Axiom IV: Bounded Resources & Deterministic State
All runtime execution horizons and resource allocations are bounded by static compile-time constants:
- Maximum control flow nesting depth: `MAX_RUNTIME_NEST = 8`.
- Maximum compile-time macro expansion depth: `MAX_MACRO_EXPAND = 16`.
- Maximum concurrent guard nesting depth: `MAX_GUARD_NEST = 4`.
- General-purpose register profile: `MAX_REGS = 32` scalar `i32` registers (within a `u8` addressing space ceiling of 256; fully aligned with `spec/vm.md Part I §2`).
- **Deterministic Key State**: Upon normal completion of a code block, physical peripheral state is governed entirely by emitted instructions. Explicitly depressed keys remain physically held until an explicit release instruction (`\kup`, `\mup`) or an emergency fail-safe release (macro termination or guard abort).
- **Deterministic Resource Reclamation**: Managed Handle resources (such as capture buffers) are deterministically reclaimed upon macro exit via `runtime.release_all()`.

---

## 2. Lexical Schema & Primitive Tokens

The MYCRO DSL character stream is partitioned into six closed, deterministic lexical primitive schemas (with preamble configuration instructions reusing the comment token `\#!syntax:`):

1. **Macro Commands (`\cmd`)**:
   - Pattern: Backslash `\` followed by an alphanumeric identifier sequence (e.g., `\delay`, `\rep`, `\enter`, `\ctrl+c`, user-defined macro `\foo`).
   - Consumer: Parsed by the compiler to emit corresponding VM opcodes or trigger compile-time macro inlining.
2. **Variable Sigil (`$`)**:
   - Pattern: Dollar sign `$` followed by an identifier reference (e.g., `$var`, `$ret`, `$timeout`, `$slot.<sig>.<field>`).
   - Consumer: Denotes dynamic value evaluation. Categorized into local general-purpose registers (`$var`), system read-only registers (`$ret`, `$timeout`), and **stateful receptor slots (mandatory-prefixed with `$slot.`**, e.g., `$slot.<sig>.<field>` or `$slot.<sig>`). Slots are the sole observable entities in the system eligible for asynchronous waiting (`\wait`) and continuous invariant guarding (`\guarded`).
3. **Numeric Literals**:
   - Pattern: Decimal integers (`120`, `-15`), hexadecimal constants (`0xFF`, `0xFF00FF`), and dimensioned time literals (`500ms`, `0.5s`).
   - Consumer: Static numeric values folded in-place during lexing into unified `i32` integer scalars (see §2.1).
4. **Brace Pairs (`{}`)**:
   - Pattern: Balanced enclosing pairs `{ ... }`.
   - Consumer: Syntactic scope delimiters. Unambiguously classified as either an Executable Code Block or an Argument Group based on the host command signature (see §3).
5. **Bracket Pairs (`[]`)**:
   - Pattern: Balanced enclosing pairs `[ ... ]`.
   - Consumer: Optional configuration modifiers or decorators (e.g., `\def{name}[argc]{body}`, `\guarded[throttle]{...}`).
6. **Literal Keystroke Text**:
   - Pattern: Raw printable ASCII characters (codes 32–126) located in executable text positions.
   - Consumer: Immediate action tokens emitted as physical keystrokes targeting the host operating system.

- **Comments and Shebang Directives (`\#`, `\#!`)**:
   - Standard comments begin with `\#` and span to the end of the line; they are silently absorbed by the lexical scanner.
   - Compiler configuration directives follow Unix shebang conventions, declared via `\#!syntax: <preset>` in the leading comment lines of the script (see §5).

---

### 2.1 Numeric Representation & Integer Folding

#### 0. v3 Baseline Limitations & Revision Rationale
- **v3 Baseline Design (`spec/dsl.md §2, §6`)**: The v3 specification permitted fractional seconds in `\delay{sec}`, `\pace{sec}`, and `\wait{sig}[{sec}]` (e.g., `\delay{0.1}`), while variables (`$var`) were stored and consumed as integer milliseconds. The underlying VM opcodes (`spec/vm.md §4`) `0x04 SLEEP` and `0x07 PACE` strictly consume unsigned 16-bit integers (`u16 ms`), and `0x29 WAIT` consumes unsigned 32-bit integers (`u32 ms`).
- **Defect & Hazard Analysis**:
  1. *Dimensional Disconnect & Floating-Point Jitter*: Mixing seconds in literals with milliseconds in registers created cognitive dissonance and forced the microcontroller to perform runtime floating-point arithmetic, causing sub-millisecond quantization jitter and unnecessary CPU cycles.
  2. *Silent Truncation Vulnerability (`macro.py:883`)*: In the v3 reference implementation, time parsing clipped values using `max(1, min(65535, ms))`. If a user wrote `\delay{100}` intending 100 seconds, execution silently clipped the duration to 65.535 seconds without error; passing `0` was silently coerced to `1ms`. This masked severe timing defects.
- **v4 Specification Resolution**: Eliminates DSL-level floating-point seconds entirely. All numeric literals are folded in-place during compilation into the unified signed 32-bit integer domain (`i32`). VM physical parameters strictly retain `u16 ms` ($0 \le t \le 65535$), where out-of-range durations trigger immediate static compilation diagnostics. Multi-minute delays must be explicitly structured using bounded `\rep` loops.

In MYCRO DSL v4, all numerical values within the type system are **normatively unified as signed 32-bit integer scalars (`i32`)**:

$$\mathbb{D}_{\text{int}} = [-2^{31}, 2^{31} - 1] = [-2147483648, 2147483647]$$

#### 1. Lexical Grammar (EBNF)
```ebnf
NumericLiteral  ::= Sign? ( HexLiteral | DimLiteral | DecLiteral )
Sign            ::= "+" | "-"
HexLiteral      ::= ("0x" | "0X") [0-9a-fA-F]+
DecLiteral      ::= [0-9]+
DimLiteral      ::= DecLiteral "ms" | [0-9]+ ("." [0-9]+)? "s"
```

#### 2. Exact Compile-Time Constant Folding Table

| Literal Representation | Syntax Example | Mathematical Constant Folding Formula | Evaluated i32 Scalar | Canonical Use Case |
|---|---|---|---|---|
| **Decimal Integer** | `120`, `-15`, `1000` | $f(\text{raw}) = \text{int}_{10}(\text{raw})$ | `120`, `-15`, `1000` | General data, loop counters, relative coordinates |
| **Hexadecimal Integer** | `0xFF`, `0xFF00FF` | $f(\text{raw}) = \text{int}_{16}(\text{raw})$ | `255`, `16711935` | Bitmasks, packed 24-bit RGB colors |
| **Millisecond Primitive** | `500ms`, `20ms` | $f(\text{digits}) = \text{int}_{10}(\text{digits})$ | `500`, `20` | Microsecond delays, pace delays, register values |
| **Second Time Dimension** | `0.5s`, `1.2s`, `5s` | $f(\text{num}) = \lfloor \text{float}(\text{num}) \times 1000 + 0.5 \rfloor$ | `500`, `1200`, `5000` | Human-scale delays, guard throttling, timeout values |

#### 3. Folding Invariants & Boundary Constraints
1. **Unified Integer Domain & Overflow Checking**: All constant folding arithmetic is calculated in 64-bit integer space. The folded scalar must fall strictly within the closed interval $[-2^{31}, 2^{31} - 1]$. Values exceeding this range trigger immediate static compilation errors.
2. **Mandatory Suffix for Fractional Quantities**: Because the underlying state machine operates at integer millisecond granularity, any literal containing a decimal point MUST explicitly carry the `s` dimensional suffix (e.g., `0.5s`). Dimensionless values must be pure integers.
3. **Rounding Semantics**: For second-denominated literals with fractions, sub-millisecond fractions are rounded to the nearest integer millisecond using standard half-up rounding (e.g., `0.0004s` folds to `0ms`, `0.0006s` folds to `1ms`).
4. **Unified Scalar Domain vs. Physical Range Assertions**:
   In strict conformance with Zero-AST and Atomic Arithmetic axioms, the type system and VM do not construct separate runtime types for time, counts, or dimensions. Time literals with `ms` or `s` suffixes are purely frontend syntax sugar for integer constant folding. The compiler validates arguments strictly via **Physical Range Assertions**:
   - Commands consume integer immediates or registers.
   - If a folded immediate violates the physical width of the target opcode (e.g., `\delay{70s}` folding to $70000 > 65535$ for `u16`, or `\rep{300}` exceeding `u8`), compilation halts with a static diagnostic.

---

## 3. Document Grammar & The Binary Brace Taxonomy

#### 0. v3 Baseline Limitations & Revision Rationale
- **v3 Baseline Design (`spec/dsl.md §1`)**: Coarsely partitioned source code into Text positions, Command name positions, and Argument brace positions. It lacked formal structural grammar constraints for distinguishing compound argument lists (`{dx, dy}`) from separate single-argument groups (`{var}{val}`), while loosely using the term "slot" to refer to both macro formal parameters and runtime middleware variables.
- **Defect Analysis**: Macro authors frequently conflated separate arguments with compound arguments (writing `\set{x, v}` instead of `\set{x}{v}`, or `\move{x}{y}` instead of `\move{dx, dy}`). Conflating parameter positions with runtime Slot variables (`$slot.<sig>.<field>`) also caused conceptual confusion.
- **v4 Specification Resolution**: Formally establishes the **Binary Brace Taxonomy** (Executable Code Blocks vs Argument Groups) and the **Structural Exclusivity Law** (`{A}{B} \ne {A, B}`). Delimiters are strictly classified as either executable bodies or argument lists, and the terminology is cleanly decoupled into static "Argument Positions" vs runtime "Slots".

A MYCRO macro script consists of a sequential ASCII character stream. Commands sequentially consume trailing brace pairs `{}` according to their **Static Signature Contracts**.

In the compiler's grammatical model, **every brace pair `{}` is partitioned into one of two mutually exclusive structures**:

### 3.1 Executable Code Blocks (`{ body }`)
- **Semantics**: An executable sub-stream of macro code. Its internal parsing is isomorphic to the top-level macro stream (printable ASCII characters emit keystrokes; backslashes trigger macro commands).
- **Host Contexts**: Bodies of control flow structures and user macro definitions (e.g., `{ body }` in `\rep{N}{ body }`, `{ then }` and `{ else }` in `\if{pred}{ then }[{ else }]`, `\def{name}[argc]{ body }`, and `\guarded[throttle]{pred}{ body }[{ on_abort }]`).

### 3.2 Argument Groups & The Structural Exclusivity Law
- **Semantics**: Delimiters passing named, numeric, or conditional arguments into the host command. An argument group contains either a single argument or a comma-separated list of multiple positional arguments.
- **The Structural Exclusivity Law (`{A}{B} \ne {A, B}`)**:
  The brace structure expected by a command is uniquely and statically determined by its signature contract. The two forms are strictly mutually exclusive and cannot be interchanged:
  - **Separate Argument Groups `{A}{B}`**: Positional arguments reside in separate, adjacent brace pairs, each bound to an independent parameter category (e.g., `\set{var}{val}`, `\add{var}{val}`, `\sub{var}{val}`).
  - **Compound Argument Groups `{A, B}`**: A single brace pair contains multiple comma-separated arguments belonging to the same functional tuple (e.g., `\move{dx, dy}`, `\click{btn[, count]}`).
  - **Extension Call Form (`\call`)**: The initial group is fixed to `{ns.fn}`, and subsequent brace structures are consumed according to the registered signature of the target extension function (e.g., `\call{img.snap}{rect}`, `\call{img.watch}{name}{coord}{interval, duration}`).
  - **Compound Structures (Argument Group + Code Block)**: When a command accepts both parameters and an executable block, the argument groups strictly precede the code blocks (e.g., `\rep{count}{ body }`, `\if{pred}{ then }`).

### 3.3 Optional Decorator Brackets (`[]`)
Used to encapsulate optional command configurations or metadata (its internal parsing is isomorphic to an argument group, supporting single values or comma-separated tuples):
- `\def{name}[argc]{body}`: Decorates the formal parameter count (0–9).
- `\guarded[throttle_ms]{pred}{body}[{on_abort}]`: Decorates the minimum polling interval (defaults to `0ms`).
- `\wait{ $slot.<sig>.<field> }[timeout]`: The mandatory argument group is in the Value category and strictly consumes a stateful receptor slot (prefixed with `$slot.`, e.g., `$slot.net.msg.val` or abbreviated `$slot.net.msg`). The optional bracket decorators define the timeout duration (Value category, accepting `500ms`, `5s`, or `$var`, folded to integer milliseconds; defaults to indefinite blocking).

---

## 4. Parameter Categories & Static Contracts

> [!NOTE]
> **Normative Terminology & The Signal / Slot Orthogonality Axioms**:
> 1. **Argument Position (Formal Parameter)**: An input parameter consumed within an argument group `{}` or bracket decorator `[]`.
> 2. **Signal / Slot Orthogonal Binary Axioms & Naming Contracts**:
>    - **Signals are Strictly Name-Position Projection Channels (Projectors)**: Represent external event projection channels (e.g., `net.msg`, `img.frame`). They are pure static identifiers, **never prefixed with `$`**, have no dynamic storage, cannot be read or computed in value positions, and cannot be waited on directly.
>    - **Slots are Strictly Value-Position Stateful Receptors**:
>      - **Mandatory Prefix Contract**: All Slot variables **MUST strictly begin with the prefix `$slot.`** (canonical form: `$slot.<sig>.<field>`). This grammatical invariant enables the single-pass lexer to unambiguously recognize slot variables without backtracking.
>      - **Default Signal-to-Slot Projection Rule**: When the outside world emits a signal `<sig>` without specific sub-fields (such as a single integer scalar, a boolean notification, or an unnamed payload), the **default projection field is uniformly mapped to `val`** (`$slot.<sig>.val`), which latches the event as pending.
>      - **Shorthand Equivalence Law**: Writing `$slot.<sig>` in macro source code (omitting the trailing field dot) is automatically desugared by the streaming compiler to `$slot.<sig>.val`.
> 3. **The Sole Observable Receptor Axiom**:
>    - **Receptor slots starting with `$slot.` are the ONLY valid asynchronous entities eligible for waiting (`\wait`) and guarding (`\guarded`)**.
>    - General-purpose registers (`$var`, not starting with `$slot.`) represent private synchronous state inside the single-threaded macro. In the absence of concurrent macro threads, registers cannot mutate asynchronously; attempting to `\wait` on a general-purpose register triggers an immediate static compilation error.
>    - Guard predicates in `\guarded` must observe at least one `$slot.` receptor. A guard composed entirely of local registers or constants is a statically degenerate "dead guard" and is rejected at compile time.

Every argument position belongs to one of three orthogonal grammatical categories based on the host command's signature:

| Parameter Category | Syntactic Form & Evaluation Rules | Compilation & Runtime Mapping |
|---|---|---|
| **Value Category (Value)** | **Dynamic variable references, numeric immediates, or compile-time value constructors**:<br>• Variable references: Local registers (`$var`), system registers (`$ret`, `$timeout`), receptor slot variables (**strictly prefixed with `$slot.`**, e.g., `$slot.<sig>.<field>` or `$slot.<sig>`);<br>• Static literals: Decimal, hexadecimal, or time-dimensioned literals (`500ms`, `0.5s`), folded at lex time into `i32` immediates;<br>• Value constructors: Packed RGB color constant `\rgb{r, g, b}`, pixel rectangle literal `\rect{...}`. | **Physical 3-Tag Closed Operand Architecture**:<br>• Immediate literal $\rightarrow$ `TAG_IMM (0)` (`i32 LE`);<br>• Local/system register $\rightarrow$ `TAG_REG (1)` (`u8 reg`);<br>• Static string constant $\rightarrow$ `TAG_STR (2)` (`u8 str_idx`, `\call` only);<br>• **Receptor Slot variables (`$slot...`)** $\rightarrow$ Pre-instruction desugaring: The compiler emits `0x28 OP_READSLOT` before the host instruction to load the slot into an ephemeral register, passing `TAG_REG` to the host instruction. In `\guarded` predicate descriptors, slots are encoded natively via the `SLOT` descriptor tag (see §4.1). |
| **Name Category (Name)** | **Static symbolic identifiers**:<br>Key names, mouse buttons, namespaces, **Signal channel names (e.g., `net.msg`)**, or variable names. Written as bare identifiers, strictly prohibiting the dynamic variable sigil `$`. | Statically resolved at compile time into single-byte usage IDs or indices (`u8`). |
| **Predicate Category (Predicate)** | **Conditional decision structures**:<br>Accepts atomic comparison triplets `{lhs, op, rhs}` or composite logic trees `\all` / `\any` (see §9). Flanked by Value-category operands, centered on a relational operator. | Directly lowered by the compiler into forward-only short-circuit jump sequences (`OP_BRA`), driving hardware-level branch decisions. |

### 4.1 Operand Tags & Slot Lowering Discipline

In the MYCRO VM execution pipeline, operand physical tags and slot access mapping adhere to the following architectural discipline:

#### 1. Physical 3-Tag Closure (No 4th `TAG_SLOT`)
Inheriting the core specifications of `spec/vm.md §3` and `spec/dsl.md §6`, the physical operand tags of standard VM instructions **strictly and permanently consist of exactly three variants**:
- `TAG_IMM = 0`: 4-byte signed integer immediate (`i32 LE`).
- `TAG_REG = 1`: 1-byte register address index (`u8 reg`).
- `TAG_STR = 2`: 1-byte string table index (`u8 str_idx`, valid only in `CALL_EXT` arguments).

**Architectural Rationale for Omitting `TAG_SLOT` from Standard Instructions**:
- **Microcontroller Dispatch Loop Simplification**: If `TAG_SLOT` were added as a generic operand tag, every base opcode in the VM dispatch loop (`OP_SLEEP`, `OP_PACE`, `OP_SET`, `OP_ADD`, `OP_SUB`, `OP_MCLICK`, `OP_MMOVE`, `OP_LOOP`, `OP_BRA`) would need branches to perform slot dictionary lookups, namespace validation, and string pool indexing, causing unacceptable dispatch overhead.
- **Pure Register Machine Model**: All arithmetic computations and hardware timings occur strictly between registers and immediate values. External slot interactions are strictly isolated within dedicated opcodes.

#### 2. Preceding `OP_READSLOT` Desugaring for Standard Instructions
When DSL code references a receptor slot variable in a standard Value position (e.g., `\delay{$slot.t}`, `\set{x}{$slot.v}`, `\click{L, $slot.n}`), the single-pass compiler employs **pre-instruction desugaring**:
1. **In-Place Read Emission**: Prior to emitting the target instruction, the compiler allocates an ephemeral register `temp_reg` (borrowed from the high-order unallocated registers of the 32-register bank) and emits:
   `0x28 OP_READSLOT temp_reg, sig_str_idx, field_str_idx`
2. **Standard `TAG_REG` Consumption**: The subsequent host instruction directly consumes `TAG_REG temp_reg`. The physical format of the host instruction remains 100% compact and oblivious to external dictionary lookup mechanisms.
3. **Register-Write Mediation**: Executing `OP_READSLOT` naturally triggers `runtime.reg_write(temp_reg, None)`, guaranteeing that external data entering the macro pipeline is safely mediated.

#### 3. Native Slot Exception in Guard Descriptors (`\guarded`)
The only syntax entity where slot variables are encoded natively is the `\guarded` instruction.
Because guards must perform **continuous dynamic re-evaluation** across delay sleep slices (`DELAY_SLICE_QUANTUM` wake-up loops) and instruction boundaries, they cannot rely on a one-time pre-emitted `READSLOT` instruction:
- The compact predicate descriptor (`pred_descriptor`) inside `0x25 GUARD_ENTER` utilizes its own dedicated 2-bit tag field (`0: IMM, 1: REG, 2: SLOT`).
- When encountering `$slot.` variables in a guard predicate, the compiler registers the signal and field strings into the string pool and packs `(sig_str_idx, field_str_idx)` directly into the descriptor.
- Upon each slice wake-up, the VM scheduler inspects the slot's memory structure (`val` and `pending`) directly, eliminating repeated deserialization without violating the 3-tag closure of standard instructions.

### 4.2 Physical Word-Length Contracts

Value-category operands are conceptually unified as scalar `i32` integers (see §2.1). Each macro instruction enforces strict physical word-length and range bounds on immediate values corresponding to its target VM opcode:

| Target Instruction | Underlying Opcode Width | Valid Immediate Interval | Semantic Invariants & Out-of-Range Handling |
|---|---|---|---|
| `\delay{t}`, `\pace{t}` | `u16` (2 bytes LE) | $[0, 65535]\text{ ms}$ | Hardware timing base. Durations exceeding 65535 ms must be explicitly expressed via `\rep` loops (rejected by static range assertions). |
| `\rep{N}` | `u8` (1 byte) | $[0, 255]$ | Static loop counter limit. Immediate values exceeding 255 trigger a compile diagnostic (`MacroError: rep count out of u8 range`). |
| `\move{dx, dy}` | `i16` (2 bytes LE) | $[-32768, 32767]$ | Relative mouse movement offsets. |
| `\wait{ $slot.<sig>.<field> }[timeout]` | `u32` (4 bytes LE) | $[0, 4294967295]\text{ ms}$ | Timeout duration (0 or omitted represents indefinite waiting; maps to `OP_WAIT u8 sig, u8 field, typed(u32)`). Shorthand `$slot.<sig>` defaults to `field="val"`. |
| `\set{var}{val}`, `\add{var}{val}`, `\sub{var}{val}` | `i32` (4 bytes LE) | $[-2^{31}, 2^{31} - 1]$ | General-purpose scalar registers. |

- **Handle Comparison Restriction**: Opaque Handle instances only support equality comparisons (`=`, `!=`) in Value positions; the read-only register `$ret` carries error-code sentinels and supports full relational comparisons.

### 4.3 Static Compile Diagnostics Delta

#### 0. v3 Baseline Audit & Model Alignment
In the v3 specification (`spec/dsl.md`) and reference implementation (`src/lib/macro.py`), **there was never a detached taxonomy of error classes**:
1. **v3 Inline Error Invariants (`spec/dsl.md §1–§6`)**: v3 defined compilation errors as inline assertions embedded directly into grammatical rules (e.g., `compile error on $var`, `circular references are compile errors`).
2. **Unified Exception Model (`src/lib/macro.py:85`)**: Under CircuitPython / MicroPython memory constraints, the compiler intentionally uses a **single base exception class**, eschewing complex object-oriented exception hierarchies:
   ```python
   class MacroError(Exception):
       def __init__(self, msg, pos):
           super().__init__(f"@{pos}: {msg}")
   ```
3. **v3 Defect Rectification**: Certain timing parameters lacked explicit out-of-range errors and instead silently clipped values (e.g., `macro.py:883` using `max(1, min(65535, ms))`, masking logical timing bugs).

v4 adheres to the principles of inheriting the baseline, eliminating silent truncation, and adding diagnostics for new grammar features:

#### 1. Baseline Invariants Inherited
All core compile assertions from v3 remain 100% active:
- **Document & Character Set (`spec/dsl.md §1`)**:
  - Non-printable ASCII (outside codes 32–126) triggers compilation errors.
  - Unbalanced delimiters are immediately intercepted: `Unmatched {`, `Unmatched [`, `Unmatched ${`, `Unexpected text after ${...}`.
- **Parameter Categories & Symbol Resolution (`spec/dsl.md §1, §3, §6`)**:
  - Name positions strictly prohibit the `$` sigil (e.g., `\kdown{$x}` raises `kdown/kup takes a key name, not a variable`).
  - Read-before-write detection: `Undefined variable: {name}` (must `\set` before read).
  - Symbol lookup failures: `Unknown key`, `Unknown mouse button`, `Unknown namespace`, `Unknown signal`.
  - Variable name constraints: Must match `[a-z][a-z0-9_]*`; spaces, operators, or reserved names (`ret`, `timeout`) are rejected.
- **User Macro Contracts (`spec/dsl.md §4`)**:
  - Top-level definitions only (`def only allowed at top level`).
  - No macro redefinition (`macro redefined: {name}`).
  - Built-in name collision protection (`macro name conflicts with builtin: {name}`).
  - Strict arity matching (`macro {name} expects {argc} arg(s)`).
  - Recursion cycle detection (`recursive macro: {name}`) and depth limit enforcement (`MAX_MACRO_EXPAND > 16`).
- **Handle & Type Invariants (`spec/dsl.md §3, §5, §6`)**:
  - Handles cannot participate in relational comparisons (`Cannot compare handles`, except `$ret` error sentinels).
  - Handles cannot be formatted as keystrokes (`typenum cannot render handles`).
  - Extension call string literals cannot contain `$` interpolation.
  - `\rect` pixel geometry bounds: Rows must be of equal width, dimensions $\le 128$, colors valid 24-bit RGB.
- **Physical Resource Ceilings (`spec/dsl.md §3` & `spec/vm.md §2`)**:
  - General register allocation limit: `MAX_REGS = 32`.
  - Shared string constant pool limit: `MAX_STRINGS = 255`.
  - Bytecode container size ceiling: 65535 bytes.

#### 2. v4 Corrections: Eradication of Silent Truncation
Completely removes the clipping hazards in `macro.py:883`. All immediate values are aligned with the target opcode width, halting compilation upon overflow:
- `\rep{N}`: Immediate values outside `u8` ($[0, 255]$) trigger static diagnostics.
- `\delay{t}`, `\pace{t}`: Folded millisecond values outside `u16` ($[0, 65535]\text{ ms}$) trigger static diagnostics; multi-minute delays must be expressed via `\rep`.
- `\move{dx, dy}`: Relative offsets outside `i16` ($[-32768, 32767]$) trigger static diagnostics.
- `\set{var}{val}`, `\add{var}{val}`, `\sub{var}{val}`: Immediate values outside signed `i32` trigger static diagnostics.

#### 3. v4 Delta Diagnostics for New Features
The streaming frontend introduces the following deterministic static diagnostics for new capabilities:
1. **Preamble Directive Ordering (`Preamble Directive Order`)**:
   - Once the macro body emits its first physical action (keystroke or command), the preamble configuration phase permanently closes. Any subsequent `\#!syntax:` directive is either treated as a standard comment or rejected in strict mode with `MacroError: #!syntax directive must appear at the top of the file`.
2. **Predicate Dual-Brace Syntax & Operator Validation (`Predicate Syntax & Operator`)**:
   - Atomic predicate units in conditional commands (`\if`, `\guarded`) MUST be formatted as self-contained dual-brace triplets: `{ {lhs, op, rhs} }`. Violating the brace pairing or providing an operator outside the valid set (`=`, `!=`, `<`, `<=`, `>`, `>=`) triggers an immediate static error.
3. **Receptor Slot Wait Target Constraint (`Receptor Wait Target Constraint`)**:
   - The argument to `\wait` MUST be a stateful receptor slot beginning with `$slot.` (canonical form `$slot.<sig>.<field>` or shorthand `$slot.<sig>`).
   - Passing a local register (`$var`), a system register (`$ret`), or a numeric literal triggers `MacroError: wait requires a $slot.<sig>.<field> receptor`, preventing deadlocks in single-threaded execution.
4. **Guard Nesting & Liveness Diagnostics (`Guard Nesting & Liveness`)**:
   - If `\guarded` nesting exceeds the compile-time constant `MAX_GUARD_NEST = 4`, compilation halts with `MacroError: guard nesting exceeds 4 levels`.
   - If a `\guarded` predicate consists entirely of local synchronous variables and literals without observing any `$slot.` receptor, it is diagnosed as an un-triggerable "dead guard" and rejected with `MacroError: guarded predicate must observe at least one $slot receptor`.

---

## 5. Preamble Directives & Layout Modes (`\#!syntax`)

### 5.1 Formal Grammar (EBNF)
Preamble directives configure the compiler's lexical scanner behavior prior to code execution:

```ebnf
PreambleDirective ::= PreamblePrefix "syntax:" Whitespace* SyntaxConfig
PreamblePrefix    ::= ("#!" | "# !")
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
| `\#!syntax: free` | `newline=ignore, space=ignore` | **Free Layout Mode (v4 Normative Default)**: Unescaped newlines and spaces are silently absorbed; explicit blanks are emitted via `\enter`, `\space`, or `\tab`. | Structured macro scripts (with loops, branches, user macros, indentation, and blank lines). |
| `\#!syntax: typewriter` | `newline=emit, space=emit` | **Typewriter Mode (v3 Compatibility)**: Raw newlines emit physical `Enter` keystrokes; raw spaces emit `Space` keystrokes. | Raw text typing, automated form filling, legacy v3 scripts. |
| `\#!syntax: newline=ignore, space=emit` | Fine-Grained Config | **Orthogonal Hybrid**: Newlines allow indentation; spaces directly emit `Space` keystrokes. | Indented scripts requiring frequent inline whitespace typing. |

- **Preamble Scope**: The directive must appear in the leading comment lines of the script, before any non-comment, non-whitespace executable character. Once the first code token is encountered, configuration is permanently frozen.
- **Comment Orthogonality Law**:
  - `\# <text>`: Standard human-readable comments; silently ignored.
  - `\#!<directive>` / `\# !<directive>`: Compiler preamble directive; processed exclusively at the head of the file. Occurrences within the body of a script are treated as standard comments.

### 5.2 Lexer Scanning State Machine & Behavior Control

The lexical scanner operates deterministically according to the active layout configuration:

#### 1. Character Handling Matrix
| Input Stream Character | Syntactic Context | `free` Mode Behavior (v4 Default) | `typewriter` Mode Behavior (v3 Compatibility) |
|---|---|---|---|
| Newline `\n` / `\r\n` | Text Position | **Silently Absorbed** (advances line counter; no keystroke emitted) | **Emits Enter Key** (`0x02 KEY keycode_enter`) |
| Space `' '` / Tab `\t` | Text Position | **Silently Absorbed** (supports free indentation; no keystroke emitted) | **Emits Space / Tab Key** (`0x01 CHAR 0x20` / `0x02 KEY keycode_tab`) |
| Explicit `\enter`, `\space`, `\tab` | Text Position | **Emits Target Keystroke** (immediate action command) | **Emits Target Keystroke** (immediate action command) |
| Line Continuation `\` + Newline | Text Position | **Silently Absorbed** (continuation swallowed) | **Silently Absorbed** (continuation swallowed) |
| Whitespace inside Braces `{ a , b }` | Argument Position | **Always Automatically Trimmed** (spaces around arguments ignored) | **Always Automatically Trimmed** (spaces around arguments ignored) |
| Printable ASCII (32–126) | Text Position | **Emits Literal Keystroke** (`0x01 CHAR c`) | **Emits Literal Keystroke** (`0x01 CHAR c`) |

#### 2. Contextual Invariants
- **Universal Brace Trimming**: Regardless of layout mode, whitespace inside brace groups `{}` and brackets `[]` (including spaces around commas) is always trimmed and discarded. Layout modes govern only raw Text positions.
- **Macro Expansion Stream Inheritance**: When user macros defined via `\def` are inlined via the expansion stack (`_nest_stack`), the inlined character stream strictly inherits the script's preamble layout configuration.

### 5.3 Canonical Syntax Examples

```dsl
\# 1. v4 Default Free Layout (active without preamble; indentation & newlines are silent)
\def{submit_form}[0]{
  \kdown{ctrl}
  \delay{50ms}
  \kup{ctrl}
  \enter          \# Explicitly emit Enter keystroke
}

\# 2. Explicit Free Layout Declaration
\#!syntax: free
\if{ {$slot.net.ready.val, =, 1} }{
  \click{L}
  \delay{200ms}
}

\# 3. v3 Typewriter Compatibility Mode (newlines emit Enter, spaces emit Space)
\#!syntax: typewriter
echo "Hello CircuitMacro"
ls -la
```

---

## 6. Base Macro Instruction System (Keys, Mouse, Timing, Bounded Loops)

#### 0. v3 Baseline Inheritance & Revision Scope
- **Fully Inherited Features (`spec/dsl.md §2`)**: Keystroke text emission, special keys (`\enter`..`\f12`), key combinations (`\ctrl+c` chained via `+`), mouse controls (`\click`, `\move`, `\mdown`, `\mup`), key press and release (`\kdown`, `\kup`), and bounded counting loops `\rep{count}{body}` (nesting limit `MAX_RUNTIME_NEST = 8`) remain 100% active and normative.
- **v4 Evolutionary Revisions**:
  1. The arguments to `\delay{t}` and `\pace{t}` are driven by folded time literals or integer milliseconds, with physical parameter width strictly bounded to `u16` ($0..65535\text{ ms}$).
  2. Durations exceeding 65535 ms are standardized as explicit bounded loop patterns (e.g., `\rep{100}{ \delay{1s} }`), eliminating implicit integer overflow and silent clipping.

### 6.1 Time Literals & u16 Physical Parameter Constraints

> [!IMPORTANT]
> **Timing Standards & Word-Length Invariants**:
> 1. **Direct Expression & In-Place Folding**: Time values are expressed via dimensioned literals (`50ms`, `0.5s`) or dimensionless integers (base unit: ms, e.g., `50`), folded at compile time into integer millisecond scalars (see §2.1). No runtime timing unit configuration is introduced.
> 2. **VM Physical Operands Strictly Retain `u16`**: Underlying VM opcodes `OP_SLEEP` (`0x04`, `\delay`) and `OP_PACE` (`0x07`, `\pace`) **strictly retain unsigned 16-bit integers (`u16`, 2 bytes LE)**. They are intentionally NOT expanded to `u32` or `i32`.
> 3. **Static Range Assertions & Long Delay Idiom**: Single delay parameters are physically bounded to $[0, 65535]\text{ ms}$ (~65.5 seconds). If a static literal folds to a value exceeding 65535 ms (e.g., `\delay{70s}`) or less than 0, the compiler halts with an immediate static diagnostic. Longer delays must be explicitly structured using bounded loops (e.g., a 100-second pause is written as `\rep{100}{ \delay{1s} }`).
> 4. **Dynamic Variable Range Handling**: When a timing command consumes a general-purpose register (e.g., `\delay{$d}`), runtime VM execution clamps or wraps the value (`val & 0xFFFF`).

### 6.2 Base Instruction Table
- **Timing Suspension & Synchronization**:
  - `\delay{t}`: Suspends execution for duration `t` (`t` in Value category; folded time literal or integer immediate satisfying $0 \le t \le 65535\text{ ms}$; longer delays expressed via `\rep`).
  - `\pace{t}`: Configures the subsequent inter-keystroke pacing delay ($u16$ constraint: $0 \le t \le 65535\text{ ms}$).
  - `\wait{ $slot.<sig>.<field> }[timeout]`: Suspends the macro task coroutine until the target receptor slot (prefixed with `$slot.`, e.g., `$slot.net.msg.val` or shorthand `$slot.net.msg`) receives an external signal projection. If the slot has a latched pending event (`slot.pending == True`), it consumes the event and returns immediately with zero delay. Mandatory argument is in Value category, consuming a slot variable; optional bracket decorator `timeout` is in Value category, satisfying $u32$ constraints ($0 \le timeout \le 4294967295\text{ ms}$; 0 or omitted represents indefinite waiting). The timeout status is written to `$timeout` (1 on timeout, 0 on event arrival).
- **Keyboard Operations**:
  - Special Keys: `\enter`, `\esc`, `\tab`, `\space`, `\bs`, `\del`, `\f1`..`\f12`.
  - Key Combinations: `\ctrl+c`, `\ctrl+alt+del` (chained with `+`).
  - `\kdown{key}`, `\kup{key}`: Key press and release (`key` in Name category, static key symbol; `\kup{0}` releases all keys).
- **Mouse Operations**:
  - `\click{btn[, count]}`: Compound argument form; `btn` in Name category (`L|R|M|B|F`), optional `count` in Value category (defaults to 1; e.g., `\click{L}` or `\click{L, 2}`).
  - `\move{dx, dy}`: Relative movement offsets; `dx`, `dy` in Value category (signed `i16`).
  - `\mdown{btn}`, `\mup{btn}`: Mouse button press and release (`btn` in Name category).
- **Bounded Counting Loops**:
  - `\rep{count}{ body }`: `count` in Value category (immediate validated statically against $0 \le count \le 255$, or a register `$var`; 0 skips the loop); `body` is an Executable Code Block. Nesting depth ceiling: `MAX_RUNTIME_NEST = 8`.

---

## 7. State & Atomic Arithmetic Instructions

#### 0. v3 Baseline Inheritance & Symmetrical Arithmetic Extension
- **Fully Inherited Features (`spec/dsl.md §3` & `spec/vm.md §4`)**: 32 general-purpose scalar `i32` registers (zeroed at macro launch), read-only system registers `$ret` and `$timeout`, zero-copy middleware slot reading, decimal keystroke rendering `\typenum`, and pixel constants `\rect` remain active. v4 formally incorporates compile-time RGB color packing `\rgb`.
- **v4 Evolutionary Revision (Symmetrical Atomic Arithmetic)**:
  In adherence to Axiom II (Atomic Arithmetic), v4 augments `\set` (assignment) and `\add` (accumulation) with the symmetrical atomic decrement macro: `\sub{var}{val}`:
  1. **Tripartite Atomic Arithmetic**: State computation converges on `\set` (write), `\add` (accumulate), and `\sub` (decrement).
  2. **No Expression Trees**: The syntax prohibits infix operators and compound arithmetic trees (e.g., `$a + $b * 2`). Complex algorithms belong in host-side middleware extension functions (`\call`), keeping the single-pass compiler minimal and deterministic.
  3. **Native Opcode Parity**: The VM allocates native opcode `0x24 SUB u8 reg, typed(i32 rhs)`, isomorphic to `0x20 SET` and `0x21 ADD` in operand encoding and `runtime.reg_write` register mediation.

- **Assignment & Arithmetic Commands**:
  - `\set{var}{val}`: Assignment (`var` in Name category, `val` in Value category; VM opcode `0x20 SET`).
  - `\add{var}{val}`: Addition with wrapping modulo $2^{32}$ (`var` in Name category, `val` in Value category; VM opcode `0x21 ADD`).
  - `\sub{var}{val}`: Subtraction with wrapping modulo $2^{32}$ (`var` in Name category, `val` in Value category; VM opcode `0x24 SUB`).
- **Special Read-Only Variables**:
  - `$ret`: Return value scalar or Handle from the most recent extension call (`\call`).
  - `$timeout`: Timeout flag from the most recent receptor slot wait (`\wait`) (1 = timed out, 0 = event latched/arrived).
- **Stateful Receptor Slots (Slots)**:
  - **Mandatory Prefix & Syntax**: All receptor slot variables in Value positions **MUST strictly begin with `$slot.`** (canonical form: `$slot.<sig>.<field>`, e.g., `$slot.net.msg.val`, `$slot.img.hp.val`; shorthand `$slot.<sig>`).
  - **Default Signal-to-Slot Projection Rule**: When an external signal `<sig>` is emitted without specific sub-fields (e.g., single scalar or notification), it is uniformly projected into `$slot.<sig>.val` and marks the event as pending. Writing `$slot.<sig>` in macro code automatically desugars to `$slot.<sig>.val`.
  - **Event Latch Model**: Each slot maintains its current scalar `val` (`i32`, initialized to 0) and an unconsumed event arrival flag `pending` (`bool`, initialized to `False`). External signal projections update `val`, set `pending = True`, and wake waiting coroutines. `\wait` consumes the event and resets `pending = False`.
  - **Sole Observable Receptor**: Slot variables are the ONLY entities in the DSL eligible for `\wait` suspension and `\guarded` continuous invariant monitoring.
- **Keystroke Number Formatting**: `\typenum{$var}` formats the integer stored in `$var` as decimal ASCII keystrokes emitted with active pace (operand must be a register reference; Handles are rejected).
- **Compile-Time Constant Color Packing**:
  - `\rgb{r, g, b}`: Packs three 0–255 integer constants in Value positions into a single 24-bit RGB scalar immediate: `(r << 16) | (g << 8) | b`.

---

## 8. Compile-Time Text Macros (`\def`)

#### 0. v3 Baseline Inheritance & Layout Harmonization
- **Fully Inherited Features (`spec/dsl.md §4`)**: `\def{name}[argc]{body}` declarations (argc 0–9), single-pass lazy text parameter substitution (`#1`..`#9`), escape sequence `##`, define-before-use ordering, and recursion/expansion limits (`MAX_MACRO_EXPAND = 16`) remain fully active.
- **v4 Evolutionary Revision**: Inlined character streams explicitly inherit the global preamble layout mode (`\#!syntax`), and argument groups transparently forward compound tuples and predicate structures.

- **Formal Signature**:
  ```dsl
  \def{name}[argc]{ body }
  ```
  - `name`: Name category, macro identifier.
  - `[argc]`: Optional bracket decorator, parameter count (0–9, defaults to 0).
  - `body`: Executable Code Block, expansion template.
- **Scope & Inlining Mechanics**: `\def` must be declared at the top level of the code stream. Calls follow strict define-before-use ordering. Inlining occurs via the compiler's expansion stack (`_nest_stack`) with parameter substitution.
- **Expansion Depth Ceiling**: Macro nesting and recursion are bounded by `MAX_MACRO_EXPAND = 16`.
- **Harmonization with `\#!syntax`**: Macro expansion bodies directly inherit the script's preamble layout configuration. Under default `free` mode, indentation and newlines within `\def` bodies are silently absorbed, and explicit keystroke whitespace is emitted via `\enter`, `\space`, and `\tab`.

---

## 9. Conditional Control & Guarded Execution (`\if`, `\guarded`)

#### 0. v3 Baseline Limitations & Revision Rationale
- **v3 Baseline Design (`spec/dsl.md §5`)**: Provided only the rigid `\ifnum{$a}{op}{b}{then}[{else}]` structure.
- **Defect Analysis**:
  1. *Rigid Structure*: Forced 4/5 separate brace pairs; the left-hand operand was restricted to variable references (`$a`), preventing immediate comparisons (such as `0 < $x`) without manual transposition; and it could not express compound Boolean logic (AND/OR).
  2. *Non-First-Class Predicates*: Comparison triplets were fragmented across separate delimiters, preventing them from being passed as cohesive arguments into `\def` text macros.
  3. *Lack of Continuous Runtime Guarding*: Branching only occurred at discrete, static points in the execution flow. The macro could not monitor external host state (e.g., window focus, unexpected dialogs, player health) during multi-second delays or loops, risking physical key desynchronization.
- **v4 Specification Resolution**:
  1. *Deprecation of `\ifnum`*: Formally deprecates `\ifnum` in favor of the structured `\if{predicate}{then}[{else}]` superset (lowered to `0x22 BRA` with zero new VM branch opcodes).
  2. *First-Class Predicates & Short-Circuit Lowering*: Introduces self-contained comparison triplets and composite `\all`/`\any` trees, lowered via syntax-directed translation into forward branch sequences.
  3. *Continuous State Guarding (`\guarded`)*: Introduces flat guard slots and delay-slice polling to provide hardware-level invariant protection and fail-safe key release (VM opcodes `0x25 GUARD_ENTER` and `0x26 GUARD_EXIT`).

### 9.1 Predicate Grammar (EBNF)
Structures in the Predicate category are formally defined as follows:

```ebnf
Predicate      ::= Triplet | LogicBlock
Triplet        ::= "{" Value "," RelOp "," Value "}"
RelOp          ::= "=" | "!=" | "<" | "<=" | ">" | ">="
LogicBlock     ::= "\all" "{" Predicate ("," Predicate)* "}"
                 | "\any" "{" Predicate ("," Predicate)* "}"
```

- **Atomic Predicate Triplet**: `{ lhs, op, rhs }`, where `lhs` and `rhs` are in the **Value category** and `op` is a relational operator. A triplet is always delimited by its own brace pair, functioning as an indivisible first-class syntactic unit.
- **Composite Logic Trees**:
  - `\all{ P1, P2, ... }`: Conjunction (AND); true if all predicates evaluate to true; short-circuit evaluated.
  - `\any{ P1, P2, ... }`: Disjunction (OR); true if any predicate evaluates to true; short-circuit evaluated.
- **Structural Isomorphism Law**:
  The production rules for single truth tests and composite multi-truth tests are **strictly isomorphic**:
  - The first brace pair of `\if` or `\guarded` is the command's parameter slot: `{ predicate }`.
  - The predicate consumed inside that slot is itself `{ lhs, op, rhs }` or `\all{...}` / `\any{...}`.
  - Therefore, the standard form of a single conditional test is invariably the self-contained dual-brace form: `\if{ {lhs, op, rhs} }{ ... }`.
  - This strict isomorphism allows predicates passed as `#1` into text macros (`\def`) to be forwarded transparently without special-case lexer heuristics.

### 9.2 Conditional Branch Instruction (`\if`)
```dsl
\if{ predicate }{ then_body }[{ else_body }]
```
- **Signature**:
  - `predicate`: Argument Group in the **Predicate category** (atomic triplet `{{lhs, op, rhs}}` or composite `{\all{...}}` / `{\any{...}}`).
  - `then_body`: **Executable Code Block**, executed when the predicate evaluates to true.
  - `else_body` (optional): **Executable Code Block**, executed when the predicate evaluates to false.

#### Examples
```dsl
\# 1. Atomic Predicate Branch (dual-brace structure: outer argument group, inner triplet unit)
\if{ {$x, =, 0} }{
  \enter
}[{
  \tab
}]

\# 2. Composite Logic Branch (disjunction with short-circuit evaluation)
\if{ \any{ {$status, =, 1}, {$err, !=, 0} } }{
  \click{L}
}

\# 3. Predicate Macro Forwarding (passed as a first-class argument)
\def{assert_ready}[1]{
  \if{ #1 }{ \enter }
}
\assert_ready{ {$state, =, 100} }
```

### 9.3 State-Guarded Execution (`\guarded`)
```dsl
\guarded[throttle_ms]{ predicate }{ protected_body }[{ on_abort }]
```
- **Signature**:
  - `[throttle_ms]` (optional bracket decorator): Value category (ms; supports `50ms`, `0.05s`; defaults to `0ms`, meaning continuous checks at every instruction boundary and delay slice).
  - `predicate`: Argument Group in the **Predicate category** (the invariant to maintain; restricted to a single-level triplet or single-level `\all`/`\any` for microcontroller polling efficiency; compound hierarchies are structured via nesting).
  - `protected_body`: **Executable Code Block**, executed under continuous invariant protection.
  - `on_abort` (optional): **Executable Code Block**, executed if the guard invariant is violated.
- **Nesting Ceiling**: Guard nesting is statically verified by the compiler and must not exceed `MAX_GUARD_NEST = 4` (`MacroError: guard nesting exceeds 4 levels`).

#### Examples
```dsl
\# 1. Basic Invariant Guard (continuous monitoring during delays and keystrokes)
\guarded{ {$slot.img.hp.val, >, 0} }{
  \click{L}
  \delay{100ms}
}[{
  \# Guard abort block: Peripherals are safely released by the runtime before entry
  \enter
}]

\# 2. Throttled Guard with Composite Invariant
\guarded[50ms]{ \all{ {$slot.sys.ready.val, =, 1}, {$slot.net.conn.val, !=, 0} } }{
  \rep{10}{
    \move{10, 0}
    \delay{20ms}
  }
}
```

#### Lowering Structure
The compiler emits a flat linear instruction sequence for `\guarded`:
```
GUARD_ENTER abort_off, header, [throttle_ms], [pred_descriptor]
<bytecode for protected_body>
GUARD_EXIT
[OP_JMP skip_abort_off]    # Emitted only if on_abort is present, allowing normal flow to skip it
<bytecode for on_abort>    # abort_off points directly to this offset
end_guarded:
```

### 9.4 Predicate Lowering Algorithm (Short-Circuit Forward Branches)
In single-pass streaming compilation, predicate trees (including nested `\all` and `\any` blocks) lower directly into `OP_BRA` branch sequences via syntax-directed short-circuit translation:

1. **Recursive Short-Circuit Rules**:
   Each predicate node $P$ receives two target labels: `(true_label, false_label)`:
   - **Conjunction Node `\all{ P_1, \dots, P_N }`**:
     - For the first $N-1$ child nodes $P_i$: The true target falls through to the next child $P_{i+1}$, while the false target branches directly to `false_label`.
     - For the final child $P_N$: Inherits the parent's `(true_label, false_label)`.
   - **Disjunction Node `\any{ P_1, \dots, P_N }`**:
     - For the first $N-1$ child nodes $P_i$: The true target branches directly to `true_label` (emitting the inverted operator under Branch-on-False semantics), while the false target falls through to $P_{i+1}$.
     - For the final child $P_N$: Inherits the parent's `(true_label, false_label)`.
2. **Atomic Leaf Emission & Commutative Normalization**:
   At each atomic triplet leaf:
   - **Commutative Swap**: `OP_BRA` requires a register as its left-hand operand. If `lhs` is an immediate and `rhs` is a variable (e.g., `{ 0, <, $x }`), the compiler swaps the operands and inverts the relational operator (`<` becomes `>`), emitting `OP_BRA(cc_swapped, reg_x, imm_0, ...)`.
   - **Pure Register & Immediate Evaluation**: When operands are registers or immediates, lowering requires zero temporary registers and zero runtime stack growth.
   - **Asynchronous Slot Desugaring**: If an operand references a receptor slot (`$slot.<sig>.<field>` or shorthand `$slot.<sig>`), the compiler identifies the `$slot.` prefix, emits `OP_READSLOT temp_reg, sig, field` to load the slot value into an ephemeral register, and performs the comparison against that register.
   - **Constant Folding**: If both operands are immediate constants, the compiler evaluates the boolean result statically, eliminating unreachable branch paths.
3. **Backpatching**: Relative branch offsets are backpatched in-place in the output buffer once target block lengths are resolved.

---

# Part II: VM Core & Bytecode Specification Delta (`spec/vm.md`)

#### 0. v3 Baseline Inheritance & Evolutionary Scope
- **Fully Inherited Features (`spec/vm.md Part I`)**: Linear register machine model, 32 general-purpose `i32` registers (`0x00`..`0x1F`), physical operand tags (`TAG_IMM=0`, `TAG_REG=1`, `TAG_STR=2`), register-write mediation interface (`runtime.reg_write(dst, src)`), and emergency fail-safe peripheral release upon unhandled exception remain 100% active and normative.
- **v4 Evolutionary Revisions**:
  1. **Container Version Increment**: With the introduction of state guarding and native subtraction opcodes, the binary container version advances to `VERSION = 4` (`MAGIC = 0xA5` and shared string pool layout remain unchanged).
  2. **Timing Opcode Invariants Maintained**: Timing opcodes `0x04` (`OP_SLEEP`, `u16 ms`) and `0x07` (`OP_PACE`, `u16 ms`) strictly maintain 2-byte unsigned integer parameters.
  3. **Zero New VM Branch Opcodes**: Conditional branches (`\if`) are lowered entirely to native `0x22 BRA` and `0x23 JMP`. No new branch opcodes are introduced into the VM core.
  4. **Native Atomic Subtraction Opcode**: Introduces `0x24 SUB u8 reg, typed(i32 rhs)`, offering symmetrical subtraction parity with `0x20 SET` and `0x21 ADD`.
  5. **Continuous Invariant Guard Opcodes**: Introduces `0x25 GUARD_ENTER` and `0x26 GUARD_EXIT`, backed by a compact predicate descriptor and static flat guard slots supporting 20ms delay-sliced polling.

## 1. Execution Machine Model & Halting Decidability

The MYCRO VM is a linear bytecode register machine:
- **Forward Control Flow**: The instruction pointer `ip` advances monotonically forward. Branch and jump instructions (`BRA`, `JMP`) strictly utilize positive forward offsets ($\text{target} > \text{pc}$).
- **Halting Decidability**: Iteration is handled exclusively via bounded counting loops (`LOOP` / `LOOP_END`). Backward branches are non-existent, ensuring the entire execution graph is guaranteed to halt deterministically in finite cycles or exit via task cancellation.

---

## 2. Bytecode Container Format (VERSION 4)

```
u8 MAGIC (0xA5)
u8 VERSION (4)
u8 str_count, followed by str_count entries of [ u8 len + UTF-8 string bytes ]  -- Shared String Table
opcode bytecode instruction stream through EOF
```

### Physical Typed Operand Encoding
```
u8 tag:  0 = Immediate (i32 LE)   1 = Register (u8 index)   2 = String (u8 string table index)
```

> [!NOTE]
> **Operand Tag 3-Variant Closure & Preceding READSLOT**:
> The physical operand tags for standard VM opcodes **strictly consist of the above 3 variants (IMM/REG/STR)**. Standard opcodes do NOT introduce a 4th slot tag. When a command consumes `$slot.<sig>.<field>` in a Value position, the frontend compiler emits `0x28 OP_READSLOT` immediately preceding the host instruction to load the slot value into an ephemeral register, allowing the host instruction to consume it as `TAG_REG`.
> The only exception is the internal predicate descriptor of `0x25 GUARD_ENTER`, which defines its own 2-bit tag field (`0: IMM, 1: REG, 2: SLOT`) to support high-frequency re-evaluation during delay slices without repeated opcode decoding (see §3.1).

---

## 3. Opcode Set Delta v4

Inherits all v3 foundational opcodes (0x01–0x07 keystroke/mouse/timing, 0x10–0x15 loops/holds, 0x20–0x23 registers/branches, 0x28–0x2B slots/extensions):

> [!NOTE]
> **Timing Parameter Word-Length Invariant**: Timing opcodes `0x04` (`OP_SLEEP`, `u16 ms`) and `0x07` (`OP_PACE`, `u16 ms`) **strictly retain 2-byte unsigned little-endian integer operands (`u16`, range 0..65535 ms)**. They are intentionally NOT expanded to `u32` or `i32`. Folded time literals in macro source code must fall within this physical range.

| Opcode | Mnemonic | Operands | Operational Semantics |
|---|---|---|---|
| `0x22` | `BRA` | `u8 cc, u8 reg, typed(i32 rhs), u16 off` | If condition evaluates to **FALSE**, branch forward `ip += off`; `cc`: 0`=`, 1`!=`, 2`<`, 3`<=`, 4`>`, 5`>=` (v3 baseline). |
| `0x23` | `JMP` | `u16 off` | Deterministic forward jump: `ip += off` (v3 baseline). |
| `0x24` | `SUB` | `u8 reg, typed(i32 rhs)` | Atomic register subtraction: `reg = (reg - rhs) & 0xFFFFFFFF`, mediated by `runtime.reg_write` (v4 delta). |
| `0x25` | `GUARD_ENTER` | `u16 abort_off, u8 header, [u16 throttle_ms], [pred_descriptor]` | Pushes active guard into static flat slot, activating continuous invariant monitoring (v4 delta). |
| `0x26` | `GUARD_EXIT` | None | Pops current guard slot, deactivating invariant monitoring (v4 delta). |
| `0x29` | `WAIT` | `u8 sig_str_idx, u8 field_str_idx, typed(u32 timeout_ms)` | Suspends macro coroutine until receptor slot `$slot.<sig>.<field>` receives an external projection event (if `pending == True` latched, consumes and returns immediately; otherwise waits for next signal) or times out; writes status to `$timeout` (v4 revised). |

### 3.1 Compact Binary Specification for Predicate Descriptors (`pred_descriptor`)
`GUARD_ENTER` encodes invariants using a packed bitfield header and contiguous condition triplets, eliminating redundant length prefixes and idle throttling overhead:

#### Instruction Header Encoding
```
u16 abort_off: Relative forward jump offset on guard failure (base point: byte immediately following this GUARD_ENTER, pointing to on_abort or end_guarded)
u8 header: Control bitfield packing
   bit 7:    logic_mode (0 = ALL conjunction, 1 = ANY disjunction)
   bit 4..6: triplet_cnt - 1 (3 bits, encoding 0..7 representing 1..8 triplets)
   bit 0:    has_throttle (0 = default 0ms unthrottled, no trailing field; 1 = explicit throttle, followed by u16 throttle_ms)
   bit 1..3: Reserved (must be 0)
[u16 throttle_ms]: Present only if has_throttle == 1 (2 bytes LE, minimum evaluation interval)
```

#### Condition Triplet Encoding (`triplet_cnt` contiguous entries)
Each triplet consists of a 1-byte specification header followed by variable-length operand payloads:
```
u8 spec: Triplet specification header packing
   bit 0..2: cc relational operator (3 bits: 0: =, 1: !=, 2: <, 3: <=, 4: >, 5: >=)
   bit 3..4: lhs_tag (2 bits: 0: IMM, 1: REG, 2: SLOT)
   bit 5..6: rhs_tag (2 bits: 0: IMM, 1: REG, 2: SLOT)
   bit 7:    Reserved (must be 0)
lhs_payload: Operand entity determined by lhs_tag
rhs_payload: Operand entity determined by rhs_tag
```

Operand payload byte layouts:
- `0 = IMM`: 4-byte signed scalar immediate (`i32 LE`).
- `1 = REG`: 1-byte register index (`u8 reg`).
- `2 = SLOT`: 2-byte string table indices (`u8 sig_str_idx, u8 field_str_idx`).

#### Self-Describing Streaming Properties
- **Zero Redundant Length Headers**: Because `triplet_cnt` and all operand payload lengths are deterministically governed by `spec` tags, the VM deserializes the exact number of triplets sequentially, advancing `ip` directly to the first byte of `<protected_body>` without requiring an outer `pred_len` field.
- **Zero Runtime Deserialization Overhead**: When entering `GUARD_ENTER`, the VM deserializes the descriptor once into a fixed-size in-memory slot tuple `(throttle_ms, last_check_ms, abort_ip, loop_depth, parsed_pred)`. The high-frequency slice re-evaluation loop inspects this pre-parsed structure directly, eliminating Python bytecode unpacking overhead.

---

## 4. Predicate Lowering & Preservation of `OP_BRA` Offset Semantics

`\if` statements lower directly into standard `OP_BRA` and `OP_JMP` instruction sequences.

### 4.1 `OP_BRA` Native Semantics
The execution semantics of `OP_BRA` are strictly: **Branch forward if condition evaluates to FALSE (Branch on False)**.

### 4.2 Syntax-Directed Short-Circuit Lowering Rules
For composite predicate trees (including arbitrary nestings of `\all` and `\any`), the single-pass compiler recursively emits `OP_BRA` jumps using label passing:

1. **Conjunction Node (`\all`)**:  
   False branches of each sub-predicate $P_i$ converge on the parent's `false_target`. The first $N-1$ successful sub-tests fall through to $P_{i+1}$, and the final child falls through to the parent's `true_target` (short-circuit on false).
2. **Disjunction Node (`\any`)**:  
   Under `OP_BRA`'s Branch-on-False semantics, the compiler maps the first $N-1$ sub-predicates to their **Logical Dual Operators (Inverse `cc`)**, emitting `OP_BRA(invert(cc), ..., then_target)`. Any sub-predicate evaluating to true immediately jumps to the true body. The final child emits its native operator `OP_BRA(cc, ..., false_target)` (branching to false if all tests fail).
3. **Dual Operator Inversion Mapping**:
   - `=` (0) $\leftrightarrow$ `!=` (1)
   - `<` (2) $\leftrightarrow$ `>=` (5)
   - `<=` (3) $\leftrightarrow$ `>` (4)

Lowering requires zero temporary registers and zero runtime stack allocations; all relative offsets are resolved in-place via backpatching.

---

## 5. Flat Iterative Guard Model

### 5.1 Static Flat Slot Management
The VM manages guard execution using a statically pre-allocated flat array and an active depth cursor:
```python
MAX_GUARD_NEST = 4  # Static compile-time nesting ceiling

# VM pre-allocates flat array at launch, maintaining constant memory layout
guard_slots = [None] * MAX_GUARD_NEST  # Entries: (throttle_ms, last_check_ms, abort_ip, loop_depth, pred_desc)
guard_depth = 0  # Active guard cursor (0..4)
```

- **`GUARD_ENTER`**: Increments `guard_depth += 1` and stores descriptor parameters into `guard_slots[guard_depth - 1]`.
- **`GUARD_EXIT`**: Decrements `guard_depth -= 1`, unwinding the cursor in $O(1)$ constant time.

### 5.2 Dual-Trigger Inspection Scheduling
When evaluating active guards, the VM iterates **from outer to inner** (indices `0` through `guard_depth - 1`). If any guard layer $i$ is violated, the VM triggers an immediate force-abort, ensuring outer macro invariants maintain highest priority.
1. **Instruction Boundary Throttling**: At the head of the main dispatch loop:
   - If `throttle_ms == 0` (default): Unthrottled; checks all active guards after every completed macro instruction.
   - If `throttle_ms > 0`: Checks guards when `monotonic_ms - last_check_ms >= throttle_ms`, updating `last_check_ms`.
2. **Delay Slicing**: When suspending execution during `\delay` (`OP_SLEEP`) or inter-keystroke pacing (`OP_PACE`), if guards are active (`guard_depth > 0`), the VM slices the total sleep duration into micro-quanta:
   $$\Delta t = \min(t_{\text{remaining}}, \Delta t_{\text{slice}})$$
   where the slice quantum $\Delta t_{\text{slice}}$ is the minimum effective period among all active guards:
   $$\Delta t_{\text{slice}} = \min_{0 \le j < \text{guard\_depth}} (t_{\text{throttle}, j} > 0 \ ? \ t_{\text{throttle}, j} : \text{DELAY\_SLICE\_QUANTUM})$$
   (with base quantum `DELAY_SLICE_QUANTUM = 20ms`). The VM evaluates active guards upon each slice wake-up; any violation immediately initiates an emergency abort.

### 5.3 Force-Abort & Nested Scope Isolation
When a guard at level $i$ evaluates to false, the VM executes atomic unwinding:
1. **Hardware Peripheral Reset (Emergency Fail-Safe)**: Calls `hid.keys_release_all()` and `hid.mouse_release(BTN_ALL)` (`BTN_ALL = 0x1F`), releasing all physically depressed keys and buttons to prevent hardware hanging. If the outer macro relies on held keys, they must be explicitly re-asserted in `on_abort` or scoped within the guard.
2. **Loop Frame Rollback**: `loop_stack = loop_stack[:guard_slots[i].loop_depth]`, cleanly rolling back loop frames belonging to the aborted scope while preserving outer loop contexts.
3. **Register Preservation**: Current values in general-purpose registers remain intact.
4. **Targeted Jump & Depth Rollback**: Sets `ip = guard_slots[i].abort_ip` and `guard_depth = i`:
   - **Scope Isolation**: Unwinds `guard_depth` back to $i$, popping the failed guard and any inner guards while leaving outer guards (`0 .. i-1`) active.
   - **Temporal Invariant Bounding**: Guards are only evaluated while execution is dynamically within their `GUARD_ENTER`..`GUARD_EXIT` bounds. Once aborted or exited, the guard is popped and will not be re-evaluated.
   - **Outer Execution Resumption**: After jumping to `on_abort` (or `end_guarded`), execution continues under the protection of surviving outer guards.

### 5.4 Normal Scope Exit
When `protected_body` completes normally, `GUARD_EXIT` pops the guard slot and skips past `on_abort`. Peripheral states remain under explicit script control.

---

# Part III: Runtime, Peripherals & Standard Library Delta (`spec/runtime.md`)

#### 0. v3 Baseline Inheritance & Evolutionary Scope
- **Fully Inherited Features (`spec/vm.md Part II`)**: Namespace registration system, cooperative single-threaded event loop (atomic slot read/write exclusivity), `HIDProvider` hardware abstraction layer (`hid_adafruit` / `mock_hid`), Handle resource ownership (overwrite reclamation, `\set` moves, macro exit `release_all`), and `sys`/`net` namespaces remain 100% active and normative.
- **v4 Evolutionary Revisions**: Introduces push-model telemetry streaming via `\call{img.watch}` alongside existing pull-based `img.snap`/`at`/`diff`. The capture provider continuously streams samples into `$slot.img.<name>.val`, eliminating network round-trip bottlenecks.

---

## 1. Runtime Architecture & Signal / Slot Reset

### 1.1 Namespaces & System Resource Model
`runtime.py` governs four dimensions of system resources:
- **Namespaces**: Functional and hardware boundaries (`sys`, `net`, `img`).
- **Function Signatures**: Input argument schemas (Value types vs. Handle types), return value contracts, and error code conventions.
- **Signals**: Pure Name-position projection channels (e.g., `net.msg`, `img.frame`).
- **Slots**: Pure Value-position state receptors, mandatory-prefixed with `$slot.` (e.g., `$slot.net.msg.val`, `$slot.img.hp.val`), holding scalar payloads and latched arrival flags.

### 1.2 Signal / Slot Paradigm Reset & Receptor Invariants

#### 0. v3 Baseline Defects & Semantic Inversion
- **v3 Baseline Design (`spec/dsl.md §1, §6` & `runtime.py:273`)**: v3 bound `\wait{sig}` to the bare Signal name, treating signals like boolean `asyncio.Event` objects. Slots were treated as passive read-only dictionaries (`$sig.field`), lacking a unifying syntactic prefix.
- **Severe Engineering Vulnerabilities**:
  1. *Transient Event Loss & Deadlock (`runtime.py:273`)*: In v3, `wait_signal` unconditionally executed `ev.clear()` upon entry. If an external signal arrived microseconds before `\wait` executed, the event was silently discarded, causing `\wait` to block indefinitely in an unrecoverable deadlock.
  2. *Conceptual Inversion*: Signals are external emission channels that cannot hold persistent state; slots held data but could not be waited on.

#### 1. v4 Signal / Slot Orthogonal Paradigm Axioms
v4 completely resets the semantics and mechanisms of Signals and Slots, establishing two orthogonal invariants:

1. **Signals are Strictly Name-Position Projection Channels (Projectors)**:
   - **Syntactic Form**: Whenever Signal semantics appear, **they MUST be pure static identifiers (Name category, never prefixed with `$`**).
   - **Semantic Role**: Represents an **External Projection Channel**. Triggered exclusively by the outside world (host Protocol v1, OBS capture providers, network push). The emitter projects data into a named channel regardless of the macro's internal execution state.
   - **Statelessness**: Signals maintain no persistent state storage. They cannot be read, cannot participate in arithmetic, and cannot be waited on directly.
2. **Slots are Strictly Value-Position Stateful Receptors**:
   - **Syntactic Form**: Whenever Slot semantics appear, **they MUST be value-position variables mandatory-prefixed with `$slot.`** (canonical form: `$slot.<sig>.<field>`, shorthand: `$slot.<sig>`).
   - **Semantic Role**: Represents a **Stateful Receptor**. Responsible for receiving and latching external signal payloads and arrival status.
   - **Default Signal-to-Slot Projection Rule**:
     - When the outside world emits to signal `<sig>` without specific sub-fields (e.g., a single scalar integer, a boolean notification, or an unnamed payload), the **default projection field is uniformly mapped to `val`**, atomically written to `$slot.<sig>.val` with its arrival flag latched.
     - If the emission carries a named key-value dictionary (`{k: v, ...}`), the payload is projected into corresponding `$slot.<sig>.<k>` fields.
     - Writing `$slot.<sig>` in macro code (omitting the trailing field dot) is automatically desugared by the streaming compiler to `$slot.<sig>.val`.
     - Multi-level namespace splitting: Beyond the `$slot.` prefix, the remainder is split at the last dot into the signal channel and field identifier (e.g., `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`).
   - **Event Latch Storage Model**:
     Each slot maintains two physical attributes without revision counters:
     - `val`: Latest scalar payload (`i32`, initialized to 0).
     - `pending`: Unconsumed event arrival flag (`bool`, initialized to `False`). External signal projections atomically set `pending = True` and wake waiting coroutines.
3. **The Sole Observable Receptor Law**:
   - **Receptor slots starting with `$slot.` are the ONLY valid asynchronous entities eligible for waiting (`\wait`) and guarding (`\guarded`)**.
   - Local general-purpose registers (`$var`) represent private synchronous state inside the single-threaded execution context. Attempting to `\wait` on `$var` is statically rejected.
   - **Lossless Event Latch Waiting**: Executing `\wait{ $slot.<sig>.<field> }[timeout]`:
     - If `slot.pending == True` (a prior projection arrived unconsumed): Consumes the event immediately, resets `slot.pending = False`, and returns with zero delay, completely eliminating the v3 race-condition event loss.
     - If `slot.pending == False` (no pending event): Suspends the coroutine until the next projection sets `pending = True` (or until timeout); upon wake-up, consumes the flag and resumes execution.

#### 2. Architectural Decision: Pure Data Slots & Unidirectional Async Flow
- **Data Slot Paradigm**: Slots serve exclusively as passive data containers and event latches. They contain no reverse control flow or re-entrant callbacks, preserving the single-pass compiler's halting decidability and flat stack execution.
- **Unidirectional Async Flow**: The macro execution model is strictly single-threaded and sequential, without concurrent macro execution blocks. The architecture maintains a strictly unidirectional flow:
  $$\text{External Signal Projection} \longrightarrow \text{Slot Event Latch} \longrightarrow \text{Internal Wait / Guard Inspection}$$

### 1.3 Cooperative Concurrency & Slot Consistency
- CircuitPython / MicroPython operate on a cooperative single-threaded event loop.
- External network packets (Protocol v1 `event.push`) and VM execution share the main thread dispatcher.
- The VM yields execution via `await asyncio.sleep` during `\delay` micro-slices or `\wait` suspensions. During computational bursts and predicate evaluations, the VM executes uninterrupted.
- **Consistency Guarantee**: Single-threaded dispatch guarantees mutual exclusion for slot reads and writes, providing deterministic memory consistency without mutex locks.

---

## 2. HIDProvider Peripheral Abstraction Layer

The VM core and compiler remain fully detached from underlying physical hardware. All peripheral interactions are funneled through the `HIDProvider` interface:
- `key_press(keycode)` / `key_release(keycode)` / `keys_release_all()`
- `mouse_press(btn)` / `mouse_release(btn)` / `mouse_move(dx, dy)`
- `char_keycodes(char)` (resolves character to physical keycode and Shift state)

Platform implementations:
- CircuitPython: `hid_adafruit.py` (backed by `adafruit_hid`).
- Unit Testing: `mock_hid.py`.
- MicroPython (Reserved Architecture): Backed by `machine.USBDevice`.

---

## 3. Standard Library Namespaces

### 3.1 `sys` Namespace (System Control)
- **`sys.free`**: Zero-argument call; returns current free heap memory in bytes (`i32`).
- **`sys.const`**: Internal runtime intrinsic; retrieves read-only Handles for compile-time constants (e.g., `\rect`).

### 3.2 `net` Namespace (Network Communication)
- **`net.send{str_tag}`**: Broadcasts a `net.msg` frame to all authenticated Protocol v1 clients; returns count of recipients.
- **`net.msg` Signal**: Triggered when a host client sends an `event.push` frame, projecting into `$slot.net.msg.*` and setting `pending = True`. Macros synchronize via `\wait{ $slot.net.msg.val }` (or shorthand `\wait{ $slot.net.msg }`).

### 3.3 `img` Namespace (Vision & Telemetry)

#### 1. Exact Pixel Matching Family (v3 Baseline Inherited)
- **`\call{img.snap}{rect}`**: Captures screen region (`rect` compound tuple `{x, y, w, h}` or constant Handle); returns non-negative snap Handle.
- **`\call{img.at}{snap_handle}{point}`**: Samples packed 24-bit RGB color at coordinates (`point` compound tuple `{x, y}`).
- **`\call{img.diff}{snap_a}{snap_b}{tol}`**: Compares two snapshots; returns count of pixels exceeding color tolerance `tol`.

#### 2. Push-Model Telemetry Subscriptions (`img.watch`, v4 Delta)
Initiates continuous push telemetry from the capture provider:
```dsl
\call{img.watch}{name}{coord}{interval_ms, duration_ms}
```
- **`name`**: Stream identifier; registers and binds external signal `img.<name>` (e.g., `img.hp`).
- **`coord`**: Sampling point `{x, y}` or bounding rect `{x, y, w, h}`.
- **`interval_ms`**: Sampling period on the host (ms; accepts `50`, `50ms`, `0.05s`).
- **`duration_ms`**: Mandatory life lease duration (ms; accepts `10000`, `10000ms`, `10s`).

**Automatic Lease Self-Destruction**: Subscriptions are bound to an explicit `duration_ms` lease. The host capture provider counts down the lease and automatically terminates the stream upon expiration, preventing orphaned background capture leaks.

#### 3. Slot Addressing & Signal Temporal Coordination
In strict conformance with `spec/dsl.md §3` slot addressing:
- **Slot Storage**: Telemetry frames arriving from the host project into signal `img.<name>`, writing the sampled scalar (color, difference count, or score) into `$slot.img.<name>.val` and setting `pending = True`.
- **Zero-Copy Reading**: Macro code reads the latest sample via `$slot.img.<name>.val` (or shorthand `$slot.img.<name>`), initialized to 0.
- **Frame Synchronization**: Initial frame synchronization or change notifications are awaited via `\wait{ $slot.img.<name>.val }` (suspending execution until the next projection arrives).

---

## 4. Handle Lifecycle & Ownership Management

1. **Resource Type Isolation**: Heavy objects (such as snapshot framebuffers) reside in the runtime's internal object store; macros interact strictly through opaque `i32` Handles.
2. **Register-Write Mediation**: Every register write is mediated by `runtime.reg_write(dst, src)`:
   - When a register holding a Handle is overwritten or assigned, the old Handle is automatically unregistered and freed.
   - Variable assignment (`\set{b}{$a}`) transfers resource ownership (Move semantics).
3. **Deterministic Cleanup**: Upon macro completion, abort, or unhandled exception, `runtime.release_all()` executes unconditionally in a `finally` block, reclaiming all active Handles.

---

# Appendices

## Appendix A: Specification Constants Table

| Constant Name | Normative Value | Scope / Specification Partition | Operational Semantics |
|---|---|---|---|
| `MAX_REGS` | 32 | Part I / II (`spec/dsl.md`, `spec/vm.md`) | General-purpose scalar `i32` register profile (address space ceiling 256; v3 inherited). |
| `MAX_RUNTIME_NEST` | 8 | Part I / II (`spec/dsl.md`, `spec/vm.md`) | Maximum runtime nesting depth for `\rep` loops and `\if` blocks (v3 inherited). |
| `MAX_GUARD_NEST` | 4 | Part I / II (`spec/dsl.md`, `spec/vm.md`) | Static flat array capacity for `\guarded` blocks (v4 delta). |
| `MAX_MACRO_EXPAND` | 16 | Part I (`spec/dsl.md`) | Maximum compile-time recursion and expansion depth for `\def` (v3 inherited). |
| `MAX_STRINGS` | 255 | Part II (`spec/vm.md`) | Maximum capacity of the shared string pool in bytecode containers (v3 inherited). |
| `DELAY_SLICE_QUANTUM` | 20ms | Part II (`spec/vm.md`) | Base time quantum for delay-sliced guard polling loops (v4 delta). |

---

## Appendix B: v3 to v4 Migration & Delta Matrix

| Language Feature | v3 Baseline Syntax / Design | v4 Normative Evolution | Migration & Backwards Compatibility Notes |
|---|---|---|---|
| **Document Layout** | Newlines emit `Enter`; spaces emit `Space` (`spec/dsl.md §1`). | `free` layout is default; legacy mode activated via `\#!syntax: typewriter`. | Blank lines and indentation are silently absorbed in v4 scripts. Legacy scripts run identically by declaring `\#!syntax: typewriter`. |
| **Delay Timing** | `\delay{0.1}` (floating-point seconds; silent clipping in v3 implementation). | `\delay{100ms}` or `\delay{0.1s}` | Folded in-place into integer millisecond immediates, eliminating MCU floating-point overhead and jitter. |
| **Keystroke Pacing** | `\pace{0.05}` (floating-point seconds). | `\pace{50ms}` or `\pace{0.05s}` | Aligned with `\delay`, folding into integer milliseconds. |
| **Long Delays** | Implicit large literals `\delay{100}` (seconds; clipped to 65535 in v3 reference). | `\rep{100}{ \delay{1s} }` | Single delay parameters are strictly constrained to `u16 ms` ($0..65535$), raising compile errors on overflow. |
| **Event Waiting** | `\wait{sig}[5]` (bare signal name, floating-point seconds; transient event loss hazard). | `\wait{ $slot.<sig>.val }[5s]` or shorthand `\wait{ $slot.<sig> }[5s]` | Signals are pure projection channels; waiting consumes stateful `$slot.` variables backed by lossless Event Latches. |
| **Conditional Branching** | `\ifnum{$a}{=}{10}{then}[{else}]` (5 separate brace pairs). | `\if{ {$a, =, 10} }{then}[{else}]` | Deprecates `\ifnum`; introduces structured, first-class dual-brace triplets lowered to `0x22 BRA` with zero new VM branch opcodes. |
| **Compound Predicates** | Not supported (required nested `\ifnum` blocks). | `\if{ \all{ {$a, >, 0}, {$b, <, 10} } }{...}` | Lowered via syntax-directed translation into short-circuit forward branch sequences with zero AST and zero temp registers. |
| **Predicate Macros** | Not supported (predicates were not first-class units). | `\def{assert}[1]{\if{#1}{\enter}}` | Predicate triplets `{lhs, op, rhs}` form uniform syntactic units, forwarded transparently into macro expansions. |
| **Continuous Invariant Guarding** | Not supported (only static discrete branches). | `\guarded[20ms]{pred}{body}[{on_abort}]` | New VM opcodes `0x25 GUARD_ENTER` / `0x26 GUARD_EXIT` with 20ms delay-slicing and fail-safe key release. |
| **Visual Telemetry** | Pull-only `\call{img.snap}{rect}`. | `\call{img.watch}{name}{coord}{intv, dur}` | Push-model telemetry streaming directly to `$slot.img.<name>.val`, synchronized via `\wait{ $slot.img.<name> }`. |
| **Subtraction & Decrement** | Not supported natively (required adding negative immediates; register subtraction impossible). | `\sub{cnt}{val}` | Introduces symmetrical atomic decrement `\sub{var}{val}` backed by native VM opcode `0x24 SUB`, preserving the zero-expression-tree axiom. |
| **Brace Taxonomy** | Mixed usage of `{x, y}` and `{x}{y}`. | Strict exclusivity: Separate `{A}{B}` vs Compound `{A, B}`. | Statically enforced by command signatures; structural mismatches trigger compile-time argument diagnostics. |
