# MYCRO DSL Specification

**Status: normative.** This document defines MYCRO DSL v4 surface syntax and source semantics. Bytecode and execution semantics are defined in [vm.md](vm.md); runtime services and extension namespaces are defined in [runtime.md](runtime.md).

### Table of Contents
- [§1. Execution and Compilation Model](#1-execution-and-compilation-model)
- [§2. Lexical Schema](#2-lexical-schema)
- [§3. Preamble and Layout Modes](#3-preamble-and-layout-modes)
- [§4. Document Grammar](#4-document-grammar)
- [§5. Base Instructions](#5-base-instructions)
- [§6. Values, Variables and State](#6-values-variables-and-state)
- [§7. Compile-Time Text Macros](#7-compile-time-text-macros-def)
- [§8. Predicates and Guarded Execution](#8-predicates-and-guarded-execution)
- [§9. Signals, Slots and Runtime Extensions](#9-signals-slots-and-runtime-extensions)
- [§10. Version History](#10-version-history)

---

## 1. Execution and Compilation Model

### 1.1 Streaming Compilation

The compiler is a single-pass streaming finite-state machine with direct bytecode emission. The input cursor moves forward, with bounded rescanning through the macro expansion stack. Parser control state uses bounded stacks governed by the profile nesting limits. Source text, macro definitions, output and string tables use storage proportional to input or output size. The opcode segment is bounded by `MAX_CODE_BYTES = 65535` bytes.

### 1.2 Execution Model

- Numeric mutation uses `\set`, `\add` and `\sub`; predicates use triplets and `\all`/`\any` blocks.
- Control flow consists of forward branches and bounded counting loops. `LOOP_END` supplies the loop-back edge.
- Every wait has a finite `i32` timeout. Extension completion and external cancellation follow [runtime.md §3.0](runtime.md#30-universal-return-and-error-contract).
- `MAX_RUNTIME_NEST = 8` bounds the combined lexical nesting of `\rep`, `\if` and `\guarded` after macro expansion.
- `MAX_MACRO_EXPAND = 16` bounds macro expansion depth.
- `MAX_GUARD_NEST = 4` bounds active guard nesting.
- `MAX_PRED_NEST = 4` bounds `\all`/`\any` nesting.
- `MAX_COMBO_KEYS = 8` bounds source key tokens in one combination.
- `MAX_SLOTS = 256` bounds distinct `(signal, field)` receptor slots.
- A script has at most 32 user variables, mapped one-to-one to the 32 general registers.
- `\pixelrect` width and height are at most `RECT_MAX_EDGE = 128` pixels.

Normal completion preserves keys and buttons held by explicit down instructions. Abnormal termination, external cancellation and guard failure release all keyboard keys, mouse buttons and any active Consumer Control usage. Managed Handles are reclaimed on every macro exit.

---

## 2. Lexical Schema

The source stream contains seven token classes:

1. **Commands**: `\` followed by `[a-zA-Z][a-zA-Z0-9_]*`. A following `+` continues a key combination under §5.1.
2. **References**:
   ```ebnf
   Ref          ::= BareRef | DelimitedRef
   BareRef      ::= "$" RefBody
   DelimitedRef ::= "${" RefBody "}"
   RefBody      ::= SlotPath | SysVarName | VarName
   VarName      ::= [a-zA-Z] [a-zA-Z0-9_]*
   SysVarName   ::= "ret" | "timeout" | "err"
   SlotPath     ::= "slot." SignalName "." FieldName
   ```
   `${}` and whitespace-only references produce `Empty reference`.
3. **Numeric literals**: decimal, hexadecimal and dimensioned time literals, folded to `i32` under §2.1.
4. **Braces**: `{ ... }`, interpreted as argument groups or code blocks by the containing command signature.
5. **Brackets**: `[ ... ]`, used for decorators.
6. **Literal text**: printable ASCII, Tab, LF and CR in text positions. Bare `{`, `[` and `]` in text positions denote literal keystrokes. A `}` in a code stream closes the current `CodeBlock`; a literal `}` inside a block is written `\}`. Command signatures give braces and brackets structural meaning.
   - `\` followed by `\`, `{`, `}` or `$` emits the second character. Escaped braces are literal keystrokes.
   - A `{` at a text position is literal and leaves code-block depth unchanged.
   - `\` followed by LF, CRLF or CR is a line continuation and emits nothing.
   - `\#` starts a comment; `\` followed by an ASCII letter starts a command.
   - Other backslash sequences, including a trailing backslash, produce `Invalid escape sequence`.
   - At document and code-stream text positions, `#` is literal; macro bodies additionally use the parameter rules in §7.
7. **Comments and preamble directives**: `\#` consumes through the line ending, when present. Leading `\#!syntax:` directives configure layout under §3.

Commands, identifiers, variable names and signal fields are case-sensitive. Letter key commands distinguish `\a` from `\A`.

### 2.1 Numeric Representation and Folding

All numeric values fold to signed 32-bit integers in `[-2147483648, 2147483647]`.

```ebnf
NumericLiteral  ::= Sign? ( HexLiteral | DimLiteral | DecLiteral )
Sign            ::= "+" | "-"
HexLiteral      ::= ("0x" | "0X") [0-9a-fA-F]+
DecLiteral      ::= [0-9]+
DimLiteral      ::= DecLiteral "ms" | [0-9]+ ("." [0-9]+)? "s"
```

| Form | Examples | Folding |
|---|---|---|
| Decimal | `120`, `-15` | Base-10 integer |
| Hexadecimal | `0xFF`, `0xFF00FF` | Base-16 integer |
| Milliseconds | `500ms`, `20ms` | Integer millisecond value |
| Seconds | `0.5s`, `1.2s`, `5s` | Round the unsigned magnitude to milliseconds with exact decimal round-half-up, then apply the sign. With unsigned digits `N` and `k` fractional digits, the magnitude is `floor((2000N + 10^k) / (2 × 10^k))`. |

- Folded values MUST belong to the `i32` interval.
- A decimal point requires the `s` suffix and otherwise produces `fractional number requires unit 's'`.
- `\delay`, `\pace` and `\guarded` accept nonnegative duration literals; a negative literal produces `negative duration not allowed`. `\wait` accepts the full signed timeout domain; nonpositive values perform an immediate poll.
- Examples of sub-millisecond rounding: `0.0004s → 0ms`, `0.0006s → 1ms`, `-0.0005s → -1ms`.
- Each consuming instruction applies the physical range checks in §6.7 after folding. For example, `\delay{70s}` and `\rep{300}` are compile errors.

---

## 3. Preamble and Layout Modes

### 3.1 Grammar

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

Preamble directives occupy the leading comment-and-whitespace region and precede the first executable token. Later directives in executable code, unknown presets, unknown keys and unknown `\#!` directives are compile errors.

### 3.2 Layout Configurations

| Declaration | Equivalent configuration | Behavior |
|---|---|---|
| `\#!syntax: free` | `newline=ignore, space=ignore` | Unescaped whitespace is absorbed; explicit commands and text blocks emit whitespace. |
| `\#!syntax: typewriter` | `newline=emit, space=emit` | Default. Raw newlines emit Enter; raw spaces emit Space. |
| `\#!syntax: newline=ignore, space=emit` | Fine-grained configuration | Newlines are absorbed and spaces are emitted. |

A fine-grained `ConfigList` modifies the configuration in effect at that directive, overriding only its listed keys. Configuration starts as `typewriter`. A preset assigns the complete two-key configuration; subsequent fine-grained entries modify that current configuration per key in source order, across one or multiple directives. For example, `free` followed by `space=emit` yields `newline=ignore, space=emit`.

Layout configuration changes whitespace scanning; token grammar, instructions and execution semantics are shared across configurations.

### 3.3 Scanner Behavior

| Input | Context | `free` | `typewriter` |
|---|---|---|---|
| LF / CRLF / CR | Text | Absorbed | `KEY enter` |
| Space / Tab | Text | Absorbed | `CHAR 0x20` / `KEY 0x2B` |
| `\enter`, `\space`, `\tab` | Text | Target keystroke | Target keystroke |
| `\` + newline | Text | Absorbed | Absorbed |
| Text-block payload | Text block | Literal keystrokes | Literal keystrokes |
| Whitespace in `{ a, b }` | Arguments | Trimmed | Trimmed |
| Visible ASCII (33–126) | Text | `CHAR` keystroke | `CHAR` keystroke |

### 3.4 Examples

```dsl
\# Structured source
\#!syntax: free
\def{submit_form}[0]{
  \kdown{ctrl}
  \delay{50ms}
  \kup{ctrl}
  \enter          \# Explicit Enter
}
```

```dsl
\# Whitespace-bearing text in free mode
\#!syntax: free
\text{}echo "Hello World"\endtext{}
\enter
```

```dsl
\# Default typewriter layout
echo "Hello MYCRO"
ls -la
```

---

## 4. Document Grammar

A command consumes trailing groups according to its signature. Braces have four roles:

1. **Code block**: `{ code_stream }`, containing an executable stream with the same grammar as the document root.
2. **Separate arguments**: `{arg_1}{arg_2}...`, with one parameter per group.
3. **Compound arguments**: `{elem_1, elem_2, ...}`, with positional elements in one group.
4. **Decorator**: `[modifier]`, adjacent to the modified command or parameter.

The signature selects one form for each position. For example, `\set{var}{val}` uses separate groups, while `\move{dx, dy}` uses a compound group. Argument groups precede code blocks. Extension argument structure comes from the registered function signature.

A decorator is immediately adjacent to its target: it precedes mandatory groups for `\guarded` and `\wait`, and follows the macro name for `\def`. Optional trailing code blocks bind when only layout-absorbed characters and comments intervene. In typewriter mode, emitted whitespace breaks that adjacency; an unbound block is parsed as literal text under §2.

A lone key or key-combination command, a media command, and a macro call whose effective `argc` is zero take zero brace arguments. Each accepts one immediately adjacent empty `{}` as a separator; the separator emits zero actions and contributes zero arguments. The empty groups in `\text{}` and `\endtext{}` are mandatory instruction syntax under §5.6.

The close `}` of a `CodeBlock` ends that block. An open `{` encountered at a text position within the block is a literal keystroke and leaves the block depth unchanged. A literal close brace within the block uses the `\}` escape.

---

## 5. Base Instructions

### 5.1 Keyboard Operations and Chording

```ebnf
KeyStmt      ::= SpecialKey | KeyCombo | KeyHold | KeyRelease
SpecialKey   ::= "\" KeyName
KeyCombo     ::= "\" KeyName ("+" ComboKeyName)+
KeyHold      ::= "\kdown" "{" ComboPayload "}"
KeyRelease   ::= "\kup" "{" (ComboPayload | "0") "}"
ComboPayload ::= KeyName | ComboKeyName ("+" ComboKeyName)+
ComboKeyName ::= KeyName | Digit
KeyName      ::= [a-zA-Z] [a-zA-Z0-9_]*
Digit        ::= "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9"
```

#### Keyboard Inventory — USB HID Usage Page 0x07

| Category | Canonical identifiers | Aliases | Usage ID | Meaning |
|---|---|---|---|---|
| Letters | `a` .. `z` | — | `0x04` .. `0x1D` | Uppercase forms expand to `[shift, letter]` in combinations and hold payloads; a lone uppercase command lowers to `CHAR`. |
| Digits | `0` .. `9` | — | `0x1E` .. `0x27` | Main-row digits (`0x1E` = `1` … `0x26` = `9`, `0x27` = `0`). Digit characters use text-character lowering (`CHAR`). |
| Editing | `enter` | `return` | `0x28` | Return / Enter |
| | `esc` | `escape` | `0x29` | Escape |
| | `bs` | `backspace` | `0x2A` | Backspace |
| | `tab` | — | `0x2B` | Tab |
| | `space` | — | `0x2C` | Spacebar |
| | `minus` | — | `0x2D` | Main keyboard `-` / `_` |
| | `equal` | — | `0x2E` | Main keyboard `=` / `+` |
| | `plus` | — | `0x2E` with Shift | Main keyboard `+` |
| Navigation | `ins` | `insert` | `0x49` | Insert |
| | `del` | `delete` | `0x4C` | Forward Delete |
| | `home` | — | `0x4A` | Home |
| | `end` | — | `0x4D` | End |
| | `pgup` | `pageup` | `0x4B` | Page Up |
| | `pgdn` | `pagedown` | `0x4E` | Page Down |
| Arrows | `up`, `down`, `left`, `right` | — | `0x52`, `0x51`, `0x50`, `0x4F` | Directional arrows |
| Lock keys | `caps` | `capslock` | `0x39` | Caps Lock |
| | `numlock` | — | `0x53` | Num Lock |
| | `scrlk` | `scrolllock` | `0x47` | Scroll Lock |
| System | `prtscr` | `printscreen`, `snapshot` | `0x46` | Print Screen |
| | `pause` | `break` | `0x48` | Pause / Break |
| | `menu` | `app`, `contextmenu` | `0x65` | Application / Context Menu |
| Function keys | `f1` .. `f12` | — | `0x3A` .. `0x45` | F1–F12 |
| Extended function keys | `f13` .. `f24` | — | `0x68` .. `0x73` | F13–F24 |
| Keypad | `kp_0` .. `kp_9` | — | `0x59` .. `0x62` | Keypad digits |
| | `kp_dot` | `kp_period` | `0x63` | Keypad decimal point |
| | `kp_enter` | — | `0x58` | Keypad Enter |
| | `kp_plus` | — | `0x57` | Keypad `+` |
| | `kp_minus` | — | `0x56` | Keypad `-` |
| | `kp_multiply` | `kp_star` | `0x55` | Keypad `*` |
| | `kp_divide` | `kp_slash` | `0x54` | Keypad `/` |
| | `kp_equal` | — | `0x67` | Keypad `=` |
| Modifiers | `ctrl` (`lctrl`), `rctrl` | `control`, `lcontrol`, `rcontrol` | `0xE0`, `0xE4` | Left / Right Control |
| | `shift` (`lshift`), `rshift` | — | `0xE1`, `0xE5` | Left / Right Shift |
| | `alt` (`lalt`), `ralt` | `opt`, `option`, `lopt`, `ropt`, `loption`, `roption`, `altgr` | `0xE2`, `0xE6` | Left / Right Alt / AltGr / Option |
| | `gui` (`lgui`), `rgui` | `win`, `lwin`, `rwin`, `cmd`, `command`, `lcmd`, `rcmd`, `lcommand`, `rcommand` | `0xE3`, `0xE7` | Left / Right GUI |

#### Combination Lowering

`+` separates key names. `plus`, `kp_plus` and the other named keys represent physical plus keys.

Every `KeyCombo` and hold/release `ComboPayload` observes the same source-token bound. For `T1 + ... + Tn`, `n` occupies `[1, MAX_COMBO_KEYS]`; a longer source chain produces `key combination exceeds MAX_COMBO_KEYS=8`. Each token expands to its physical sequence `K(Ti)`, such as `a → [a]`, `A → [shift, a]`, and `plus → [shift, equal]`. Concatenation produces `K = K(T1) || ... || K(Tn)`. The compiler emits:

1. `KEY_DOWN` for each element of `K` in source order;
2. `SLEEP click_hold_ms`;
3. `KEY_UP` for each element in reverse order.

Each down/up instruction receives VM pacing. Combinations use the sequence lowering above; `COMBO` is a v4 NOP.

A lone key command lowers to one action:

- `\a`–`\z`, `\A`–`\Z`, `\plus`, `\minus`, `\equal` and `\space` lower to `CHAR` for their named printable character. `\A` therefore emits `CHAR 0x41`.
- Other named keys, including Tab, Enter, arrows, function keys and every `kp_*` key, lower to `KEY` with the listed usage.

`click_hold_ms` is a device configuration input with domain `[0,65535]` milliseconds and default `DEFAULT_CLICK_HOLD_MS = 10`. A tap consists of press, hold for `click_hold_ms`, and release. The VM reads its execution-time value for taps in `KEY`, `CHAR`, `MCLICK`, `CONSUMER_KEY` and each `TYPEREG` character. Combination lowering reads the compiler-time value for the embedded `SLEEP`. Internal hold suspension, explicit `SLEEP`, and automatic pacing share the guard slicing in vm.md §6.2. Explicit holds use `\kdown`/`\mdown`, `\delay` and `\kup`/`\mup`.

- `\kdown{combo}` emits the forward `KEY_DOWN` sequence.
- `\kup{combo}` emits the reverse `KEY_UP` sequence.
- `\kup{0}` releases all keyboard keys. Within a combination, `0` denotes the main-row zero key.

Combination expansion preserves duplicates and source order. For example, `\a+A` produces `[a, shift, a]` down, one hold, and the reverse sequence up. Host treatment of repeated assertions of the same usage is platform-dependent.

The streaming compiler collects a `+` chain in a bounded buffer of at most `MAX_COMBO_KEYS = 8` source key tokens, expands it forward for down events and backward for up events.

### 5.2 Multimedia and Consumer Control

```ebnf
MediaStmt  ::= "\" MediaName
MediaName  ::= MediaKey | MediaAlias
MediaKey   ::= "media_playpause" | "media_next" | "media_prev" | "media_stop" | "media_play" | "media_pause"
             | "volup" | "voldn" | "mute" | "brightup" | "brightdn" | "sys_sleep"
MediaAlias ::= (* each alias in the table below *)
```

These commands emit USB HID Consumer Control usages from Usage Page `0x0C`. Each pulse presses the usage, holds it for `click_hold_ms` under the guard slicing in vm.md §6.2, and releases it. Abnormal termination, external cancellation and guard failure release any active usage.

| Command | Aliases | Usage ID | Action |
|---|---|---|---|
| `\media_playpause` | `playpause`, `play_pause` | `0x00CD` | Play / Pause toggle |
| `\media_next` | `next_track` | `0x00B5` | Next track |
| `\media_prev` | `prev_track` | `0x00B6` | Previous track |
| `\media_stop` | `stop_track` | `0x00B7` | Stop |
| `\media_play` | `play` | `0x00B0` | Play |
| `\media_pause` | `pause_track` | `0x00B1` | Pause |
| `\volup` | `media_volup`, `volume_up` | `0x00E9` | Volume increment |
| `\voldn` | `media_voldn`, `volume_down` | `0x00EA` | Volume decrement |
| `\mute` | `media_mute`, `volume_mute` | `0x00E2` | Mute toggle |
| `\brightup` | `brightness_up` | `0x006F` | Brightness increment |
| `\brightdn` | `brightness_down` | `0x0070` | Brightness decrement |
| `\sys_sleep` | `system_sleep`, `host_sleep` | `0x0032` | Host suspend / sleep |

Physical keyboard `Fn` processing occurs inside the keyboard controller. MYCRO scripts address the resulting keyboard or consumer usage directly.

### 5.3 Mouse Operations

```ebnf
ClickStmt ::= "\click" "{" Button [ "," Value ] "}"
MoveStmt  ::= "\move" "{" Value "," Value "}"
MouseDown ::= "\mdown" "{" Button "}"
MouseUp   ::= "\mup" "{" Button "}"
Button    ::= "L" | "R" | "M" | "B" | "F"
```

- `\click{btn[, count]}` clicks button `L`, `R`, `M`, `B` or `F`. `count` defaults to 1 and has interval `[1, 65535]`.
- `\move{dx, dy}` performs relative movement with signed `i16` components.
- `\mdown{btn}` and `\mup{btn}` press and release a mouse button.

### 5.4 Delay and Pacing

```ebnf
DelayStmt ::= "\delay" "{" Duration "}"
PaceStmt  ::= "\pace" "{" Duration "}"
Duration  ::= DimLiteral | DecLiteral | ScalarRef
ScalarRef ::= VarRef | SysVarRef | SlotRef
```

Durations use milliseconds after folding. Static values occupy `[0, 65535]`. Dynamic register values clamp to that interval at execution.

- `\delay{t}` emits one `SLEEP`.
- `\pace{t}` emits `PACE` and sets the task-global VM pacing interval. Execution starts at `DEFAULT_PACE_MS`; the profile supplies this initial value, and `PACE` is the sole runtime writer. Pacing persists across nested scopes until the next `PACE` or execution start. It follows completion of each atomic action, including any internal `click_hold_ms`, under [vm.md §4](vm.md#4-opcode-set-v4). Automatic pacing adds zero compiler-emitted instructions.

For example, with `click_hold_ms = 10` and `pace_ms = 50`, a tap beginning at `t = 0` releases at `t = 10`; its pacing interval ends at `t = 60`, when the next atomic action can begin. Explicit `\delay` remains a separate `SLEEP` instruction.

Longer delays use bounded loops, such as `\rep{100}{\delay{1s}}`.

### 5.5 Bounded Counting Loops

```ebnf
RepStmt ::= "\rep" "{" Value "}" CodeBlock
```

`\rep{count}{body}` executes `body` `count` times. Static counts occupy `[0, 255]`; dynamic counts clamp to that interval. A zero count skips the body. An out-of-range static count produces `MacroError: rep count out of u8 range`.

### 5.6 Verbatim Text Blocks

```ebnf
TextBlock ::= "\text{}" Payload "\endtext{}"
Payload   ::= (* maximal sequence ending at the first literal "\endtext{}" *)
```

- `\text` and `\endtext` each require an immediately adjacent empty `{}`; violations produce `\text requires {}` or `\endtext requires {}`, respectively.
- The payload starts after `\text{}` and ends before the first literal `\endtext{}`.
- Payload characters emit literal keystrokes in every layout mode. Commands, comments, escapes and reference sigils are payload text.
- CRLF emits one Enter; standalone CR and LF each emit Enter.
- An `\endtext{}` instruction requires an active text block; a mismatched instruction produces `endtext without text block`.
- End of input before the terminator produces `Unterminated text block`.
- `text` and `endtext` are reserved command names.

Macro substitution precedes payload scanning. In `\def{gg}[1]{\text{}#1\endtext{}}`, the argument supplies payload bytes directly, including braces, brackets and backslashes. A literal `\endtext{}` in the argument serves as the terminator.

---

## 6. Values, Variables and State

### 6.1 Variables and References

```ebnf
VarRef  ::= "$" VarName | "${" VarName "}"
VarName ::= [a-zA-Z] [a-zA-Z0-9_]*
```

A script has at most 32 user variables. Each holds an `i32` scalar or opaque Handle and starts at scalar zero. The first `\set{name}{value}` or `\pixelrect{name}{rows}` statement declares `name`; registers are allocated from `0x00` in first-write order. Declaration follows evaluation of a `\set` source value, so `\set{x}{$x}` reads `$x` before declaring it and produces `Undefined variable: x`. Every other read of an unassigned variable produces `Undefined variable: {name}`. The names `ret`, `timeout`, `err` and `slot` are reserved; assigning to one produces `Reserved variable name: {name}`.

Value positions evaluate `$name` and `${name}` identically. `RefBody` resolution tries `SlotPath`, then `SysVarName`, then `VarName`, so the `slot.` prefix selects slot syntax before variable scanning. Bare references consume the identifier greedily. Delimited references provide an explicit boundary. An unclosed delimited reference produces `Unmatched ${`; an empty one produces `Empty reference`; non-whitespace following its closing brace within the same reference token produces `Unexpected text after ${...}`.

In literal text and text-block payloads, `$` and `${` are ordinary characters.

### 6.2 Assignment and Arithmetic

```ebnf
SetStmt ::= "\set" "{" VarName "}" "{" Value "}"
AddStmt ::= "\add" "{" VarName "}" "{" Value "}"
SubStmt ::= "\sub" "{" VarName "}" "{" Value "}"
```

- `\set{var}{val}` performs one atomic Value read and assigns the resulting scalar or Handle reference to `var`.
- `\add{var}{val}` and `\sub{var}{val}` require a target declared by an earlier `\set` or `\pixelrect`; they mutate `var` with signed two's-complement `i32` wraparound.
- A Handle in the arithmetic destination or source operand causes a runtime type fault: the destination remains unchanged and `$err` becomes `ERR_HANDLE_MISUSE`.

Handle lifetime and write mediation are defined in [runtime.md §4](runtime.md#4-handle-lifecycle-and-ownership-management).

### 6.3 System Registers and Slots

```ebnf
SysVarRef  ::= "$" SysVarName | "${" SysVarName "}"
SysVarName ::= "ret" | "timeout" | "err"
```

- `$ret` holds the latest extension result: a scalar, a Handle ID, or zero for a void/failure result.
- `$err` holds the latest extension status or Handle-misuse fault code.
- `$timeout` holds the latest wait status: zero for arrival and one for timeout.
- `$slot.<sig>.<field>` and `${slot.<sig>.<field>}` read asynchronous receptor slots under §9.1.

These registers are read-only at source level.

### 6.4 Scalar Literals and Resource Construction

`Value` is the atomic-read category. It contains immediate scalar literals and atomic references; evaluation consists of compile-time literal folding or one atomic read. A `DynamicRef` preserves its scalar or Handle runtime type. Resource construction uses dedicated statements.

```ebnf
ScalarLiteral  ::= NumericLiteral | RGBLiteral
RGBLiteral     ::= "\rgb" "{" u8 "," u8 "," u8 "}"
PixelRectStmt  ::= "\pixelrect" "{" VarName "}" "{" PixelRows "}"
PixelRows      ::= Row ("," Row)*
Row            ::= "\row" "{" PixelColor ("," PixelColor)* "}"
PixelColor     ::= HexLiteral | DecLiteral | RGBLiteral
```

- `\rgb{r,g,b}` packs three constants in `[0,255]` as `(r << 16) | (g << 8) | b` during compilation, producing a `TAG_IMM` scalar.
- `\pixelrect{name}{rows}` constructs a snapshot resource and assigns its Handle to `name`. Its matrix rows have equal length, width and height occupy `[1,128]`, and each color occupies `[0,0xFFFFFF]`. Pixels serialize in row-major RGB8 order.
- `\pixelrect` belongs to the statement grammar. A Value-position occurrence produces `pixelrect requires destination statement`.

Pixel data is stored in the runtime const store during compilation. Constant IDs start at zero and increase in compiler encounter order after macro expansion. Each execution materializes a fresh snapshot Handle through the compiler intrinsic `sys.const`, then assigns `$ret` directly to the destination. Resource lowering activates the intrinsic independently of source-level `\use{sys}` activation. Constant Handles share the lifetime rules in runtime.md §4; their pixel bytes belong to the compilation-lifetime const arena.

The compiler emits:
```
CALL_EXT sys.const, TAG_IMM cid
BRA $err, =, 0, off -> pixelrect_done
SET destination, TAG_REG $ret
pixelrect_done:
```
`BRA` branches on a false condition, so a nonzero `$err` transfers to `pixelrect_done`. Materialization failure leaves the destination unchanged and preserves the `$err` value from `sys.const`. Success performs the ordinary mediated `SET` from `$ret`. This path uses the destination register and `$ret` directly.

```dsl
\#!syntax: free
\use{img}
\pixelrect{expected}{\row{0xFF0000, 0x00FF00}}
\call{img.snap}{0, 0, 2, 1}
\set{actual}{$ret}
\call{img.diff}{$expected}{$actual}{0}
```

### 6.5 Parameter Categories and Lowering

```ebnf
Value      ::= ScalarLiteral | DynamicRef
DynamicRef ::= VarRef | SysVarRef | SlotRef
```

| Category | Source forms | Lowering |
|---|---|---|
| Value | Scalar literal, variable, system register or slot | Scalar literal → `TAG_IMM`; register → `TAG_REG`. A slot emits `READSLOT` into scratch, followed by `TAG_REG`. Each allocation uses the lowest register unbound to a user variable or live temporary, with successive slot operands occupying consecutive scratch registers. The compiler releases these allocations after the consuming instruction. Exhaustion, including a script with all 32 registers bound to user variables, produces `no free register for slot operand`. Guard descriptors encode slots directly. |
| Name | Bare identifiers for keys, buttons, namespaces, signals, variables and extension labels | Compile-time usage ID or string-table index. Extension labels use `TAG_NAME` and preserve case. |
| Predicate | Triplet, `\all` or `\any` | Forward short-circuit branches under §8 and vm.md §5. |

### 6.6 Dynamic Number Rendering

`\typenum{ref}` accepts a user variable, system register or slot reference. It renders the signed scalar in decimal ASCII, including a leading `-`, and taps each character with `click_hold_ms` and per-character pacing.

A literal argument produces `typenum requires a variable ($name)`. A Handle operand renders empty output and sets `$err = ERR_HANDLE_MISUSE`.

### 6.7 Physical Operand Ranges

Static immediates MUST lie within the target interval. Dynamic register values clamp to the same interval for built-in opcode physical domains. `CALL_EXT` scalar arguments retain their raw signed `i32` values for registered-signature validation.

| Instruction | Physical domain |
|---|---|
| `\delay`, `\pace` | `u16` milliseconds, `[0,65535]` |
| `\rep` | `u8` count, `[0,255]` |
| `\click` count | `u16`, `[1,65535]` |
| `\move` components | `i16`, `[-32768,32767]` |
| `\wait` timeout | `i32` milliseconds, `[-2147483648,2147483647]`; omitted = `2147483647`; nonpositive = immediate poll |
| `\guarded` throttle | `u16` milliseconds, `[0,65535]`; default = 0 |
| `\rgb` channels | Three `u8` values, `[0,255]` |
| `\pixelrect` width/height | `[1,128]` |
| `\set`, `\add`, `\sub` | `i32` |

A Handle reaching a scalar-only typed operand produces one uniform runtime fault: the host action and its automatic pacing are skipped, the destination or controlled state remains unchanged, and `$err` becomes `ERR_HANDLE_MISUSE`. This covers `SLEEP`, `PACE`, `LOOP`, both `MCLICK` operands, both `MMOVE` operands, `KEY_DOWN`, `KEY_UP`, `MDOWN`, `MUP`, `CONSUMER_KEY`, `BRA` operands, `ADD`, `SUB`, `TYPEREG`, and the `WAIT` timeout. A Handle used as an arithmetic destination follows the same rule. A faulty predicate takes its false path. `CALL_EXT` instead applies the registered runtime signature to each argument and return value. The VM-wide operational rules are in vm.md §4.

### 6.8 Global Compile Diagnostics

- Source characters are printable ASCII, with Tab/LF/CR accepted in text positions and text blocks. Other bytes are compile errors.
- Unmatched delimiters in structural positions are compile errors.
- Identifiers match `[a-zA-Z][a-zA-Z0-9_]*`. Name positions exclude `$`.
- Undefined variables, unknown keys, unknown mouse buttons, unknown namespaces, unknown static signals and unknown signal namespaces are compile errors.
- `net.send` labels are nonempty, optionally dotted identifiers.
- The opcode segment is at most `MAX_CODE_BYTES = 65535` bytes, including its terminal `END`; the string table independently permits at most 255 entries of at most 255 UTF-8 bytes each.
- Compound groups reject empty elements and trailing commas.
- `sys.const` is reserved for compiler lowering.

Command-specific diagnostics appear with their grammar and semantics above.

---

## 7. Compile-Time Text Macros (`\def`)

```ebnf
DefStmt   ::= "\def" "{" MacroName "}" [ "[" Argc "]" ] CodeBlock
MacroCall ::= "\" MacroName ArgGroup*
MacroName ::= [a-zA-Z] [a-zA-Z0-9_]+
Argc      ::= "0" | "1" | ... | "9"
```

Macro bodies are stored for bounded inline expansion. Expansion inherits the document layout configuration and uses `MAX_MACRO_EXPAND = 16` as its depth ceiling.

1. A call requires an earlier definition at document root. Calls before definition and recursive calls produce `undefined macro`.
2. Definitions inside loops, branches, guards or macro bodies produce `def not allowed in nested scope`.
3. Redefinition produces `macro redefinition: {name}`.
4. Macro names have at least two characters and avoid every built-in command, scalar literal, resource statement, logic keyword, key name and alias in §5.
5. Calls supply exactly `argc` separate argument groups. An omitted `[argc]` defaults to zero; an argc-zero call accepts the empty separator in §4.

Each actual argument group is captured with nested-brace counting. Its opening delimiter sets depth one, each unescaped `{` increments depth, and each unescaped `}` decrements depth; the group ends at depth zero. The escape sequences `\\`, `\{` and `\}` are captured verbatim and preserve the current depth. The captured content then undergoes ordinary substitution and lexical scanning.

`#1`–`#9` denote arguments. A parameter number above `argc` is a compile error. `##` emits a literal `#`; all other `#` sequences, including `#0`, are literal text.

Substitution is byte-oriented and precedes lexical scanning. Parameter recognition occurs in the macro body before substitution; replacement text bypasses further parameter recognition. Arguments forward transparently into compound groups and predicates. Consequently, an argument containing literal `\endtext{}` supplies the terminator of an enclosing text block.

---

## 8. Predicates and Guarded Execution

### 8.1 Predicate Grammar

```ebnf
Predicate      ::= Triplet | LogicBlock
Triplet        ::= "{" Value "," RelOp "," Value "}"
RelOp          ::= "=" | "!=" | "<" | "<=" | ">" | ">="
LogicBlock     ::= "\all" "{" Predicate ("," Predicate)* "}"
                 | "\any" "{" Predicate ("," Predicate)* "}"
```

`\all` computes conjunction and `\any` computes disjunction, both with short-circuit evaluation. Logic nesting is bounded by `MAX_PRED_NEST = 4`.

The first argument of `\if` or `\guarded` wraps one predicate. An atomic predicate therefore has the dual-brace form `\if{ {lhs, op, rhs} }{body}`.

An evaluated source triplet with a Handle operand is FALSE and sets `$err = ERR_HANDLE_MISUSE`. Short-circuit evaluation determines which triplets execute. Recursive lowering preserves this false polarity through every `\all`/`\any` level up to `MAX_PRED_NEST`.

### 8.2 Conditional Branch (`\if`)

```ebnf
IfStmt    ::= "\if" "{" Predicate "}" CodeBlock [ CodeBlock ]
CodeBlock ::= "{" CodeStream "}"
```

The optional second block executes when the predicate is false. Branches lower to forward `BRA`/`JMP` sequences under [vm.md §5](vm.md#5-predicate-lowering--op_bra-offset-semantics). `\ifnum` follows the unknown-command diagnostic path.

Example:

```dsl
\#!syntax: free
\use{img}
\call{img.snap}{0, 0, 128, 128}
\if{ {$err, !=, 0} }{
    \# Capture failure path
    \media_stop
}{
    \set{snap}{$ret}
    \# Snapshot success path
}
```

### 8.3 Guarded Execution (`\guarded`)

```ebnf
GuardedStmt    ::= "\guarded" [ "[" StaticDuration "]" ] "{" GuardPredicate "}" CodeBlock [ CodeBlock ]
GuardPredicate ::= GuardTriplet
                 | "\all" "{" GuardTriplet ("," GuardTriplet)* "}"
                 | "\any" "{" GuardTriplet ("," GuardTriplet)* "}"
GuardTriplet   ::= "{" GuardValue "," RelOp "," GuardValue "}"
GuardValue     ::= ScalarLiteral | DynamicRef
StaticDuration ::= DimLiteral | DecLiteral
```

A guard contains one triplet or one flat `\all`/`\any` list of 1–8 triplets. `GuardValue` matches the descriptor's immediate, register and slot operand categories. Nested logic produces `guarded does not support nested predicate logic; max 8 flat triplets`.

- The optional decorator is a static `u16` throttle in milliseconds, defaulting to zero. Dynamic references are compile errors.
- The entry inspection evaluates every active guard, including outer layers and the new predicate, before the protected body. It sets their `last_check_ms` to the current time; throttles apply to subsequent evaluations under the schedule in vm.md §6.
- A false invariant releases keyboard keys, mouse buttons and any active Consumer Control usage, rolls back loop frames owned by the failed guard, preserves registers, and transfers control to the optional abort block or the guard end.
- Guard nesting is bounded by `MAX_GUARD_NEST = 4`.

---

## 9. Signals, Slots and Runtime Extensions

Macro-initiated operations use `\call`. External events use signals, slot projection and `\wait`.

### 9.1 Signal and Slot Model

```ebnf
SlotRef    ::= "$" "slot." SignalName "." FieldName
             | "${" "slot." SignalName "." FieldName "}"
SignalName ::= Identifier ("." Identifier)*
FieldName  ::= Identifier
Identifier ::= [a-zA-Z] [a-zA-Z0-9_]*
```

Signals are named external projection channels. Slots are scalar receptors written through `$slot.` references. After the `$slot.` prefix, the final dot separates the signal name from the field name. The static `net` signal set is `{net.msg}`. Dynamic namespaces validate signals at namespace level after activation.

A scalar or notification projection uses field `val`; a key-value projection uses its payload keys. Slot field names are explicit in source.

Each `(signal, field)` slot stores:

- `val`: latest `i32` payload, initially zero;
- `pending`: arrival latch, initially zero.

Projection writes the latest payload, sets `pending = 1`, and wakes registered waiters. A multi-field projection updates each addressed slot independently. Multiple pending projections coalesce into the latest value with one latch.

### 9.2 Waiting

```ebnf
WaitStmt    ::= "\wait" [ "[" WaitTimeout "]" ] "{" SlotRef "}"
WaitTimeout ::= [ "-" ] ( DimLiteral | DecLiteral ) | ScalarRef
```

The wait target is a `SlotRef`. Other argument categories are compile errors.

- A pending latch is consumed immediately: `pending = 0`, `$timeout = 0`.
- With an empty latch, execution suspends until projection or timeout.
- Projection consumes the latch and writes `$timeout = 0`.
- Timeout rechecks the latch before returning. A latched projection is consumed as an arrival; an empty latch produces `$timeout = 1`.
- With active guards, every wait poll and slice wake-up evaluates guards before arrival or timeout processing. A false guard unregisters the waiter, preserves the slot's `pending` latch and the prior `$timeout`, and then performs the guard abort. Guard success permits the arrival and timeout rules above.
- Positive timeouts bound the wait in milliseconds. Nonpositive timeouts perform an immediate poll.
- An omitted timeout lowers to `2147483647ms`.

A wait observes arrival, while a later slot read returns the latest value at read time. Coalescing permits that value to be newer than the projection that woke the waiter.

Example:

```dsl
\#!syntax: free
\use{net}
\guarded{ {$err, =, 0} }{
    \rep{100}{
        \wait[100ms]{ $slot.net.msg.val }
        \if{ {$timeout, =, 0} }{
            \typenum{$slot.net.msg.val}
            \enter
        }
    }
}
```

### 9.3 Namespace Activation and Calls

```ebnf
UseStmt     ::= "\use" "{" Namespace "}"
CallStmt    ::= "\call" "{" ExtensionFn "}" ArgGroup*
ExtensionFn ::= Namespace "." Identifier
Namespace   ::= Identifier
Name        ::= Identifier ("." Identifier)*
ArgGroup    ::= "{" Value "}" | "{" Value ("," Value)* "}" | "{" Name "}"
```

`\use{ns}` is a top-level, idempotent, order-sensitive namespace activation directive. Every source-level `\call`, signal and slot reference requires a preceding activation of its namespace; this rule applies uniformly to `sys`, `net`, `img` and extension namespaces. Unknown or undeclared namespaces are compile errors. Activation is a compile-time operation. Compiler-emitted `sys.const` intrinsics are activated by §6.4 lowering independently of source directives.

`\call{ns.fn}` invokes an extension. Argument groups MUST match the registered signature in count, structure and category, then flatten positionally. A mismatch is a compile error. Scalar `TAG_REG` arguments pass their raw signed `i32` values to the runtime signature; built-in physical-domain clamping belongs to the opcodes in §6.7. For example, `\call{img.watch}{name}{x,y}{interval,duration}` emits one `CALL_EXT` with five operands. `ns` and `fn` are string-table indices; Name-category arguments use `TAG_NAME` and preserve case.

Each call writes `$ret` and `$err`. Void and failure results set `$ret = 0`; success sets `$err = 0`, and failure sets a nonzero error code. Standard signatures are defined in [runtime.md §3](runtime.md#3-standard-library-namespaces).

---

## 10. Version History

| Version | Changes |
|---|---|
| v1 | Text typing, basic key symbols and escapes. |
| v2 | Mouse controls, delay/pace separation, repetition, key holds, continuations and comments. |
| v3 | Text macros, registers, arithmetic, `$ret`/`$timeout`, slots, `\ifnum`, `\typenum`, extension calls and Name/Value categories. |
| v4 | Layout preambles with incremental key overrides; verbatim text blocks; unified signed `i32` folding and physical ranges; explicit signal/slot receptors; bounded waits and immediate polling; `$err`; uniform runtime Handle faults; native-branch recursive predicates and immediately evaluated guards; `\sub`; scalar RGB literals and destination-bound `\pixelrect` resource statements; VM pacing and guard-sliced click holds; printable-key `CHAR` lowering; `MAX_COMBO_KEYS = 8` and `MAX_SLOTS = 256`; open-world `net.send` labels. `\ifnum` follows the unknown-command path, and `\rect` is renamed `\pixelrect`. |
