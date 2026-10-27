# MYCRO DSL Specification

**Status: normative.** This document specifies the macro DSL's surface syntax and semantics for **DSL v4**. The reference implementation is the compiler in `src/lib/macro.py`; the bytecode it emits and execution semantics are specified in [vm.md](vm.md); the runtime environment, peripheral abstractions, and extension namespaces are specified in [runtime.md](runtime.md).

### Table of Contents
- [§1. Core Design Axioms & System Invariants](#1-core-design-axioms--system-invariants)
- [§2. Lexical Schema & Primitive Tokens](#2-lexical-schema--primitive-tokens)
- [§3. Preamble Directives & Document Layout Modes (`\#!syntax`)](#3-preamble-directives--document-layout-modes-syntax)
- [§4. Document Grammar & The Binary Brace Taxonomy](#4-document-grammar--the-binary-brace-taxonomy)
- [§5. Base Macro Instruction System](#5-base-macro-instruction-system)
- [§6. Values, Variables & State Model](#6-values-variables--state-model)
- [§7. Compile-Time Text Macros (`\def`)](#7-compile-time-text-macros-def)
- [§8. Conditional Control & Guarded Execution (`\if`, `\guarded`)](#8-conditional-control--guarded-execution-if-guarded)
- [§9. Signals, Slots & Runtime Extensions](#9-signals-slots--runtime-extensions)
- [§10. Boundaries and Version History](#10-boundaries-and-version-history)

---

## 1. Core Design Axioms & System Invariants

The normative semantics and compilation model of MYCRO DSL v4 are governed by four immutable system axioms:

### 1.1 Axiom I: Zero-AST Single-Pass Streaming
The compiler is a single-pass streaming finite state machine with zero Abstract Syntax Tree (AST) allocation:
- **Zero Syntax Tree Overhead**: The compiler neither constructs nor retains heap-allocated AST nodes. The lexical scanner traverses the input ASCII character stream and directly emits binary bytecode into the output buffer in a single traversal.
- **Unidirectional Non-Backtracking Consumption**: The character stream is consumed monotonically forward. Except for the bounded macro inline expansion stack (`_nest_stack`), there is no global lookahead buffer.
- **$O(1)$ Dynamic Working Memory**: The working memory of the compiler (excluding the pre-allocated output bytecode buffer and string table arena) is strictly bounded $O(1)$, independent of source script length. The output bytecode buffer itself is bounded by `MAX_BYTECODE = 65535` bytes.

### 1.2 Axiom II: Atomic Arithmetic & Structured Predication
The surface language is composed exclusively of explicit instructions and flat decision structures:
- Numerical state mutations are expressed strictly through atomic macro instructions (`\set`, `\add`, `\sub`). Infix operators and binary expression trees (e.g., `$x + $y * 2`) are prohibited.
- Logical decisions are structured as flat predicate triplets combined via short-circuit blocks, lowering directly into forward branch bytecode.

### 1.3 Axiom III: Strict Termination & Halting Decidability
The Control Flow Graph (CFG) is strictly constrained to forward DAGs and bounded counting loops:
- Conditional branches (`OP_BRA`) and unconditional jumps (`OP_JMP`) are strictly forward-progressing ($\text{target} > \text{pc}$).
- Loops are exclusively instantiated through bounded counter iterations (`\rep{N}`). General backward branches (`while`, `goto`) do not exist.
- Execution is statically guaranteed to halt in a finite number of cycles, time out deterministically (as all wait operations enforce an upper bound $\le 2147483647\text{ ms}$), terminate via external task cancellation (`POST /macro/abort`), or terminate via guard failure.

### 1.4 Axiom IV: Bounded Resources & Deterministic State
All runtime execution horizons and resource allocations conform strictly to the Authoritative Profile Constants Table in [vm.md §1.0](vm.md#10-authoritative-profile-constants-table):
- Maximum logical block nesting depth: `MAX_RUNTIME_NEST = 8` — applies uniformly to **every** nested logical block (`\rep`, `\if`, `\guarded`); e.g. `\rep{N}{ \if{...}{ \guarded{...}{ ... } } }` nests 3 blocks. Verified statically at compile time; exceeding it triggers a compile error.
- Maximum compile-time macro expansion depth: `MAX_MACRO_EXPAND = 16`.
- Maximum concurrent guard nesting depth: `MAX_GUARD_NEST = 4`.
- Maximum predicate logic-block nesting depth: `MAX_PRED_NEST = 4` (spam cap for `\all`/`\any` nesting; see [§8.1](#81-predicate-grammar-ebnf)).
- Variable capacity profile: Up to 32 distinct user variables per macro (mapped to `MAX_VARS = 32` / `MAX_REGS = 32` scalar `i32` registers; see [vm.md §2](vm.md#2-register-file)).
- Maximum `\pixelrect` edge length: `RECT_MAX_EDGE = 128` pixels ($128^2 \times 3 = 48\text{ KiB}$ matching `SNAP_SLOT_BYTES`).
- **Deterministic Key State**: Upon normal completion of a code block, physical peripheral state is governed entirely by emitted instructions. Explicitly depressed keys remain physically held until an explicit release instruction (`\kup`, `\mup`) or an emergency fail-safe release (abnormal termination: task abort, uncaught exception, or guard failure).
- **Abnormal Termination Fail-Safe**: Abnormal termination (uncaught exceptions, task cancellation, or guard failure) unconditionally executes `hid.keys_release_all()` and `hid.mouse_release(0x1F)`.
- **Deterministic Resource Reclamation**: Managed Handle resources (such as capture buffers) are deterministically reclaimed upon macro exit via `runtime.release_handles()` (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

---

## 2. Lexical Schema & Primitive Tokens

The MYCRO DSL character stream is partitioned into seven closed, deterministic lexical primitive schemas:

1. **Macro Commands (`\cmd`)**: Backslash `\` followed by an identifier matching `[a-zA-Z0-9_]+` (e.g., `\delay`, `\rep`, `\enter`, `\ctrl+c`, user-defined macro `\foo`). The identifier itself never contains `+`; a `+` immediately following the identifier continues the token as a key combination per the §5.1 `KeyCombo` grammar. Parsed by the compiler to emit corresponding bytecode instructions or trigger compile-time macro expansion.
2. **Unified Variable Reference Sigils (`Ref`)**: Dollar sign `$` followed by an identifier reference (`$var`, `$ret`, `$timeout`, `$err`, `$slot.<sig>.<field>`) or an explicit delimited enclosure (`${var}`, `${ret}`, `${timeout}`, `${err}`, `${slot.<sig>.<field>}`). Denotes dynamic value evaluation:
   ```ebnf
   Ref          ::= BareRef | DelimitedRef
   BareRef      ::= "$" RefBody
   DelimitedRef ::= "${" RefBody "}"
   RefBody      ::= VarName | SysVarName | SlotPath
   VarName      ::= [a-zA-Z] [a-zA-Z0-9_]*
   SysVarName   ::= "ret" | "timeout" | "err"
   SlotPath     ::= "slot." SignalName "." FieldName
   ```
   An empty delimited sequence `${}` or `${   }` raises an immediate static compile error (`Empty reference`). User variables cannot use reserved system names (`ret`, `timeout`, `err`, `slot`).
3. **Numeric Literals**: Decimal integers (`120`, `-15`), hexadecimal constants (`0xFF`, `0xFF00FF`), and dimensioned time literals (`500ms`, `0.5s`). Static numeric values folded in-place during lexing into unified `i32` integer scalars (see [§2.1](#21-numeric-representation--integer-folding)).
4. **Brace Pairs (`{}`)**: Balanced enclosing pairs `{ ... }`. Syntactic scope delimiters, classified as either an Executable Code Block or an Argument Group based on host command signature (see [§4](#4-document-grammar--the-binary-brace-taxonomy)).
5. **Bracket Pairs (`[]`)**: Balanced enclosing pairs `[ ... ]`. Optional configuration modifiers or decorators (e.g., `\def{name}[argc]{body}`, `\guarded[throttle]{...}`, `\wait[timeout]{...}`).
6. **Literal Keystroke Text**: Printable ASCII characters (codes 32–126) located in executable text positions. Emitted as physical keystrokes targeting the host operating system. In addition, Tab (code 9), LF (code 10), and CR (code 13) are valid text characters handled according to layout mode (see [§3.3](#33-lexer-scanning-state-machine)).
   - Bare `{` `}` `[` `]` in text positions are literal keystrokes; braces and brackets are structural only where a command's signature consumes them.
   - **Escape Sequences**: `\` followed by one of `\ { } $` emits the literal second character. `\` followed by a newline (LF or CRLF) is a line continuation (silently absorbed, §3.3). `\` followed by an ASCII letter starts a command token. `\` followed by any other character (or end of input) is a compile error (`Invalid escape sequence`).
   - `#` outside a `\def` body is a literal keystroke (parameter substitution exists only inside macro bodies, §7).
7. **Comments and Shebang Directives (`\#`, `\#!`)**:
   - Standard comments begin with `\#` and span to the end of the line, **including the terminating newline itself**; the entire span is silently absorbed by the lexical scanner (a comment line emits no Enter keystroke under any layout mode).
   - Compiler configuration directives follow Unix shebang conventions, declared via `\#!syntax: <preset>` in leading comment lines of the script (see [§3](#3-preamble-directives--document-layout-modes-syntax)).

- **Case Sensitivity Principle**:
  MYCRO is strictly a **case-sensitive language** across all tokens, identifiers, and keystrokes. Single-letter key commands (`\a` for lowercase 'a' vs. `\A` for Shift+'a'), command names, user macro names (`\def`), variable identifiers (`$foo` vs. `$Foo`), and signal/field names are case-distinct.

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
| **Millisecond Primitive** | `500ms`, `20ms` | $f(\text{digits}) = \text{int}_{10}(\text{digits})$ | `500`, `20` | Delays, pacing intervals, variable values |
| **Second Time Dimension** | `0.5s`, `1.2s`, `5s` | Exact decimal integer folding (no binary floating point): with the literal's digits as integer $N$ and $k$ fraction digits, $f(N, k) = \lfloor (2000N + 10^k) \,/\, (2 \cdot 10^k) \rfloor$ (round-half-up) | `500`, `1200`, `5000` | Delays, guard throttling, timeout values |

#### Folding Invariants & Boundary Constraints
1. **Unified Integer Domain & Overflow Checking**: Numeric literals and folded constants evaluate to mathematical integers. The evaluated scalar must fall strictly within the signed 32-bit range $[-2^{31}, 2^{31} - 1]$. Values outside this range constitute an arithmetic overflow and trigger an immediate static compilation error.
2. **Mandatory Suffix for Fractional Quantities**: Any literal containing a decimal point MUST explicitly carry the `s` dimensional suffix (e.g., `0.5s`). Fractional literals without `s` (e.g., `0.5`) trigger an immediate static compile error (`fractional number requires unit 's'`).
3. **Non-Negative Duration Enforcement**: Duration literals in time positions (`\delay`, `\pace`, `\guarded`) cannot be negative; negative time literals (e.g., `-50ms`) trigger an immediate static compile error (`negative duration not allowed`). (`\wait` is the deliberate exception: negative timeouts are accepted and mean immediate poll; see [§9.2](#92-asynchronous-event-synchronization-wait).)
4. **Rounding Semantics**: Sub-millisecond fractions are rounded using half-up rounding (e.g., `0.0004s` folds to `0ms`, `0.0006s` folds to `1ms`).
5. **Physical Range Assertions (Decoupled from Lexing)**: The lexical scanner unifies all numeric and time literals into signed `i32` mathematical scalars. The validation of whether an immediate value satisfies the physical width of its target VM opcode is decoupled from lexing and enforced as **Physical Range Assertions**, normatively specified in **[§6.7 (Physical Word-Length Contracts)](#67-physical-word-length-contracts)** and each command's dedicated section. Folded values exceeding target opcode bounds (e.g., `\delay{70s}` exceeding `u16`, `\rep{300}` exceeding `u8`) trigger immediate static compilation errors.

---

## 3. Preamble Directives & Document Layout Modes (`\#!syntax`)

Preamble directives configure the compiler's lexical scanner behavior prior to code execution.

### 3.1 Formal Grammar (EBNF)

```ebnf
PreambleDirective ::= "\#!" Whitespace* "syntax:" Whitespace* SyntaxConfig
SyntaxConfig      ::= Preset | ConfigList
Preset            ::= "free" | "typewriter"
ConfigList        ::= ConfigItem ("," Whitespace* ConfigItem)*
ConfigItem        ::= Key Whitespace* "=" Whitespace* Value
Key               ::= "newline" | "space"
Value             ::= "ignore" | "emit"
Whitespace        ::= " " | "\t"
```

### 3.2 Layout Modes and Equivalences

| Declaration Form | Internal Equivalent | Scanner Behavior | Target Use Case |
|---|---|---|---|
| `\#!syntax: free` | `newline=ignore, space=ignore` | **Free Layout Mode (explicitly activated)**: Unescaped newlines and spaces are silently absorbed; explicit blanks emitted via `\enter`, `\space`, `\tab`; whitespace-bearing text via verbatim text blocks `\text{}`…`\endtext{}` (see [§5.6](#56-verbatim-text-blocks-text--endtext)). Canonical recognition marker for v4 structured scripts. | Structured macro scripts (loops, branches, user macros, indentation). |
| `\#!syntax: typewriter` | `newline=emit, space=emit` | **Typewriter Mode (default)**: Raw newlines emit `Enter`; raw spaces emit `Space`. Scripts without preamble behave identically to v3 at source-semantics level (identical observable keystrokes and timing; the emitted container is VERSION=4 bytecode, not byte-identical — see [vm.md §8](vm.md#8-version-policy-and-history)). | Raw text typing, automated form filling, legacy v3 scripts. |
| `\#!syntax: newline=ignore, space=emit` | Fine-Grained Config | **Orthogonal Hybrid**: Newlines allow indentation; spaces emit `Space`. | Indented scripts requiring frequent inline whitespace typing. |

- **Source-Semantics Equivalence**: Layout modes alter only the scanner's interpretation of unescaped whitespace and newlines; once tokenized, all layout modes share identical grammatical rules, instruction sets, and execution semantics.
- **Preamble Scope**: The directive must appear in the leading comment lines of the script, before any non-comment, non-whitespace executable character. A `\#!syntax:` directive encountered after the first code token is a compile error. Unknown presets, unknown keys, and unknown `\#!` directive names are compile errors. A fine-grained `ConfigList` inherits the typewriter defaults (`emit`) for unspecified keys. Multiple directives are permitted; later entries override earlier ones per key.
- **Comment Orthogonality Law**:
  - `\# <text>`: Standard human-readable comments; silently ignored.
  - `\#!<directive>`: Compiler preamble directive; processed exclusively at the head of the file.

### 3.3 Lexer Scanning State Machine

| Input Stream Character | Context | `free` Mode (`\#!syntax: free`) | `typewriter` Mode (default) |
|---|---|---|---|
| Newline `\n` / `\r\n` / `\r` | Text Position | **Silently Absorbed** | **Emits Enter Key** (`0x02 KEY enter`) |
| Space `' '` / Tab `\t` | Text Position | **Silently Absorbed** | **Emits Space / Tab Key** |
| Explicit `\enter`, `\space`, `\tab` | Text Position | **Emits Target Keystroke** | **Emits Target Keystroke** |
| Line Continuation `\` + Newline | Text Position | **Silently Absorbed** | **Silently Absorbed** |
| `\text{}`…`\endtext{}` payload | Text block ([§5.6](#56-verbatim-text-blocks-text--endtext)) | **Every character emitted literally** | **Every character emitted literally** |
| Whitespace inside `{ a , b }` | Argument Position | **Always Automatically Trimmed** | **Always Automatically Trimmed** |
| Printable ASCII (32–126) | Text Position | **Emits Literal Keystroke** | **Emits Literal Keystroke** |

### 3.4 Canonical Syntax Examples

```dsl
\# 1. Canonical v4 structured script (free layout activated by preamble; indentation & blank lines are silent)
\#!syntax: free
\def{submit_form}[0]{
  \kdown{ctrl}
  \delay{50ms}
  \kup{ctrl}
  \enter          \# Explicitly emit Enter keystroke
}

\# 2. Text block: emitting whitespace-bearing text in free mode
\#!syntax: free
\text{}echo "Hello World"\endtext{}
\enter

\# 3. Default mode (no preamble; source-behavior identical to v3: newlines emit Enter, spaces emit Space)
echo "Hello MYCRO"
ls -la
```

---

## 4. Document Grammar & The Binary Brace Taxonomy

Commands sequentially consume trailing brace pairs `{}` according to their static signature contracts.

### 4.1 Abstract Grammatical Templates

Every brace structure in the surface language maps to one of four abstract templates:

1. **Executable Code Block Template (`{ code_stream }`)**: Delimits a nested sequence of executable commands and text actions. Parsing is isomorphic to the top-level macro stream.
2. **Separate Positional Argument Template (`{ arg_1 }{ arg_2 }...`)**: Passes independent arguments in separate adjacent brace pairs, each bound to an independent parameter category.
3. **Compound Tuple Argument Template (`{ elem_1, elem_2, ... }`)**: Passes multiple comma-separated elements within a single brace pair, forming a functional tuple.
4. **Optional Decorator Template (`[ modifier ]`)**: Passes optional configuration modifiers in literal square brackets.

### 4.2 The Structural Exclusivity Law (`{A}{B} \ne {A, B}`)

The brace structure expected by a command is statically and uniquely determined by its signature contract. The two forms are strictly mutually exclusive and cannot be interchanged:

- **Separate Argument Groups `{A}{B}`**: Positional arguments reside in separate brace pairs.
  *Illustrated by*: `\set{var}{val}` ([§6.2](#62-variable-assignment--arithmetic-set-add-sub)), `\add{var}{val}` ([§6.2](#62-variable-assignment--arithmetic-set-add-sub)), `\sub{var}{val}` ([§6.2](#62-variable-assignment--arithmetic-set-add-sub)).
- **Compound Argument Groups `{A, B}`**: Functionally coupled values reside within a single comma-separated brace pair.
  *Illustrated by*: `\move{dx, dy}` ([§5.3](#53-mouse-operations)), `\click{btn, count}` ([§5.3](#53-mouse-operations)).
- **Extension Calls (`\call`)**: The initial group is `{ns.fn}`; subsequent brace structures are consumed according to the registered signature of the target extension function (see [§9.3](#93-runtime-extension-calls-use-call)).
- **Compound Structures (Argument Group + Code Block)**: When a command accepts both parameters and an executable block, the argument groups strictly precede the code blocks.
  *Illustrated by*: `\rep{count}{ body }` ([§5.5](#55-bounded-counting-loops-rep)), `\if{pred}{ then }` ([§8.2](#82-conditional-branch-instruction-if)), `\guarded[throttle]{pred}{ body }` ([§8.3](#83-state-guarded-execution-guarded)).

### 4.3 Optional Decorator Brackets Discipline (`[]`)

Optional scalar configuration is written in **literal square brackets**.
**Position rule**: A decorator sits immediately adjacent to what it modifies:
- Modifying command execution: Leads before mandatory groups (e.g., `\guarded[50ms]{...}` in [§8.3](#83-state-guarded-execution-guarded), `\wait[5s]{...}` in [§9.2](#92-asynchronous-event-synchronization-wait)).
- Modifying a specific parameter: Follows that parameter group (e.g., `\def{name}[argc]{body}` in [§7](#7-compile-time-text-macros-def), where argc modifies name arity).

> [!NOTE]
> 1. `[...]` in command signatures always denotes literal square brackets. Inside EBNF grammar blocks, `[ x ]` denotes standard grammar optionality.
> 2. **Optional code blocks are not decorators**: Optional code blocks such as `else` in `\if` ([§8.2](#82-conditional-branch-instruction-if)) or `on_abort` in `\guarded` ([§8.3](#83-state-guarded-execution-guarded)) are written as trailing bare `{}` blocks, omissible as a whole.
> 3. Omissible elements inside a compound argument group (e.g., `count` in `\click{btn, count}`) belong to the argument group itself, not to decorators.
> 4. **Trailing-block adjacency**: An optional trailing block binds to its command only when nothing but layout-absorbed characters and comments intervene. In typewriter mode, any intervening printable character (spaces/newlines are keystrokes) breaks the binding; an unbound `{...}` then parses as ordinary literal text (braces are keystrokes per §2).

---

## 5. Base Macro Instruction System

Base macro instructions provide the fundamental actions of MYCRO scripts: emitting physical keystrokes, modifier combinations, multimedia actions, mouse actions, timing delays, inter-keystroke pacing, bounded repetition loops, and verbatim text blocks. These instructions operate directly without requiring state or variable declarations.

### 5.1 Keyboard Operations & Chording

```ebnf
KeyStmt      ::= SpecialKey | KeyCombo | KeyHold | KeyRelease
SpecialKey   ::= "\" KeyName
KeyCombo     ::= "\" KeyName ("+" KeyName)+
KeyHold      ::= "\kdown" "{" ComboPayload "}"
KeyRelease   ::= "\kup" "{" (ComboPayload | "0") "}"
ComboPayload ::= KeyName ("+" KeyName)*
KeyName      ::= [a-zA-Z] [a-zA-Z0-9_]*
```

#### Keyboard Inventory Table (USB HID Page 0x07)
All keys in this inventory belong to standard USB HID Usage Page 0x07 (Keyboard/Keypad) and transmit standard 1-byte usage IDs within the keyboard report:

| Category | Canonical Identifiers | Aliases | Usage ID | Semantics / Target Action |
|---|---|---|---|---|
| **Letters** | `a` .. `z` | — | `0x04` .. `0x1D` | Letter keys; uppercase forms `\A` .. `\Z` expand to `[shift, letter]` |
| **Digits** | `0` .. `9` | — | `0x1E` .. `0x27` | Main-row digit keys (`0x1E` = `1` … `0x26` = `9`, `0x27` = `0`) |
| **Core Editing & Whitespace** | `enter` | `return` | `0x28` | Return / Enter key |
| | `esc` | `escape` | `0x29` | Escape key |
| | `bs` | `backspace` | `0x2A` | Backspace key |
| | `tab` | — | `0x2B` | Tab key |
| | `space` | — | `0x2C` | Spacebar |
| | `minus` | — | `0x2D` | Main keyboard `-` / `_` key |
| | `equal` | — | `0x2E` | Main keyboard `=` / `+` key |
| | `plus` | — | `0x2E` (Shift) | Main keyboard `+` symbol in combo |
| **Editing & Navigation (6-Pack)** | `ins` | `insert` | `0x49` | Insert key |
| | `del` | `delete` | `0x4C` | Forward Delete key |
| | `home` | — | `0x4A` | Home key (cursor to line/document start) |
| | `end` | — | `0x4D` | End key (cursor to line/document end) |
| | `pgup` | `pageup` | `0x4B` | Page Up key |
| | `pgdn` | `pagedown` | `0x4E` | Page Down key |
| **Directional Arrows** | `up`, `down`, `left`, `right` | — | `0x52`, `0x51`, `0x50`, `0x4F` | Directional arrow navigation |
| **Lock Keys** | `caps` | `capslock` | `0x39` | Caps Lock toggle |
| | `numlock` | — | `0x53` | Numeric Keypad Num Lock toggle |
| | `scrlk` | `scrolllock` | `0x47` | Scroll Lock toggle |
| **System & Screen Control** | `prtscr` | `printscreen`, `snapshot` | `0x46` | Print Screen / Screen Capture |
| | `pause` | `break` | `0x48` | Pause / Break key |
| | `menu` | `app`, `contextmenu` | `0x65` | Application / Right-click Context Menu |
| **Standard Function Keys** | `f1` .. `f12` | — | `0x3A` .. `0x45` | Standard function keys |
| **Extended Macro Function Keys** | `f13` .. `f24` | — | `0x68` .. `0x73` | Dedicated conflict-free macro function keys |
| **Numeric Keypad (Keypad)** | `kp_0` .. `kp_9` | — | `0x59` .. `0x62` | Dedicated numeric keypad digits |
| | `kp_dot` | `kp_period` | `0x63` | Dedicated keypad decimal point |
| | `kp_enter` | — | `0x58` | Dedicated keypad Enter |
| | `kp_plus` | — | `0x57` | Dedicated keypad `+` |
| | `kp_minus` | — | `0x56` | Dedicated keypad `-` |
| | `kp_multiply` | `kp_star` | `0x55` | Dedicated keypad `*` |
| | `kp_divide` | `kp_slash` | `0x54` | Dedicated keypad `/` |
| | `kp_equal` | — | `0x67` | Dedicated keypad `=` |
| **Symmetric Modifiers** | `ctrl` (`lctrl`), `rctrl` | `control`, `lcontrol`, `rcontrol` | `0xE0`, `0xE4` | Left/Right Control modifier |
| | `shift` (`lshift`), `rshift` | — | `0xE1`, `0xE5` | Left/Right Shift modifier |
| | `alt` (`lalt`), `ralt` | `opt`, `option`, `lopt`, `ropt`, `loption`, `roption`, `altgr` | `0xE2`, `0xE6` | Left/Right Alt / AltGr / Option |
| | `gui` (`lgui`), `rgui` | `win`, `lwin`, `rwin`, `cmd`, `command`, `lcmd`, `rcmd`, `lcommand`, `rcommand` | `0xE3`, `0xE7` | Left/Right Windows / Command / GUI |

#### Chording & Combination Rules
- **Delimiter Exclusivity**: The plus symbol `+` serves exclusively as the combination delimiter in chording expressions. Physical plus keys are represented by explicit key identifiers:
  - Main keyboard: `plus` (physical `Keypad = / +` usage ID `0x2E`), `equal` (`0x2E`), `minus` (`0x2D`).
  - Dedicated numeric keypad: `kp_plus` (`0x57`), `kp_minus` (`0x56`), `kp_multiply` (`0x55`), `kp_divide` (`0x54`), `kp_enter` (`0x58`).
- **Sole Lowering Path**: All combinations lower to key sequences; the legacy `COMBO` opcode (`0x03`) is deprecated and NOP'd by VM v4 ([vm.md §4](vm.md#4-opcode-set-v4)).
- **Sequential Lowering & Symmetric LIFO Envelope**:
  A combination $T_1 + T_2 + \dots + T_n$ maps each constituent token $T_i$ to its physical keycode sequence $K(T_i)$ (e.g., `a` $\rightarrow [a]$, `A` $\rightarrow [\text{shift}, a]$, `plus` $\rightarrow [\text{shift}, \text{equal}]$). The compiler concatenates these sequences linearly into $\mathbf{K} = K(T_1) \mathbin{\Vert} \dots \mathbin{\Vert} K(T_n)$ and emits opcodes strictly according to user-specified order:
  1. **Sequential Depression**: Emits `KEY_DOWN` forward for each key in $\mathbf{K}$.
  2. **Pulse Hold**: Emits `SLEEP click_hold_ms` (profile constant `DEFAULT_CLICK_HOLD_MS = 10ms`; see [vm.md §1.0](vm.md#10-authoritative-profile-constants-table)).
  3. **LIFO Release**: Emits `KEY_UP` in reverse order for each key in $\operatorname{reversed}(\mathbf{K})$.
  4. **Inter-Action Pacing**: The compiler emits no pacing bytes; each `KEY_DOWN`/`KEY_UP` of the sequence is itself a peripheral action, paced by the VM's automatic pacing rule (see [§5.4](#54-timing-delays--inter-keystroke-pacing) and [vm.md §4](vm.md#4-opcode-set-v4)).
- **Single-Key Lowering (`SpecialKey`)**: A lone key token lowers to exactly **one** action, never a sequence:
  - If the token names exactly one printable ASCII character — letters `\a`–`\z` / `\A`–`\Z`, character-named keys (`\plus`, `\minus`, `\equal`, `\space`, …) — it lowers to one `CHAR` opcode carrying that character (`\A` = `CHAR 0x41`). Shift resolution is the keymap's job, identical to typing the character in text position. (HID usages are caseless; case exists only as the Shift modifier or as a character. Digit keys have no command form — `\1` is rejected at the lexer, digits are typed as text characters.)
  - Otherwise (non-printable named keys: `\enter`, `\tab`, arrows, F-keys, keypad keys) it lowers to one `KEY` opcode — a VM-level tap (press, hold `click_hold_ms`, release).

  The character form and the physical form are both first-class and intentional: `\A` types the character A (one action, one pacing unit), while the combination syntax `\shift+a` spells out the physical chord (sequence lowering, each constituent action paced). The $K(T)$ mapping above serves combinations and `\kdown`/`\kup` payloads, where `A` still expands physically to $[\text{shift}, a]$.

> [!NOTE]
> **Hold Configuration Scope**: `click_hold_ms` is a runtime-adjustable global configuration (default 10ms) serving as the **default hold duration for single-shot press/click actions only** — keyboard taps, mouse clicks, and consumer-control pulses (HID transports only press and release events, so a single-shot action is realized as press + hold interval + release). It does NOT apply to explicit hold control: any press duration the script must control is expressed as `\kdown`/`\mdown` + `\delay` + `\kup`/`\mup`. VM-internal taps (`KEY`/`CHAR`/`MCLICK`/`CONSUMER_KEY`) read the live value at execution time; combination sequences use the value the compiler folds into the emitted `SLEEP` operand at compile time.
- **Key Hold & Release (`\kdown`, `\kup`)**:
  - `\kdown{combo}`: Emits forward `KEY_DOWN` sequence across $\mathbf{K}$ without auto-release.
  - `\kup{combo}`: Emits reverse `KEY_UP` sequence across $\operatorname{reversed}(\mathbf{K})$.
  - `\kup{0}`: Failsafe releasing all physically held keys on the device. (The payload `0` is reserved for release-all, so the main-row `0` key cannot be released individually; include it in a combination payload such as `shift+0` — releasing a non-held key is harmless.)
- **Extreme Boundary Lowering (`\a+A`)**:
  Tokens are lowered mechanically without heuristic intervention or duplication pruning:
  `\a+A` expands to $\mathbf{K} = [a, \text{shift}, a]$ and lowers strictly to:
  $$\text{KEY\_DOWN } a \to \text{KEY\_DOWN } \text{shift} \to \text{KEY\_DOWN } a \to \text{SLEEP } \text{hold} \to \text{KEY\_UP } a \to \text{KEY\_UP } \text{shift} \to \text{KEY\_UP } a$$
  *Platform Behavior Note*: Host operating systems and USB HID driver stacks may treat rapid identical keycode assertions (such as `\a+A` expanding to double `a` depression within milliseconds) as redundant or ignored. Scripts requiring distinct sequential presses should use explicit spacing or pacing.

> [!NOTE]
> **Compiler Implementation Note (Linear Streaming Expansion)**:
> In the single-pass lexer, scanning a `+` chain collects key identifiers into a bounded buffer ($\le 8$ entries, $O(1)$ stack memory), resolves each token to its keycode sequence, iterates forward to emit `KEY_DOWN`, and iterates in reverse to emit `KEY_UP`. This guarantees zero-AST streaming parsing adhering to [§1.1 Axiom I](#11-axiom-i-zero-ast-single-pass-streaming).

### 5.2 Multimedia & Consumer Control

```ebnf
MediaStmt ::= "\" MediaKey
MediaKey  ::= "media_playpause" | "media_next" | "media_prev" | "media_stop" | "media_play" | "media_pause"
            | "volup" | "voldn" | "mute" | "brightup" | "brightdn" | "sys_sleep"
```

Multimedia and consumer control actions target the **USB HID Consumer Control** collection (Usage Page 0x0C). Unlike keyboard keystrokes, these operate on 16-bit consumer usage codes. Explicit prefixes (`media_`, `sys_`) prevent naming collisions with macro execution commands (e.g., distinguishing `\sys_sleep` from macro execution delay `\delay`, and `\media_stop` from macro halting):

| Command | Aliases | Consumer Usage ID | Target Hardware Action |
|---|---|---|---|
| `\media_playpause` | `playpause`, `play_pause` | `0x00CD` | Toggle media playback (Play / Pause) |
| `\media_next` | `next_track` | `0x00B5` | Skip to next audio/video track |
| `\media_prev` | `prev_track` | `0x00B6` | Skip to previous audio/video track |
| `\media_stop` | `stop_track` | `0x00B7` | Stop media playback |
| `\media_play` | `play` | `0x00B0` | Start media playback |
| `\media_pause` | `pause_track` | `0x00B1` | Pause media playback |
| `\volup` | `media_volup`, `volume_up` | `0x00E9` | Increment master audio volume |
| `\voldn` | `media_voldn`, `volume_down` | `0x00EA` | Decrement master audio volume |
| `\mute` | `media_mute`, `volume_mute` | `0x00E2` | Toggle master audio mute |
| `\brightup` | `brightness_up` | `0x006F` | Increment display brightness |
| `\brightdn` | `brightness_down` | `0x0070` | Decrement display brightness |
| `\sys_sleep` | `system_sleep`, `host_sleep` | `0x0032` | Trigger host system suspend / sleep |

#### The Fn Key Physical Matrix Architecture
On physical computer keyboards, the `Fn` key is a local matrix layer switcher handled entirely inside the physical keyboard controller. It does NOT generate or emit any standard USB HID keyboard code to the operating system. When a user presses `Fn + F1` on a physical keyboard, the keyboard controller translates this internally and transmits Consumer Control `MUTE` (`0x00E2`).

Because MYCRO emulates the USB HID controller directly, script authors do NOT specify virtual Fn modifier combinations. Instead, multimedia, navigation, and display actions are dispatched as first-class primary instructions (`\mute`, `\volup`, `\brightup`, `\home`, `\f13`..`\f24`).

### 5.3 Mouse Operations

```ebnf
ClickStmt ::= "\click" "{" Button [ "," Value ] "}"
MoveStmt  ::= "\move" "{" Value "," Value "}"
MouseDown ::= "\mdown" "{" Button "}"
MouseUp   ::= "\mup" "{" Button "}"
Button    ::= "L" | "R" | "M" | "B" | "F"
```

- **`\click{btn[, count]}`**: Mouse click (`btn` in Name category `L|R|M|B|F`; `count` omissible in-group Value, $1 \le count \le 65535$, defaults to 1).
- **`\move{dx, dy}`**: Relative mouse move (`dx`, `dy` in Value category, signed `i16` satisfying $-32768 \le dx, dy \le 32767$).
- **`\mdown{btn}`, `\mup{btn}`**: Mouse button hold / release.

### 5.4 Timing Delays & Inter-Keystroke Pacing

```ebnf
DelayStmt ::= "\delay" "{" Duration "}"
PaceStmt  ::= "\pace" "{" Duration "}"
Duration  ::= DimLiteral | DecLiteral | ScalarRef
ScalarRef ::= VarRef | SysVarRef | SlotRef
```

1. **Direct Expression & In-Place Folding**: Time values are expressed via dimensioned literals (`50ms`, `0.5s`) or dimensionless integers (ms), folded at compile time into integer millisecond scalars (see [§2.1](#21-numeric-representation--integer-folding)).
2. **Physical u16 Constraints**: Underlying timing opcodes strictly retain `u16 ms` ($0 \le t \le 65535$). Static folded constants $> 65535\text{ ms}$ or $< 0$ trigger static compile errors; longer durations must use bounded loops (e.g., `\rep{100}{ \delay{1s} }`).
3. **Dynamic Variable Range Handling**: When a timing command consumes a variable (e.g., `\delay{$d}` or `\delay{${d}}`), the runtime **clamps** the value to $[0, 65535]$ (negative runtime values clamp to `0`). Bitwise masking/truncation is strictly forbidden.
4. **Semantics**:
   - `\delay{t}`: Sleep for duration `t` ($0 \le t \le 65535\text{ ms}$).
   - `\pace{t}`: Configure the global inter-action pacing interval ($0 \le t \le 65535\text{ ms}$). Pacing is applied by the VM after every peripheral-action opcode (see [vm.md §4](vm.md#4-opcode-set-v4)); the compiler emits nothing extra. Pacing state is global to the execution: initialized to `DEFAULT_PACE_MS` at start, modified only by `\pace`, with no per-scope save or restore.

(Hex literals are not accepted in Duration positions.)

### 5.5 Bounded Counting Loops (`\rep`)

```ebnf
RepStmt ::= "\rep" "{" Value "}" "{" CodeBlock "}"
```

- **`\rep{count}{ body }`**: Executes `body` repeatedly for `count` iterations ($0 \le count \le 255$, or variable `$var`; count of 0 skips the body).
- `body` is an Executable Code Block.
- Loop nesting is bounded by `MAX_RUNTIME_NEST = 8`.

### 5.6 Verbatim Text Blocks (`\text{}` … `\endtext{}`)

```ebnf
TextBlock ::= "\text{}" Payload "\endtext{}"
Payload   ::= (* any character sequence not containing "\endtext{}"; zero escapes, emitted verbatim *)
```

- **Strict Symmetrical Pair**: A text block is defined strictly and symmetrically by the instruction pair `\text{}` and `\endtext{}`.
- **Syntactic Barrier (`{}`)**: The empty `{}` is the explicit syntactic delimiter of each instruction, cleanly separating the command token from surrounding text without requiring whitespace heuristics:
  - Any character immediately following `\text` other than `{}` (e.g. `\text foo` or `\text\n`) triggers an immediate compile error (`\text requires {}`).
  - Any character immediately following `\endtext` other than `{}` triggers an immediate compile error (`\endtext requires {}`).
  - `\endtext{}` outside an active text block triggers an `endtext without text block` compile error.
- **Verbatim Payload Semantics**:
  - The payload begins on the byte immediately following the closing `}` of `\text{}`.
  - The payload ends on the byte immediately preceding the `\` of `\endtext{}`.
  - Every enclosed character is emitted verbatim as physical keystrokes under **all layout modes** (spaces/tabs/newlines emit Space/Tab/Enter keys).
  - Zero escapes: No macro commands, comments, escapes (`\\`), or variable sigils (`$`) are recognized inside the payload (a backslash is a literal keystroke).
  - **Newline Normalization**: Line endings inside the payload follow the §3.3 convention: a CRLF sequence emits a single Enter; a standalone CR emits Enter.
- **Termination & Lexical Protection**:
  - The scanner searches the payload strictly for the literal sequence `\endtext{}`. This sequence is the payload's only reserved sequence; exhausting the character stream without it raises an `Unterminated text block` compile error.
  - User macros cannot be named `text` or `endtext`.
- **Full Immunity in `\def`**: Parameter text inside `\def{gg}[1]{\text{}#1\endtext{}}` passes directly into the payload without brace parsing, allowing parameter payloads to contain unescaped braces, brackets, or backslashes (but not the literal terminator sequence `\endtext{}`, which still ends the block — substitution precedes scanning; see [§7](#7-compile-time-text-macros-def)).

---

## 6. Values, Variables & State Model

When macros require dynamic parameters, counters, or coordination, MYCRO provides a scalar variable system and parameter calling contracts.

### 6.1 Variables & Storage Model

```ebnf
VarRef  ::= "$" VarName | "${" VarName "}"
VarName ::= [a-zA-Z] [a-zA-Z0-9_]*
```

- **User Variables**: A macro script supports up to `MAX_VARS = 32` distinct user variables (corresponding to the VM register profile; see [vm.md §2](vm.md#2-register-file)). Each variable holds a signed two's complement **i32** scalar ($[-2^{31}, 2^{31} - 1]$) or an opaque resource handle. All variables are initialized to zero at execution start.
- **Implicit Declaration & Reserved Names**: Variables are declared implicitly upon their first write via `\set{name}{val}`. Variable identifiers (`VarName`) are strictly case-sensitive, begin with an ASCII letter, and match `[a-zA-Z][a-zA-Z0-9_]*`. System keywords and register names (`ret`, `timeout`, `err`, `slot`) are reserved; assigning to them as user variables triggers a static compile error (`Reserved variable name: {name}`).
- **Reading Variables (`VarRef`)**: In Value positions, reading a variable strictly requires the `$` sigil, supported in two syntactically equivalent representations:
  - **Bare Reference Form (`$name`)**: The `$` sigil immediately followed by an alphanumeric identifier (e.g., `$x`, `$count`). The scanner consumes matching identifier characters greedily.
  - **Delimited Reference Form (`${name}`)**: The opening sigil sequence `${` followed by the variable identifier and terminated by an explicit closing brace `}` (e.g., `${x}`, `${count}`).
- **Delimited Form Mechanics & Lexical Boundaries**:
  - The delimited syntax `${name}` establishes an explicit lexical boundary, preventing syntactic ambiguity when variable references directly abut literal text or occur within compact, comma-separated argument groups (e.g., `\move{${x},0}`).
  - **Strict Enclosure Invariants**:
    - An unclosed `${` (missing closing `}`) raises an immediate static compile error (`Unmatched ${`).
    - An empty reference `${}` or `${   }` raises an immediate static compile error (`Empty reference`).
    - Any non-whitespace characters following the closing `}` within the reference token (e.g., `${x}trailing`) raise an immediate static compile error (`Unexpected text after ${...}`).
- **Static Resolution & Diagnostics**:
  - Both `$name` and `${name}` resolve identically to the same underlying register index at compile time.
  - Register indices are allocated from `0x00` upward in order of each variable's first write (`\set`).
  - Reading an unassigned variable triggers a static compile error (`Undefined variable: {name}`).
  - Variable evaluation is active strictly in Value positions; in plain keystroke text or verbatim text blocks (`\text{}`), `$` and `${` are treated as literal printable characters.

### 6.2 Variable Assignment & Arithmetic (`\set`, `\add`, `\sub`)

MYCRO intentionally omits infix arithmetic expressions (e.g., `x = a + b`); variable mutations are expressed exclusively through three statement commands:

```ebnf
SetStmt ::= "\set" "{" VarName "}" "{" Value "}"
AddStmt ::= "\add" "{" VarName "}" "{" Value "}"
SubStmt ::= "\sub" "{" VarName "}" "{" Value "}"
```

- **`\set{var}{val}`**: Assigns `val` to variable `var`, replacing any previous value.
- **`\add{var}{val}`**: Adds `val` to variable `var` in-place, with signed 32-bit two's complement modular wraparound ($[-2^{31}, 2^{31} - 1]$).
- **`\sub{var}{val}`**: Subtracts `val` from variable `var` in-place, with signed 32-bit two's complement modular wraparound ($[-2^{31}, 2^{31} - 1]$).
- **Handle Type Restrictions**: Opaque resource handles (such as snapshot handles returned by `\call`) have no arithmetic semantics. Supplying a handle to `\add` or `\sub` is a **runtime type fault**: the operation performs no mutation and sets `$err = ERR_HANDLE_MISUSE` (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)). Where a handle type is syntactically evident (e.g., a `\pixelrect` constructor in an arithmetic position), the compiler MAY reject it at compile time. Underlying handle lifetimes and resource reclamation are managed automatically by the runtime environment (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

### 6.3 System Read-Only Variables & Receptor Slots

```ebnf
SysVarRef  ::= "$" SysVarName | "${" SysVarName "}"
SysVarName ::= "ret" | "timeout" | "err"
```

In addition to user variables (`$var`, `${var}`), MYCRO provides three specialized categories of read-only variables, all supporting bare and delimited reference forms:

- **System Status Variables (`SysVarRef`)**: Dedicated registers reserved for execution status:
  - **`$ret` / `${ret}`**: Holds the scalar return value or resource Handle ID from the most recent extension call (`\call`). Overwritten on each call; evaluates to `0` if the callee returns None or encounters an error (see [§9.3](#93-runtime-extension-calls-use-call)).
  - **`$err` / `${err}`**: Holds the error status code from the most recent extension call (`\call`) or handle-misuse type fault. Evaluates to `0` on success, or a non-zero error code from the standard error table on failure (see [runtime.md §3.0](runtime.md#30-universal-return--error-contract)).
  - **`$timeout` / `${timeout}`**: Holds the completion status of the most recent receptor wait (`\wait`). Evaluates to `1` if the wait timed out, or `0` if an external projection arrived or consumed a pending event (see [§9.2](#92-asynchronous-event-synchronization-wait)).
- **Asynchronous Receptor Slots (`SlotRef`)**: Variables prefixed with `$slot.` (e.g., `$slot.<sig>.<field>` or `${slot.<sig>.<field>}`) act as stateful event latches reflecting external signals (such as host network packets or vision telemetry). They can be evaluated as dynamic values or waited on via `\wait`. The complete Signal/Slot orthogonal architecture and synchronization contracts are normatively specified in **[§9.1](#91-the-signal--slot-orthogonal-architecture)**.

### 6.4 Static Value Constructors (`\rgb`, `\pixelrect`)

Static value constructors are compile-time literal expressions occurring in Value parameter positions. They produce either folded numeric scalar immediates or pre-allocated static resource handles without generating runtime constructor bytecode.

- **Compile-Time Color Packing (`\rgb`)**: `\rgb{r, g, b}` packs three 0–255 integer constants ($u8$) into a single 24-bit RGB scalar integer: `(r << 16) | (g << 8) | b`.
- **Pixel Rectangle Constants (`\pixelrect`)**: Declares a static 2D pixel matrix literal:
  ```ebnf
  PixelRect  ::= "\pixelrect" "{" Row ("," Row)* "}"
  Row        ::= "\row" "{" PixelColor ("," PixelColor)* "}"
  PixelColor ::= HexLiteral | DecLiteral | "\rgb" "{" u8 "," u8 "," u8 "}"
  ```
  - **Rectangular Invariant**: All rows must contain an identical number of pixels ($W_i = W_0$); irregular row lengths trigger an immediate static compile error.
  - **Dimension Bounds**: Matrix dimensions are bounded by `RECT_MAX_EDGE = 128` pixels ($1 \le W, H \le 128$; total buffer $W \times H \times 3 \le 48\text{ KiB}$ matching `SNAP_SLOT_BYTES`). Exceeding this bound triggers a compile error.
  - **Pixel Color Range**: Each color element must evaluate to an integer $0 \le c \le 0xFFFFFF$.

> [!NOTE]
> **Static Constructor Mechanism: Zero-Bytecode & Pre-Populated Storage**
>
> Static constructors are evaluated and materialized entirely during compilation. They adhere to two fundamental architectural invariants:
>
> 1. **Zero Runtime Constructor Bytecode**:
>    Static constructors **never emit bytecode instructions at runtime to construct objects, allocate buffers, or execute initialization loops**. The VM runtime contains no opcodes or dynamic allocators for building RGB colors or pixel matrices.
> 2. **Pre-Populating Memory vs. Immediate Folding**:
>    - **Scalar Folding (`\rgb`)**: Evaluated at compile time directly into a 24-bit numeric scalar immediate (`TAG_IMM (0)`). It is emitted inline into the operand field of the consuming instruction (such as `\set` or `\call`), incurring zero runtime CPU overhead and leaving zero construction footprint in bytecode.
>    - **Memory Pre-Population & Const Store Materialization (`\pixelrect`)**: The compiler parses the 2D pixel array, serializes the raw RGB byte buffer, and **pre-populates the runtime middleware's static constant store** (`runtime.add_const("snap", ...)`) during compilation. Bytecode carries only a lightweight constant reference (materialized via the `sys.const` intrinsic into a static snapshot Handle). Constant IDs are allocated from `0` upward in order of constructor appearance in the source. At runtime, macro execution performs zero pixel copying, zero buffer allocation, and zero heap churn on the ESP32-S3 microcontroller, eliminating heap fragmentation and GC jitter.

### 6.5 Parameter Categories & Calling Contracts

In [§4.1](#41-abstract-grammatical-templates) and [§4.2](#42-the-structural-exclusivity-law-ab-ne-a-b), MYCRO established the abstract syntactic templates governing command invocations (`\command`), argument grouping styles (`{A}{B}` vs `{A, B}`), and optional bracket decorators (`[]`). With the underlying state representation and value inventory now established across [§6.1](#61-variables--storage-model)–[§6.4](#64-static-value-constructors-rgb-pixelrect) (scalar variables, arithmetic mutations, asynchronous receptor slots, and static value constructors), this section bridges grammar to execution semantics by formalizing the three orthogonal **Parameter Categories** that populate those brace groups, alongside their physical compilation lowering contracts.

```ebnf
Value      ::= NumericLiteral | DynamicRef | Constructor
DynamicRef ::= VarRef | SysVarRef | SlotRef
```

Every command argument position belongs to one of three orthogonal categories based on signature contracts:

| Parameter Category | Syntactic Form & Evaluation Rules | Compilation & Runtime Mapping |
|---|---|---|
| **Value Category (Value)** | Dynamic variables, numeric immediates, or compile-time constructors:<br>• Variables: User variables (`$var`, `${var}`), system read-only variables (`$ret`, `${ret}`, `$timeout`, `${timeout}`, `$err`, `${err}`), receptor slots (`$slot.<sig>.<field>`, `${slot.<sig>.<field>}`);<br>• Static literals: Decimal, hex, or time literals (`500ms`, `0.5s`), folded at lex time to `i32`;<br>• Constructors: Static value constructors (packed RGB `\rgb{r, g, b}`, pixel rectangle `\pixelrect{...}`; see [§6.4](#64-static-value-constructors-rgb-pixelrect)). | **Physical 3-Tag Closed Operand Architecture**:<br>• Immediate literal $\rightarrow$ `TAG_IMM (0)` (`i32 LE`);<br>• Register $\rightarrow$ `TAG_REG (1)` (`u8 reg`);<br>• **Receptor slots (`$slot...`)** $\rightarrow$ Pre-instruction desugaring: compiler loads slot into an ephemeral register (allocated from the lowest available register index not bound to a user variable; a second slot operand of the same instruction takes the next-lowest unbound register; scratch registers are released immediately after the consuming instruction; **if no unbound register remains, compilation fails deterministically** with `no free register for slot operand`) before host instruction execution, passing `TAG_REG`. In `\guarded` descriptors, slots are encoded natively via the `SLOT` descriptor tag. |
| **Name Category (Name)** | Static symbolic identifiers: Key names, mouse buttons, namespaces, signal channel names, variable names, and `\call` label arguments (e.g. `net.send` event labels). Bare identifiers, strictly prohibiting `$`. | Statically resolved at compile time to usage IDs or string-table indices (`u8`; `\call` name-category arguments and `net.send` labels are emitted as `TAG_NAME (2)` operands). Labels preserve exact case. |
| **Predicate Category (Predicate)** | Conditional decision structures: Atomic triplets `{lhs, op, rhs}` or composite logic trees `\all` / `\any` (see [§8](#8-conditional-control--guarded-execution-if-guarded)). | Directly lowered by the compiler into forward short-circuit jump sequences (`OP_BRA`). |

### 6.6 Dynamic Keystroke Rendering (`\typenum`)

While base macro instructions ([§5](#5-base-macro-instruction-system)) emit static keystrokes from literal tokens and arithmetic instructions ([§6.2](#62-variable-assignment--arithmetic-set-add-sub)) mutate state in-place, `\typenum` is an **executable action instruction** that consumes a Value-category variable operand ([§6.5](#65-parameter-categories--calling-contracts)) and bridges dynamic variable state directly to the USB HID typing engine.

- **Syntactic Form**: `\typenum{$var}` (also accepts `${var}`, `$ret`, `${ret}`, `$timeout`, `${timeout}`, `$err`, `${err}`, and asynchronous slots `$slot.<sig>.<field>`, `${slot.<sig>.<field>}`).
- **Execution Semantics**: Evaluates the signed 32-bit integer scalar stored in the variable operand, converts its value to an ASCII decimal string representation (including a leading `-` for negative quantities), and sequentially transmits each character as an individual USB HID keypress and release, adhering strictly to active pacing intervals set by `\pace`.
- **Static Type Invariant**: `\typenum` accepts exclusively variables holding numeric scalars (`int` or `color`). Supplying a non-variable (e.g., `\typenum{100}`) halts compilation immediately (`typenum requires a variable ($name)`). A variable that holds an opaque resource Handle at runtime triggers a **runtime type fault**: no output, `$err = ERR_HANDLE_MISUSE` (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

### 6.7 Physical Word-Length Contracts

Every macro instruction consuming numeric immediates enforces strict physical word-length and range bounds corresponding to its target VM opcode. **Static immediates** violating these intervals trigger immediate static compilation diagnostics. **Dynamic register values** are uniformly **clamped** to the target interval at runtime (e.g., `\rep{$n}` with `$n = 300` runs 255 iterations; `\move{$x, 0}` with `$x = 40000` moves +32767); modulo truncation is forbidden everywhere:

| Target Instruction | Opcode Parameter Width | Valid Immediate Interval | Normative Definition & Reference |
|---|---|---|---|
| `\delay{t}`, `\pace{t}` | `u16` (2 bytes LE) | $[0, 65535]\text{ ms}$ | Timing base. Durations $> 65535\text{ ms}$ must be expressed via `\rep` loops (see [§5.4](#54-timing-delays--inter-keystroke-pacing), [§5.5](#55-bounded-counting-loops-rep)). |
| `\rep{N}` | `u8` (1 byte) | $[0, 255]$ | Loop counter ceiling (`MacroError: rep count out of u8 range`; see [§5.5](#55-bounded-counting-loops-rep)). |
| `\click{btn, count}` | `u16` (2 bytes LE) | $[1, 65535]$ | Click repeat count (defaults to 1; see [§5.3](#53-mouse-operations)). |
| `\move{dx, dy}` | `i16` (2 bytes LE) | $[-32768, 32767]$ | Relative mouse movement offsets (see [§5.3](#53-mouse-operations)). |
| `\wait[timeout]{ $slot... }` | `i32` (4 bytes LE) | $[-2^{31}, 2^{31} - 1]\text{ ms}$ | Timeout duration (omitted = $2147483647\text{ ms}$; $\le 0$ = immediate poll, for literals and dynamic values alike; see [§9.2](#92-asynchronous-event-synchronization-wait)). |
| `\guarded[throttle_ms]` | `u16` (2 bytes LE) | $[0, 65535]\text{ ms}$ | Minimum polling interval (defaults to 0 = continuous; see [§8.3](#83-state-guarded-execution-guarded)). |
| `\rgb{r, g, b}` | `u8` $\times 3$ (1 byte each) | $[0, 255]$ each | Red, green, blue color channels (see [§6.4](#64-static-value-constructors-rgb-pixelrect)). |
| `\pixelrect` edge length | `u8` (1 byte) | $[1, 128]$ | Maximum width and height bounded by `RECT_MAX_EDGE = 128` (see [§6.4](#64-static-value-constructors-rgb-pixelrect)). |
| `\set`, `\add`, `\sub` | `i32` (4 bytes LE) | $[-2^{31}, 2^{31} - 1]$ | Signed 32-bit integer scalar with modular arithmetic (see [§6.2](#62-variable-assignment--arithmetic-set-add-sub)). |

### 6.8 Static Compile Diagnostics

- **Character Set & Delimiters**: Non-printable ASCII (outside 32–126, with the explicit exception of Tab (9), LF (10), and CR (13) in text positions and text blocks) and non-ASCII bytes (≥ 0x80) raise compile errors. Unmatched delimiters (`{`, `}`, `[`, `]`, `${`) **in structural positions** raise compile errors (in text positions they are literal keystrokes, §2).
- **Name vs Value Partition & Identifiers**: Name positions strictly forbid `$`. Variable and identifier names are case-sensitive and must match `[a-zA-Z][a-zA-Z0-9_]*`; reserved names (`ret`, `timeout`, `err`, `slot`) cannot be used as user variable names. Undefined variables, unknown keys, unknown mouse buttons, unknown namespaces, and unknown signals raise compile errors. `net.send` labels must be non-empty (optionally dotted) identifiers; empty or non-identifier labels raise compile errors.
- **Signal Namespace Validation**: Signals must belong to statically recognized built-in channels (`net.msg`) or namespaces imported via `\use` (`img.<name>` under `\use{img}`). Signals belonging to unknown or undeclared namespaces raise static compile errors (`unknown signal namespace`).
- **Guard Predicate Flatness**: `\guarded` invariant predicates must be flat (1 to 8 triplets); nested `\all` or `\any` blocks trigger compile errors.
- **Container Ceilings**: Emitted bytecode stream cannot exceed `MAX_BYTECODE = 65535` bytes; string table cannot exceed `MAX_STRINGS = 255` entries, and each entry cannot exceed `MAX_STRING_BYTES = 255` UTF-8 bytes.
- **Scratch Register Exhaustion**: A Value-position slot reference when all 32 registers are bound to user variables raises a compile error (`no free register for slot operand`; see [§6.5](#65-parameter-categories--calling-contracts)).
- **Eradication of Silent Truncation**: Immediate values outside valid physical opcode ranges halt compilation immediately.
- **Preamble Directive Ordering**: `\#!syntax:` directives must appear at the head of the file before any executable action token.
- **Predicate Triplet Structure**: Predicate arguments must be formatted as dual-brace triplets `{ {lhs, op, rhs} }` with operators in `{=, !=, <, <=, >, >=}`.
- **Receptor Slot Wait Target**: The argument to `\wait` must be a `$slot.<sig>.<field>` receptor. Passing user variables, system variables, or literals raises a compile error (see [§9.2](#92-asynchronous-event-synchronization-wait)).
- **Numeric Typenum Target**: The argument to `\typenum` must be a variable or slot reference. Passing non-variables triggers a compile error; a handle held at runtime triggers a runtime type fault via `$err` (see [§6.6](#66-dynamic-keystroke-rendering-typenum)).
- **Guard Nesting Limit**: `\guarded` nesting cannot exceed `MAX_GUARD_NEST = 4`.
- **Predicate Nesting Limit**: `\all`/`\any` logic blocks nest at most `MAX_PRED_NEST = 4` deep (see [§8.1](#81-predicate-grammar-ebnf)).
- **Namespace Declaration**: `\call`, signal, and `$slot` references to a namespace require a preceding top-level `\use{ns}` (no exemptions); references to unknown or undeclared namespaces raise compile errors (see [§9.3](#93-runtime-extension-calls-use-call)).
- **Extension Signature Match**: `\call` argument groups must match the registered signature in count, structure, and category; mismatches raise compile errors. `sys.const` is compiler-internal and not callable from source.
- **Malformed Slot References**: `$slot.` with an empty signal or field segment (e.g., `$slot.`, `$slot..x`) raises a compile error.
- **Compound Group Elements**: Empty elements and trailing commas in compound argument groups (e.g., `\move{1,}`) raise compile errors.

---

## 7. Compile-Time Text Macros (`\def`)

```ebnf
DefStmt   ::= "\def" "{" MacroName "}" [ "[" Argc "]" ] "{" CodeBlock "}"
MacroCall ::= "\" MacroName ArgGroup*
MacroName ::= [a-zA-Z] [a-zA-Z0-9_]+
Argc      ::= "0" | "1" | ... | "9"
```

- **Definition & Expansion**:
  - Declared at top level: `\def{name}[argc]{body}`.
  - Parameters `#1`–`#9` are single-pass textual substitutions expanded on the compiler's `_nest_stack`. Substituted text is not rescanned for parameters. `##` emits a literal `#`.
  - Expansion depth bounded by `MAX_MACRO_EXPAND = 16`.
  - Macro expansion bodies directly inherit the script's preamble layout configuration (`\#!syntax`).
  - Parameters forward transparently into compound argument groups and predicate triplets without call-site parsing.
- **Scoping & Definition Invariants**:
  1. **Forward-Definition Only**: A macro must be defined via `\def` prior to any call site. Recursive or backward invocations trigger static compile errors (`undefined macro`).
  2. **Top-Level Scope Only**: `\def` declarations are valid strictly at the root document scope. Defining macros inside loops, branches, guards, or other macros triggers a compile error (`def not allowed in nested scope`).
  3. **No Redefinition**: Defining a macro with an existing user macro name triggers a compile error (`macro redefinition: {name}`).
  4. **Reserved Name Conflicts**: Macro names must be at least 2 characters long and cannot collide with built-in macro commands (`delay`, `rep`, `pace`, `click`, `move`, `kdown`, `kup`, `mdown`, `mup`, `set`, `add`, `sub`, `typenum`, `text`, `endtext`, `def`, `if`, `guarded`, `use`, `call`, `wait`), media/consumer commands (`volup`, `voldn`, `mute`, `brightup`, `brightdn`, `media_playpause`, `media_next`, `media_prev`, `media_stop`, `media_play`, `media_pause`, `sys_sleep`), constructors and logic keywords (`rgb`, `pixelrect`, `row`, `all`, `any`), or any key name or alias in the §5.1 inventory (e.g., `enter`, `esc`, `ctrl`, `return`, `win`, `kp_0`, …).
  5. **Arity Matching**: Invocations must supply exactly `argc` separate argument brace groups matching the definition; arity mismatches trigger static compile errors.
- **Parameter Substitution Rules**:
  - `#` followed by a non-digit, non-`#` character (including `#0`) is literal text. `#n` with `n > argc` is a compile error.
  - Substitution is byte-level and precedes scanning: an argument containing the literal sequence `\endtext{}` terminates an enclosing `\text{}` block early — parameter immunity covers braces, brackets, and backslashes, not the terminator itself (see [§5.6](#56-verbatim-text-blocks-text--endtext)). Substituted argument text is never rescanned for `#`.

---

## 8. Conditional Control & Guarded Execution (`\if`, `\guarded`)

### 8.1 Predicate Grammar (EBNF)

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
  - **Nesting Spam Cap**: Logic blocks nest at most `MAX_PRED_NEST = 4` deep. (Nesting emits no bytecode, so no output-side bound would otherwise constrain it. Unary chains like `\any{\all{\any{P}}}` add no logic, and depth-2 normal forms (CNF/DNF) already express every boolean function — this caps garbage, not expressiveness.)
- **Structural Isomorphism Law**: The first brace group of `\if` or `\guarded` is `{ predicate }`. When containing an atomic triplet, it forms the standard dual-brace structure: `\if{ {lhs, op, rhs} }{ ... }`.
- **Handle Comparison Semantics**: Resource handles have no comparison semantics. A triplet receiving a handle operand (e.g., `\if{ {$h, =, 0} }` where `$h` holds a handle) is a **runtime type fault**: the predicate evaluates to FALSE and the VM sets `$err = ERR_HANDLE_MISUSE` (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)). Where a handle type is syntactically evident, the compiler MAY reject the comparison at compile time. To verify call success, scripts inspect the error channel: `\if{ {$err, !=, 0} }`.

### 8.2 Conditional Branch Instruction (`\if`)

> [!NOTE]
> **v4 removes `\ifnum` outright — there is no compatibility mapping.** Legacy v3 scripts containing `\ifnum` fail loudly at compile time (unknown command), deliberately surfacing every unmigrated site.

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
- **Forward-Only Progression**: Control flow within `\if` branches progress strictly forward ($\text{target} > \text{pc}$); backward jumps are impossible. See [vm.md §5](vm.md#5-predicate-lowering--op_bra-offset-semantics) for bytecode branch lowering.

#### Canonical Error-Checking Idiom with `$err`
Because handles cannot be compared against scalar values, checking whether an extension call succeeded uses the dedicated `$err` channel:
```dsl
\call{img.snap}{rect}
\if{ {$err, !=, 0} }{
    \# Handle capture failure or provider timeout
    \media_stop
}{
    \set{snap}{$ret}
    \# Proceed with valid snapshot handle
}
```

### 8.3 State-Guarded Execution (`\guarded`)

```ebnf
GuardedStmt    ::= "\guarded" [ "[" StaticDuration "]" ] "{" GuardPredicate "}" CodeBlock [ CodeBlock ]
GuardPredicate ::= Triplet
                 | "\all" "{" Triplet ("," Triplet)* "}"
                 | "\any" "{" Triplet ("," Triplet)* "}"
StaticDuration ::= DimLiteral | DecLiteral
```

```dsl
\guarded[throttle_ms]{ predicate }{ protected_body }
\guarded[throttle_ms]{ predicate }{ protected_body }{ on_abort }
```
- `[throttle_ms]` (optional decorator): **Static literal** minimum polling interval (`DimLiteral` or `DecLiteral` only, $0 \le throttle\_ms \le 65535\text{ ms}$, `u16`; defaults to `0ms`, continuous checks at instruction boundaries and delay slices). Dynamic references are rejected at compile time — the `GUARD_ENTER` descriptor encodes a fixed `u16` field.
- `predicate`: Invariant predicate asserted continuously throughout the execution of `protected_body`.
- **Flat Invariant Constraint**: The invariant predicate inside `\guarded` must be flat: either a single atomic triplet or a single-layer `\all` or `\any` containing between 1 and 8 triplets ($1 \le \text{count} \le 8$). Nested logical expressions (such as `\all{ \any{...} }`) are strictly prohibited in `\guarded` and trigger a compile error (`guarded does not support nested predicate logic; max 8 flat triplets`).
- `protected_body`: Executable Code Block executed under invariant protection.
- `on_abort` (optional): Bare `{}` trailing block executed upon guard failure. Omissible as a whole.
- **Fail-Safe Peripheral Release**: Upon guard failure, all physically held keys and mouse buttons are released immediately before entering `on_abort` (or exiting).
- **Scope Unwinding**: If an invariant is violated, execution of `protected_body` terminates immediately. Active loop frames within the aborted scope are rolled back while outer scope state is preserved, and control transfers to `on_abort` (or the end of the guarded block).
- **Nesting Ceiling**: Verified statically; maximum nesting depth is `MAX_GUARD_NEST = 4`.
- **Bytecode & Execution Model**: See [vm.md §4.2](vm.md#42-guarded-block-bytecode-lowering-pattern) and [vm.md §6](vm.md#6-flat-iterative-guard-model) for guard framing opcodes and the flat iterative monitoring model.

---

## 9. Signals, Slots & Runtime Extensions

MYCRO communicates with the external host environment through a strictly partitioned bidirectional model:
$$\begin{aligned}
\text{World-Initiated External Events} &\quad\longrightarrow\quad \text{Signals} \;\longrightarrow\; \text{Slots} \;\longrightarrow\; \text{\texttt{\textbackslash wait} Synchronization} \\
\text{Macro-Initiated Request / Response} &\quad\longrightarrow\quad \text{\texttt{\textbackslash use} Dependencies} \;\longrightarrow\; \text{\texttt{\textbackslash call} Extension Functions}
\end{aligned}$$

- **The Direction Invariant**: Macro-initiated requests MUST use `\call`; world-initiated events MUST use signals and slots. Slots are state receptors, never a return channel for `\call`.

### 9.1 The Signal / Slot Orthogonal Architecture

```ebnf
SlotRef    ::= "$" "slot." SignalName "." FieldName
             | "${" "slot." SignalName "." FieldName "}"
SignalName ::= Identifier ("." Identifier)*
FieldName  ::= Identifier
Identifier ::= [a-zA-Z] [a-zA-Z0-9_]*
```

Slots bridge the synchronous macro execution engine with the asynchronous external world:
- **Projector vs. Receptor Orthogonality**:
  - **Signals** are pure external projection channels (**Name category**, e.g., `net.msg`, `img.frame`). They are triggered exclusively by the outside world, maintain no persistent storage, cannot be read, and cannot be awaited directly.
  - **Slots** are internal state receptors (**Value category**, mandatory prefix `$slot.`, e.g., `$slot.net.msg.val`). They hold persistent scalar payloads and arrival latches.
- **No-Shorthand Law**: Slot field names MUST be written explicitly in source code (e.g., `$slot.net.msg.val`). An omitted form such as `$slot.<sig>` is forbidden; multi-level identifiers split deterministically at the LAST dot (e.g., `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`).
- **Default Signal-to-Slot Projection**: When an external signal `<sig>` is emitted without specific sub-fields (such as a single scalar or notification), it is uniformly projected into `$slot.<sig>.val`.
- **Event Latch Storage Model**: Each `(signal, field)` slot maintains:
  - `val`: Latest scalar payload (`i32`, initialized to `0`).
  - `pending`: Unconsumed event arrival flag (`i32` scalar flag: `1` = event pending, `0` = consumed/idle; initialized to `0`). External signal projections atomically set `pending = 1` and wake waiting coroutines. Slots maintain a coalescing latest-value latch; rapid unconsumed projections overwrite `val` while keeping `pending = 1`.

### 9.2 Asynchronous Event Synchronization (`\wait`)

```ebnf
WaitStmt    ::= "\wait" [ "[" WaitTimeout "]" ] "{" SlotRef "}"
WaitTimeout ::= [ "-" ] ( DimLiteral | DecLiteral ) | ScalarRef
```

```dsl
\wait{ $slot.sig.field }
\wait[timeout]{ $slot.sig.field }
```

- **Receptor Target Invariant ("The Sole Waitable Receptor Law")**: The target argument to `\wait` MUST be a receptor slot (`SlotRef`, e.g., `$slot.<sig>.<field>`). User variables (`$var`), system read-only variables (`$ret`, `$timeout`, `$err`), and numeric literals are statically rejected with a compile error. In MYCRO, user variables represent private synchronous state; external asynchronous synchronization is mediated exclusively through `$slot.` receptors.
- **Suspension & Latch Consumption**: `\wait[timeout]{ $slot.<sig>.<field> }` synchronizes with external signal events via the receptor slot's arrival latch:
  - If `slot.pending == 1` upon entry (prior projection unconsumed), the wait consumes the event immediately, sets `pending = 0`, and resumes execution with zero delay.
  - If `slot.pending == 0`, execution suspends until a new external projection arrives (setting `pending = 1`) or until `timeout` expires.
- **Timeout Domain & Immediate Polling**: The timeout duration in brackets `[timeout]` belongs to the signed 32-bit integer domain ($[-2147483648, 2147483647]$):
  - `timeout > 0`: Bounded wait up to `timeout` milliseconds.
  - `timeout <= 0`: **Non-blocking immediate poll** (evaluates `slot.pending`; if `1`, consumes and writes `$timeout = 0`; else immediately writes `$timeout = 1`). This applies uniformly to literals and dynamic values — a negative literal such as `\wait[-1]{...}` is a legal immediate poll, deliberately enabling countdown idioms (e.g. `\wait[$deadline_rem]{...}` after repeated `\sub`) without guard clauses.
  - Omitted `[timeout]`: Automatically emitted by the compiler as $2147483647\text{ ms}$ ($2^{31}-1$, $\approx 24.8$ days). Indefinite wait without a timeout is strictly prohibited to guarantee halting decidability.
- **Status Reporting**: Writes `1` to `$timeout` if the wait timed out, or `0` if an external projection arrived or a pending event was consumed.

#### Event Polling Loop Idiom
```dsl
\guarded{ {$err, =, 0} }{
    \rep{1000}{
        \wait[100ms]{ $slot.net.msg.val }
        \if{ {$timeout, =, 0} }{
            \# Event received and consumed!
            \typenum{$slot.net.msg.val}
            \enter
        }
    }
}
```

### 9.3 Runtime Extension Calls (`\use`, `\call`)

```ebnf
UseStmt     ::= "\use" "{" Namespace "}"
CallStmt    ::= "\call" "{" ExtensionFn "}" ArgGroup*
ExtensionFn ::= Namespace "." Identifier
Namespace   ::= Identifier
Name        ::= Identifier ("." Identifier)*
ArgGroup    ::= "{" Value "}" | "{" Value ("," Value)* "}" | "{" Name "}"
```

- **`\use{ns}`**: Namespace activation directive. Valid at top level only, idempotent, and order-sensitive: every `\call`, signal, or `$slot` reference to a namespace requires a preceding `\use{ns}` — **no exemptions**: even the transport-plane `net` namespace (`net.send`, `net.msg`) must be declared. The transport being always online is irrelevant; what `\use` activates is the DSL-visible exposure of the namespace (its callable callbacks and signal projection wiring), which is a real registration act for every namespace. Unknown namespaces and references to known-but-undeclared namespaces raise compile errors. The on-device compiler performs activation at compile time, so `\use` emits no bytecode.
- **`\call{ns.fn}{arg}...`**: Invokes a registered runtime extension function.
- **Argument Lowering & Flattening**: Each argument brace group following `{ns.fn}` is flattened into positional `typed(T)` bytecode operands according to the target function's registered signature. For example, `\call{img.watch}{name}{x, y}{interval, duration}` lowers to a single `0x2A CALL_EXT` instruction with `argc = 5` operands (name + two coordinates + two timings). The `u8 ns, u8 fn` fields of `CALL_EXT` are string-table indices of the namespace and function names (self-describing; no separate registry numbering exists).
- **Label Preservation**: Name-category arguments (such as `net.send`'s label) preserve exact casing and are emitted as string-table index operands (`TAG_NAME = 2`).
- **Two-Channel Results**: Callee writes return value or Handle ID to `$ret` (`0xFF`, evaluates to `0` on void or failure) and error code to `$err` (`0xFD`, `0` on success, non-zero on failure).
- Registered namespaces (`sys`, `net`, `img`), signatures, and Handle ownership semantics are normative in [runtime.md](runtime.md) (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

---

## 10. Boundaries and Version History

### Deliberate Boundaries
- Numbers are `i32` only.
- Strings are not values anywhere in the language. Literal text has exactly two fates: keystrokes in text positions, and Name-category identifiers resolved to string-table indices. Runtime data enters as Handle resources.
- Resource handles have no arithmetic or comparison semantics; handle misuse is a runtime type fault reported via `$err` (`ERR_HANDLE_MISUSE`), and call success/failure is inspected via `$err`.
- No arithmetic expression trees or operator precedence; arithmetic is statement-based via `\set`, `\add`, `\sub`.
- No backward branches or `\while` loops; iteration is strictly bounded via `\rep{N}`.
- Single delay durations bounded by `u16` ($0..65535\text{ ms}$).
- Infinite wait without timeout is prohibited; all waits enforce an upper bound $\le 2147483647\text{ ms}$.
- `\pixelrect` dimensions bounded by `RECT_MAX_EDGE = 128` pixels ($48\text{ KiB}$).

### Version History

| Version | Key Additions and Changes |
|---|---|
| **v1** (gist era) | Text typing, basic key symbols, escape characters. |
| **v2** | Mouse controls (`\click`, `\move`, `\mdown`, `\mup`), `\delay`/`\pace` split, `\rep`, `\kdown`/`\kup`, line continuations, comments. |
| **v3** | User macros `\def`; variables/`\set`/`\add`; `$ret`/`$timeout`; signal slots; `\ifnum`; `\typenum{$var}`; `\use`/`\call`/`\wait`; Name vs Value partition; signature typing and Handle ownership; decoupled compile-time expansion (`MAX_MACRO_EXPAND=16`) and runtime nesting (`MAX_RUNTIME_NEST=8`). |
| **v4** | Preamble layout modes (`\#!syntax: free` vs default `typewriter`); verbatim text blocks `\text{}`…`\endtext{}`; unified `i32` numeric folding with `ms`/`s` time literals; `u16 ms` physical delay clamping and eradication of silent truncation; Binary Brace Taxonomy & Structural Exclusivity Law; signal/slot orthogonal reset: bare Name projectors vs `$slot.<sig>.<field>` stateful receptors with coalescing latest-value latches; signed `i32` wait timeout domain with non-blocking immediate polling ($\le 0$, literals and dynamic values alike); dedicated `$err` (`0xFD`) error register separated from `$ret` (`0xFF`); handle type-fault discipline (runtime `$err` reporting); structured first-class predicates (`\if` with bare `{}` else; `\ifnum` **removed outright** — unknown command, compile error; logic-block nesting capped at `MAX_PRED_NEST = 4`); VM-applied global pacing after every peripheral action (combination sequences paced per constituent action); lone printable-character keys lower to a single `CHAR` action; continuous flat invariant guarding (`\guarded[throttle]` with delay-slicing and fail-safe release; nested logic forbidden); subtraction `\sub`; compile-time color packing `\rgb`; `\pixelrect` static pixel matrices; open-world Name labels for `net.send`; Authoritative Profile Constants Table integration. |

