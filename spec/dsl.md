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
- Variable capacity profile: Up to 32 distinct user variables per macro (mapped to `MAX_REGS = 32` scalar `i32` registers; see [vm.md §2](vm.md#2-register-file)).
- **Deterministic Key State**: Upon normal completion of a code block, physical peripheral state is governed entirely by emitted instructions. Explicitly depressed keys remain physically held until an explicit release instruction (`\kup`, `\mup`) or an emergency fail-safe release (macro termination or guard abort).
- **Deterministic Resource Reclamation**: Managed Handle resources (such as capture buffers) are deterministically reclaimed upon macro exit (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

---

## 2. Lexical Schema & Primitive Tokens

The MYCRO DSL character stream is partitioned into seven closed, deterministic lexical primitive schemas:

1. **Macro Commands (`\cmd`)**: Backslash `\` followed by an alphanumeric identifier sequence (e.g., `\delay`, `\rep`, `\enter`, `\ctrl+c`, user-defined macro `\foo`). Parsed by the compiler to emit corresponding bytecode instructions or trigger compile-time macro expansion.
2. **Variable Sigil (`$`)**: Dollar sign `$` followed by an identifier reference (`$var`, `$ret`, `$timeout`, `$slot.<sig>.<field>`). Denotes dynamic value evaluation. Categorized into user variables (`$var`), system read-only variables (`$ret`, `$timeout`), and **stateful receptor slots (mandatory-prefixed with `$slot.`**, e.g., `$slot.<sig>.<field>`).
3. **Numeric Literals**: Decimal integers (`120`, `-15`), hexadecimal constants (`0xFF`, `0xFF00FF`), and dimensioned time literals (`500ms`, `0.5s`). Static numeric values folded in-place during lexing into unified `i32` integer scalars (see [§2.1](#21-numeric-representation--integer-folding)).
4. **Brace Pairs (`{}`)**: Balanced enclosing pairs `{ ... }`. Syntactic scope delimiters, classified as either an Executable Code Block or an Argument Group based on host command signature (see [§4](#4-document-grammar--the-binary-brace-taxonomy)).
5. **Bracket Pairs (`[]`)**: Balanced enclosing pairs `[ ... ]`. Optional configuration modifiers or decorators (e.g., `\def{name}[argc]{body}`, `\guarded[throttle]{...}`, `\wait[timeout]{...}`).
6. **Literal Keystroke Text**: Raw printable ASCII characters (codes 32–126) located in executable text positions. Emitted as physical keystrokes targeting the host operating system.
7. **Comments and Shebang Directives (`\#`, `\#!`)**:
   - Standard comments begin with `\#` and span to the end of the line; silently absorbed by the lexical scanner.
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
| **Second Time Dimension** | `0.5s`, `1.2s`, `5s` | $f(\text{num}) = \lfloor \text{float}(\text{num}) \times 1000 + 0.5 \rfloor$ | `500`, `1200`, `5000` | Delays, guard throttling, timeout values |

#### Folding Invariants & Boundary Constraints
1. **Unified Integer Domain & Overflow Checking**: Numeric literals and folded constants evaluate to mathematical integers. The evaluated scalar must fall strictly within the signed 32-bit range $[-2^{31}, 2^{31} - 1]$. Values outside this range constitute an arithmetic overflow and trigger an immediate static compilation error.
2. **Mandatory Suffix for Fractional Quantities**: Any literal containing a decimal point MUST explicitly carry the `s` dimensional suffix (e.g., `0.5s`). Dimensionless values must be pure integers.
3. **Rounding Semantics**: Sub-millisecond fractions are rounded using half-up rounding (e.g., `0.0004s` folds to `0ms`, `0.0006s` folds to `1ms`).
4. **Physical Range Assertions (Decoupled from Lexing)**: The lexical scanner unifies all numeric and time literals into signed `i32` mathematical scalars. The validation of whether an immediate value satisfies the physical width of its target VM opcode is decoupled from lexing and enforced as **Physical Range Assertions**, normatively specified in **[§6.6 (Physical Word-Length Contracts)](#66-physical-word-length-contracts)** and each command's dedicated section. Folded values exceeding target opcode bounds (e.g., `\delay{70s}` exceeding `u16`, `\rep{300}` exceeding `u8`) trigger immediate static compilation errors.

---

## 3. Preamble Directives & Document Layout Modes (`\#!syntax`)

Preamble directives configure the compiler's lexical scanner behavior prior to code execution.

### 3.1 Formal Grammar (EBNF)

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

### 3.2 Layout Modes and Equivalences

| Declaration Form | Internal Equivalent | Scanner Behavior | Target Use Case |
|---|---|---|---|
| `\#!syntax: free` | `newline=ignore, space=ignore` | **Free Layout Mode (explicitly activated)**: Unescaped newlines and spaces are silently absorbed; explicit blanks emitted via `\enter`, `\space`, `\tab`; whitespace-bearing text via verbatim text blocks `\text{}`…`\endtext{}` (see [§5.6](#56-verbatim-text-blocks-text--endtext)). Canonical recognition marker for v4 structured scripts. | Structured macro scripts (loops, branches, user macros, indentation). |
| `\#!syntax: typewriter` | `newline=emit, space=emit` | **Typewriter Mode (default)**: Raw newlines emit `Enter`; raw spaces emit `Space`. Scripts without preamble behave byte-identically to v3. | Raw text typing, automated form filling, legacy v3 scripts. |
| `\#!syntax: newline=ignore, space=emit` | Fine-Grained Config | **Orthogonal Hybrid**: Newlines allow indentation; spaces emit `Space`. | Indented scripts requiring frequent inline whitespace typing. |

- **Preamble Scope**: The directive must appear in the leading comment lines of the script, before any non-comment, non-whitespace executable character. Once the first code token is encountered, configuration is permanently frozen.
- **Comment Orthogonality Law**:
  - `\# <text>`: Standard human-readable comments; silently ignored.
  - `\#!<directive>`: Compiler preamble directive; processed exclusively at the head of the file.

### 3.3 Lexer Scanning State Machine

| Input Stream Character | Context | `free` Mode (`\#!syntax: free`) | `typewriter` Mode (default) |
|---|---|---|---|
| Newline `\n` / `\r\n` | Text Position | **Silently Absorbed** | **Emits Enter Key** (`0x02 KEY enter`) |
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

\# 3. Default mode (no preamble; byte-identical to v3: newlines emit Enter, spaces emit Space)
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
KeyName      ::= [a-zA-Z0-9_]+
```

#### Keyboard Inventory Table (USB HID Page 0x07)
All keys in this inventory belong to standard USB HID Usage Page 0x07 (Keyboard/Keypad) and transmit standard 1-byte usage IDs within the keyboard report:

| Category | Canonical Identifiers | Aliases | Usage ID | Semantics / Target Action |
|---|---|---|---|---|
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
- **Sequential Lowering & Symmetric LIFO Envelope**:
  A combination $T_1 + T_2 + \dots + T_n$ maps each constituent token $T_i$ to its physical keycode sequence $K(T_i)$ (e.g., `a` $\rightarrow [a]$, `A` $\rightarrow [\text{shift}, a]$, `plus` $\rightarrow [\text{shift}, \text{equal}]$). The compiler concatenates these sequences linearly into $\mathbf{K} = K(T_1) \mathbin{\Vert} \dots \mathbin{\Vert} K(T_n)$ and emits opcodes strictly according to user-specified order:
  1. **Sequential Depression**: Emits `KEY_DOWN` forward for each key in $\mathbf{K}$.
  2. **Pulse Hold**: Emits `SLEEP click_hold_ms` (default 10ms).
  3. **LIFO Release**: Emits `KEY_UP` in reverse order for each key in $\operatorname{reversed}(\mathbf{K})$.
  4. **Inter-Action Pacing**: Emits active pacing delay (`pace_ms`).
- **Key Hold & Release (`\kdown`, `\kup`)**:
  - `\kdown{combo}`: Emits forward `KEY_DOWN` sequence across $\mathbf{K}$ without auto-release.
  - `\kup{combo}`: Emits reverse `KEY_UP` sequence across $\operatorname{reversed}(\mathbf{K})$.
  - `\kup{0}`: Failsafe releasing all physically held keys on the device.
- **Extreme Boundary Lowering (`\a+A`)**:
  Tokens are lowered mechanically without heuristic intervention or duplication pruning:
  `\a+A` expands to $\mathbf{K} = [a, \text{shift}, a]$ and lowers strictly to:
  $$\text{KEY\_DOWN } a \to \text{KEY\_DOWN } \text{shift} \to \text{KEY\_DOWN } a \to \text{SLEEP } \text{hold} \to \text{KEY\_UP } a \to \text{KEY\_UP } \text{shift} \to \text{KEY\_UP } a$$
  The physical outcome of redundant or collision-bearing physical sequences is governed directly by host peripheral runtime behavior rather than restricted by static compiler checks.

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
Duration  ::= DimLiteral | DecLiteral | "$" VarName
```

1. **Direct Expression & In-Place Folding**: Time values are expressed via dimensioned literals (`50ms`, `0.5s`) or dimensionless integers (ms), folded at compile time into integer millisecond scalars (see [§2.1](#21-numeric-representation--integer-folding)).
2. **Physical u16 Constraints**: Underlying timing opcodes strictly retain `u16 ms` ($0 \le t \le 65535$). Static folded constants $> 65535\text{ ms}$ or $< 0$ trigger static compile errors; longer durations must use bounded loops (e.g., `\rep{100}{ \delay{1s} }`).
3. **Dynamic Variable Range Handling**: When a timing command consumes a variable (e.g., `\delay{$d}`), the runtime **clamps** the value to $[0, 65535]$. Bitwise masking is forbidden.
4. **Semantics**:
   - `\delay{t}`: Sleep for duration `t` ($0 \le t \le 65535\text{ ms}$).
   - `\pace{t}`: Configure inter-keystroke pacing interval ($0 \le t \le 65535\text{ ms}$).

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
- **Termination & Lexical Protection**:
  - The scanner searches the payload strictly for the literal sequence `\endtext{}`. This sequence is the payload's only reserved sequence; exhausting the character stream without it raises an `Unterminated text block` compile error.
  - User macros cannot be named `text` or `endtext`.
- **Full Immunity in `\def`**: Parameter text inside `\def{g}[1]{\text{}#1\endtext{}}` passes directly into the payload without brace parsing, allowing parameter payloads to contain unescaped braces, brackets, or backslashes.

---

## 6. Values, Variables & State Model

When macros require dynamic parameters, counters, or coordination, MYCRO provides a scalar variable system and parameter calling contracts.

### 6.1 Variables & Storage Model

- **User Variables**: A macro script supports up to `MAX_VARS = 32` distinct user variables (corresponding to the VM register profile; see [vm.md §2](vm.md#2-register-file)). Each variable holds a signed two's complement **i32** scalar ($[-2^{31}, 2^{31} - 1]$) or an opaque resource handle. All variables are initialized to zero at execution start.
- **Implicit Declaration**: Variables are declared implicitly upon their first write via `\set{name}{val}`. Variable identifiers are strictly case-sensitive, begin with an ASCII letter, and match `[a-zA-Z][a-zA-Z0-9_]*`.
- **Reading Variables (`$var`)**: In Value positions, reading a variable strictly requires the `$` sigil (e.g., `$x`, `${x}`). Reading an unassigned variable triggers a static compile error (`Undefined variable: {name}`).

### 6.2 Variable Assignment & Arithmetic (`\set`, `\add`, `\sub`)

MYCRO intentionally omits infix arithmetic expressions (e.g., `x = a + b`); variable mutations are expressed exclusively through three statement commands:

```ebnf
SetStmt ::= "\set" "{" VarName "}" "{" Value "}"
AddStmt ::= "\add" "{" VarName "}" "{" Value "}"
SubStmt ::= "\sub" "{" VarName "}" "{" Value "}"
VarName ::= [a-zA-Z] [a-zA-Z0-9_]*
```

- **`\set{var}{val}`**: Assigns `val` to variable `var`, replacing any previous value.
- **`\add{var}{val}`**: Adds `val` to variable `var` in-place, with signed 32-bit two's complement modular wraparound ($[-2^{31}, 2^{31} - 1]$).
- **`\sub{var}{val}`**: Subtracts `val` from variable `var` in-place, with signed 32-bit two's complement modular wraparound ($[-2^{31}, 2^{31} - 1]$).
- **Handle Type Restrictions**: Opaque resource handles (such as snapshot handles returned by `\call`) cannot participate in arithmetic; passing a handle to `\add` or `\sub` raises a static compile error. Underlying handle lifetimes and resource reclamation are managed automatically by the runtime environment (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

### 6.3 System Read-Only Variables & Receptor Slots

In addition to user variables (`$var`), MYCRO provides two specialized categories of read-only variables:

- **System Status Variables**: Dedicated registers reserved for execution status:
  - **`$ret`**: Holds the scalar return value or resource Handle from the most recent extension call (`\call`). Overwritten on each call; evaluates to `0` if the callee returns None (see [§9.3](#93-runtime-extension-calls-use-call)).
  - **`$timeout`**: Holds the completion status of the most recent receptor wait (`\wait`). Evaluates to `1` if the wait timed out, or `0` if an external projection arrived (see [§9.2](#92-asynchronous-event-synchronization-wait)).
- **Asynchronous Receptor Slots (`$slot.<sig>.<field>`)**: Variables prefixed with `$slot.` act as stateful event latches reflecting external signals (such as host network packets or vision telemetry). They can be evaluated as dynamic values or waited on via `\wait`. The complete Signal/Slot orthogonal architecture and synchronization contracts are normatively specified in **[§9.1](#91-the-signal--slot-orthogonal-architecture)**.

### 6.4 Value Constructors & Keystroke Formatting

```ebnf
RgbLit  ::= "\rgb" "{" Value "," Value "," Value "}"
```

- **Compile-Time Color Packing (`\rgb`)**: `\rgb{r, g, b}` packs three 0–255 integer constants ($u8$) into a single 24-bit RGB scalar immediate: `(r << 16) | (g << 8) | b`.
- **Keystroke Number Formatting (`\typenum`)**: `\typenum{$var}` renders the decimal integer value stored in `$var` as ASCII keystrokes with active pacing. The argument must be a variable reference; resource Handles are rejected with a static diagnostic.
- **Pixel Rectangle Constants (`\rect`)**: `\rect{\row{0xRRGGBB,...},...}` is a compile-time pixel constant preprocessed into the runtime const store and materialized as a snap Handle via `sys.const`.

### 6.5 Parameter Categories & Calling Contracts

Every command argument position belongs to one of three orthogonal categories based on signature contracts:

| Parameter Category | Syntactic Form & Evaluation Rules | Compilation & Runtime Mapping |
|---|---|---|
| **Value Category (Value)** | Dynamic variables, numeric immediates, or compile-time constructors:<br>• Variables: User variables (`$var`), system read-only variables (`$ret`, `$timeout`), receptor slots (`$slot.<sig>.<field>`);<br>• Static literals: Decimal, hex, or time literals (`500ms`, `0.5s`), folded at lex time to `i32`;<br>• Constructors: Packed RGB color `\rgb{r, g, b}`, pixel rectangle `\rect{...}`. | **Physical 3-Tag Closed Operand Architecture**:<br>• Immediate literal $\rightarrow$ `TAG_IMM (0)` (`i32 LE`);<br>• Register $\rightarrow$ `TAG_REG (1)` (`u8 reg`);<br>• Static string $\rightarrow$ `TAG_STR (2)` (`u8 str_idx`, `\call` only);<br>• **Receptor slots (`$slot...`)** $\rightarrow$ Pre-instruction desugaring: compiler emits `0x28 OP_READSLOT` before host instruction to load slot into an ephemeral register, passing `TAG_REG`. In `\guarded` descriptors, slots are encoded natively via the `SLOT` descriptor tag. |
| **Name Category (Name)** | Static symbolic identifiers: Key names, mouse buttons, namespaces, signal channel names, or variable names. Bare identifiers, strictly prohibiting `$`. | Statically resolved at compile time to usage IDs or string indices (`u8`). |
| **Predicate Category (Predicate)** | Conditional decision structures: Atomic triplets `{lhs, op, rhs}` or composite logic trees `\all` / `\any` (see [§8](#8-conditional-control--guarded-execution-if-guarded)). | Directly lowered by the compiler into forward short-circuit jump sequences (`OP_BRA`). |

### 6.6 Physical Word-Length Contracts

Every macro instruction consuming numeric immediates enforces strict physical word-length and range bounds corresponding to its target VM opcode. Values violating these intervals trigger immediate static compilation diagnostics:

| Target Instruction | Opcode Parameter Width | Valid Immediate Interval | Normative Definition & Reference |
|---|---|---|---|
| `\delay{t}`, `\pace{t}` | `u16` (2 bytes LE) | $[0, 65535]\text{ ms}$ | Timing base. Durations $> 65535\text{ ms}$ must be expressed via `\rep` loops (see [§5.4](#54-timing-delays--inter-keystroke-pacing), [§5.5](#55-bounded-counting-loops-rep)). |
| `\rep{N}` | `u8` (1 byte) | $[0, 255]$ | Loop counter ceiling (`MacroError: rep count out of u8 range`; see [§5.5](#55-bounded-counting-loops-rep)). |
| `\click{btn, count}` | `u16` (2 bytes LE) | $[1, 65535]$ | Click repeat count (defaults to 1; see [§5.3](#53-mouse-operations)). |
| `\move{dx, dy}` | `i16` (2 bytes LE) | $[-32768, 32767]$ | Relative mouse movement offsets (see [§5.3](#53-mouse-operations)). |
| `\wait[timeout]{ $slot... }` | `u32` (4 bytes LE) | $[0, 4294967295]\text{ ms}$ | Timeout duration (0 or omitted = indefinite; see [§9.2](#92-asynchronous-event-synchronization-wait)). |
| `\guarded[throttle_ms]` | `u16` (2 bytes LE) | $[0, 65535]\text{ ms}$ | Minimum polling interval (defaults to 0 = continuous; see [§8.3](#83-state-guarded-execution-guarded)). |
| `\rgb{r, g, b}` | `u8` $\times 3$ (1 byte each) | $[0, 255]$ each | Red, green, blue color channels (see [§6.4](#64-value-constructors--keystroke-formatting)). |
| `\set`, `\add`, `\sub` | `i32` (4 bytes LE) | $[-2^{31}, 2^{31} - 1]$ | Signed 32-bit integer scalar with modular arithmetic (see [§6.2](#62-variable-assignment--arithmetic-set-add-sub)). |

### 6.7 Static Compile Diagnostics

- **Character Set & Delimiters**: Non-printable ASCII (outside 32–126) and unmatched delimiters (`{`, `}`, `[`, `]`, `${`) raise compile errors.
- **Name vs Value Partition & Identifiers**: Name positions strictly forbid `$`. Variable and identifier names are case-sensitive and must match `[a-zA-Z][a-zA-Z0-9_]*`; reserved names (`ret`, `timeout`) cannot be used as variable names. Undefined variables, unknown keys, unknown mouse buttons, unknown namespaces, and unknown signals raise compile errors.
- **Eradication of Silent Truncation**: Immediate values outside valid physical opcode ranges halt compilation immediately.
- **Preamble Directive Ordering**: `\#!syntax:` directives must appear at the head of the file before any executable action token.
- **Predicate Triplet Structure**: Predicate arguments must be formatted as dual-brace triplets `{ {lhs, op, rhs} }` with operators in `{=, !=, <, <=, >, >=}`.
- **Receptor Slot Wait Target**: The argument to `\wait` must be a `$slot.<sig>.<field>` receptor. Passing user variables, system variables, or literals raises a compile error (see [§9.2](#92-asynchronous-event-synchronization-wait)).
- **Guard Nesting Limit**: `\guarded` nesting cannot exceed `MAX_GUARD_NEST = 4`.

---

## 7. Compile-Time Text Macros (`\def`)

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
- **Structural Isomorphism Law**: The first brace group of `\if` or `\guarded` is `{ predicate }`. When containing an atomic triplet, it forms the standard dual-brace structure: `\if{ {lhs, op, rhs} }{ ... }`.

### 8.2 Conditional Branch Instruction (`\if`)

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

### 8.3 State-Guarded Execution (`\guarded`)

```ebnf
GuardedStmt ::= "\guarded" [ "[" Duration "]" ] "{" Predicate "}" CodeBlock [ CodeBlock ]
```

```dsl
\guarded[throttle_ms]{ predicate }{ protected_body }
\guarded[throttle_ms]{ predicate }{ protected_body }{ on_abort }
```
- `[throttle_ms]` (optional decorator): Value category minimum polling interval ($0 \le throttle\_ms \le 65535\text{ ms}$, `u16`; defaults to `0ms`, continuous checks at instruction boundaries and delay slices).
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

### 8.4 Predicate Lowering Algorithm (Short-Circuit Forward Branches)

Predicate trees lower directly into `OP_BRA` branch sequences via syntax-directed short-circuit translation:
1. **Conjunction Node `\all{ P_1, ..., P_N }`**: The first $N-1$ child tests branch to `false_target` on false and fall through to $P_{i+1}$ on true. The final test inherits the parent targets.
2. **Disjunction Node `\any{ P_1, ..., P_N }`**: The first $N-1$ child tests branch to `true_target` on true (emitting inverted `cc` under Branch-on-False semantics) and fall through to $P_{i+1}$ on false. The final test branches to `false_target` on false.
3. **Atomic Leaf Emission & Commutative Normalization**:
   - If `lhs` is immediate and `rhs` is variable, operands are swapped and relational operator is inverted.
   - If an operand references `$slot.<sig>.<field>`, an `OP_READSLOT` is emitted to an ephemeral register.
   - If both operands are immediate constants, the condition is evaluated statically (dead branches eliminated).

---

## 9. Signals, Slots & Runtime Extensions

MYCRO communicates with the external host environment through a strictly partitioned bidirectional model:
$$\begin{aligned}
\text{Macro-Initiated Request / Response} &\quad\longrightarrow\quad \text{\texttt{\textbackslash call} Extension Functions} \\
\text{World-Initiated External Events} &\quad\longrightarrow\quad \text{Signals} \;\longrightarrow\; \text{Slots} \;\longrightarrow\; \text{\texttt{\textbackslash wait} Synchronization}
\end{aligned}$$

### 9.1 The Signal / Slot Orthogonal Architecture

```ebnf
SlotRef    ::= "$slot." DottedName
DottedName ::= Identifier "." Identifier
Identifier ::= [a-zA-Z] [a-zA-Z0-9_]*
```

Slots bridge the synchronous macro execution engine with the asynchronous external world:
- **Projector vs. Receptor Orthogonality**:
  - **Signals** are pure external projection channels (**Name category**, e.g., `net.msg`, `img.frame`). They are triggered exclusively by the outside world, maintain no persistent storage, cannot be read, and cannot be awaited directly.
  - **Slots** are internal state receptors (**Value category**, mandatory prefix `$slot.`, e.g., `$slot.net.msg.val`). They hold persistent scalar payloads and arrival latches.
- **No-Shorthand Law**: Slot field names MUST be written explicitly in source code (e.g., `$slot.net.msg.val`). An omitted form such as `$slot.<sig>` is forbidden; multi-level identifiers split deterministically at the LAST dot (e.g., `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`).
- **Default Signal-to-Slot Projection**: When an external signal `<sig>` is emitted without specific sub-fields (such as a single scalar or notification), it is uniformly projected into `$slot.<sig>.val`.
- **Event Latch Storage Model**: Each `(signal, field)` slot maintains:
  - `val`: Latest scalar payload (`i32`, initialized to 0).
  - `pending`: Unconsumed event arrival flag (`bool`, initialized to `False`). External signal projections atomically set `pending = True` and wake waiting coroutines.
- **The Sole Waitable Receptor Law**: Receptor slots starting with `$slot.` are the ONLY valid asynchronous entities eligible for waiting (`\wait`). User variables (`$var`) represent private synchronous state; attempting to `\wait` on `$var` is statically rejected.

### 9.2 Asynchronous Event Synchronization (`\wait`)

```ebnf
WaitStmt ::= "\wait" [ "[" Duration "]" ] "{" SlotRef "}"
```

- **Suspension & Latch Consumption**: `\wait[timeout]{ $slot.<sig>.<field> }` suspends execution until the target receptor slot receives an external projection:
  - If `slot.pending == True` upon entry (prior projection unconsumed), consumes the event, clears `pending = False`, and resumes immediately with zero delay.
  - If `slot.pending == False`, suspends until a new projection arrives or until `timeout` expires.
- **Timeout Decorator**: Optional `timeout` duration in brackets `[timeout]` ($0 \le timeout \le 4294967295\text{ ms}$, `u32 LE`; 0 or omitted = indefinite wait).
- **Status Reporting**: Writes `1` to `$timeout` if the wait timed out, or `0` if an external projection arrived.

### 9.3 Runtime Extension Calls (`\use`, `\call`)

```ebnf
UseStmt  ::= "\use" "{" Namespace "}"
CallStmt ::= "\call" "{" ExtensionFn "}" ArgGroup*
```

- **`\use{ns}`**: Compile-time dependency declaration against the runtime namespace table. Unknown namespace raises a compile error. Emits no bytecode.
- **`\call{ns.fn}{arg}...`**: Invokes a registered runtime extension function. Arguments are validated against the registered signature. Return value or resource Handle is written to `$ret`.
- **Direction Invariant**: Macro-initiated requests MUST use `\call`; world-initiated events MUST use signals and slots. Slots are never a return channel for `\call`.
- Registered namespaces (`sys`, `net`, `img`), signatures, and Handle ownership semantics are normative in [runtime.md](runtime.md) (see [runtime.md §4](runtime.md#4-handle-lifecycle--ownership-management)).

---

## 10. Boundaries and Version History

### Deliberate Boundaries
- Numbers are `i32` only.
- Strings are not first-class values; string arguments exist only as compile-time string constants for extension calls. Runtime data enters as Handle resources.
- No arithmetic expression trees or operator precedence; arithmetic is statement-based via `\set`, `\add`, `\sub`.
- No backward branches or `\while` loops; iteration is strictly bounded via `\rep{N}`.
- Single delay durations bounded by `u16` ($0..65535\text{ ms}$).

### Version History

| Version | Key Additions and Changes |
|---|---|
| **v1** (gist era) | Text typing, basic key symbols, escape characters. |
| **v2** | Mouse controls (`\click`, `\move`, `\mdown`, `\mup`), `\delay`/`\pace` split, `\rep`, `\kdown`/`\kup`, line continuations, comments. |
| **v3** | User macros `\def`; variables/`\set`/`\add`; `$ret`/`$timeout`; signal slots; `\ifnum`; `\typenum{$var}`; `\use`/`\call`/`\wait`; Name vs Value partition; signature typing and Handle ownership; decoupled compile-time expansion (`MAX_MACRO_EXPAND=16`) and runtime nesting (`MAX_RUNTIME_NEST=8`). |
| **v4** | Preamble layout modes (`\#!syntax: free` vs default `typewriter`); verbatim text blocks `\text{}`…`\endtext{}`; unified `i32` numeric folding with `ms`/`s` time literals; `u16 ms` physical delay clamping and eradication of silent truncation; Binary Brace Taxonomy & Structural Exclusivity Law; signal/slot orthogonal reset: bare Name projectors vs `$slot.<sig>.<field>` stateful receptors with lossless event latches; `\wait[timeout]{$slot...}` leading bracket decorator; structured first-class predicates (`\if` with bare `{}` else; deprecating `\ifnum`); continuous invariant guarding (`\guarded[throttle]` with delay-slicing and fail-safe release); subtraction `\sub`; compile-time color packing `\rgb`. |
