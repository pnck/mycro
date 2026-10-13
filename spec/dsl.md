# MYCRO DSL Specification

**Status: normative.** This document specifies the macro DSL's surface
syntax and semantics. The reference implementation is the compiler in
`src/lib/macro.py`; the bytecode it must emit and the execution
semantics are specified in [vm.md](vm.md) (which also specifies the
middleware type discipline — Part II — that the DSL's extension calls
follow). Read together, the two specs fully define the language:
*dsl.md* is what the author writes; *vm.md* is what it compiles to and
how it runs.

Current version: **DSL v3** (history in §7).

## 1. Document model

A macro source is text. Positions in the text are one of three kinds,
and the kind decides the meaning of `$` and `#`:

| Position kind | Examples | Rules |
|---|---|---|
| **Text position** | top level, bodies of `\rep`/`\def`/`\ifnum` | printable ASCII 32–126 types verbatim; `$` is a literal character |
| **Value position** | a command's argument braces | int literals / `$var` / `$sig.field` interpolation |
| **Command name position** | right after `\` | a command name or key name |

Text-position details:

- Line endings: `\n`, `\r\n`, `\r` all emit an Enter keystroke
- A literal tab character emits Tab
- Escapes: `\\` `\{` `\}` `\$` type the literal character; a backslash
  directly before a newline is a **line continuation** (both swallowed);
  `\#` starts a **comment** to end of line; a bare `#` in plain text
  types a literal `#`
- Bare `{` / `}` in text position type literally
- Anything outside printable ASCII (after line-ending handling) is a
  compile error

## 2. Keys, mouse, timing (v2 base)

Command names are `\`-prefixed; built-in names are ≥2 letters (single
letters are permanently reserved as bare key names — `\a` types "a").

- Special keys: `\enter` `\esc` `\tab` `\space` `\bs` `\del`,
  `\up` `\down` `\left` `\right`, `\home` `\end` `\pgup` `\pgdn`,
  `\ins` `\caps`, `\f1`–`\f12`
- Combos: `\ctrl+c`, `\ctrl+shift+end`, … modifiers chain with `+`;
  a trailing modifier alone (`\ctrl+`) presses/releases the pure modifier
- Mouse: `\click{L|R|M|B|F[,count]}` (B/F = side buttons),
  `\move{dx,dy}` (relative, i16), `\mdown{btn}` / `\mup{btn}`
- Keys held: `\kdown{name}` / `\kup{name}`; `\kup{0}` releases all
- Timing: `\delay{sec}` one-shot sleep (float seconds or `$var` ms);
  `\pace{sec}` sets the interval after subsequent actions
- Loops: `\rep{N}{body}` — N is u8 (0 skips); nesting ≤ 4
  (`MAX_NEST`, shared with `\ifnum`)

All numeric arguments are value positions (§3).

## 3. Values and variables

- Variables are **i32** registers, zeroed at execution start; declared
  implicitly by `\set{name}{value}` (names: `[a-z][a-z0-9_]*`)
- `\set{x}{v}` assigns; `\add{x}{v}` adds (wraps i32); these are the
  **only** instructions that write user variables
- Literals: decimal or `0x` hex, optional leading `-`, i32 range checked
- In a value position, `$name` matches `[a-z0-9_.]` greedily;
  `${name}` delimits explicitly; reading a variable before its `\set`
  is a compile error
- **Special read-only registers**: `$ret` (the result of the most recent
  `\call`; 0 when the call returned nothing) and `$timeout` (1 when the
  last `\wait` timed out). Users cannot `\set` them
- **Slots**: `$sig.field` reads last-published signal state (missing
  reads as 0); signal names may contain dots — the split is at the LAST
  dot (`$net.msg.x` = signal `net.msg`, field `x`)
- **Dual semantics**: `$name` in a value position is the integer;
  `\val{name}` in a text position types the decimal digits (one pacing
  action)

## 4. User macros

- `\def{name}[argc]{body}` (argc 0–9), invoked as `\name{a1}...`;
  resolution priority: built-in commands → user macros → key names
- **A call supplies exactly argc brace groups**; parameter `#i` is the
  raw text of the i-th group. This is how multi-group commands are
  written as macros: `\def{pair}[2]{#1-#2}` + `\pair{a}{b}` — a
  three-group `\multibody{x}{y}{z}` style macro is simply argc 3
- Parameters `#1`–`#9` are **pure textual single-pass substitution**,
  expanded first; substituted text is not rescanned; `##` is a literal
  `#`; `#` followed by anything else (incl. `0` or end of body) passes
  through literally
- **Compound arguments forward transparently**: a group containing
  `A,B` (or `x,y,w,h`) is just text; when its `#i` lands in a
  `point`/`rect`/`\move`-style compound position it is parsed there.
  `\def{snapwatch}[1]{\call{img.snap}{#1}\set{w}{$ret}}` +
  `\snapwatch{40,8,120,12}` works with zero compound-aware machinery
- Corollary: **parameter text is never parsed at the call site** — its
  validity is determined entirely by the landing position(s); a
  parameter landing in two positions must be valid in both
- Top-level only, define-before-use, no redefinition, no clash with
  built-ins/key names; wrong argc and circular references are compile
  errors; expansion depth counts toward `MAX_NEST`; no variadic or
  optional parameters

## 5. Conditionals

`\ifnum{$a}{op}{b}{then}[{else}]` — op ∈ `= != < <= > >=`; the left
operand must be a variable; the right is an int value position. Compiles
to forward branches only (no backward jump ⇒ no `\while`; no else-if —
nest instead). Handle-typed operands are a compile error, **except that
`$ret` is always comparable** — it carries error sentinels (negative
values) alongside results, and checking it is the mandatory failure path
(spec/vm.md Part II §2).

## 6. Runtime extension

- `\use{ns}` — compile-time dependency declaration against the runtime's
  namespace table; unknown namespace = compile error; emits no bytecode
- `\call{ns.fn}{arg}...` — invoke a runtime function; argument count and
  forms are checked against the registered signature; the return value
  lands in `$ret`
- `\wait{sig}[{sec}]` — suspend until the signal's **next** fire (a fire
  with no waiter is discarded; slots still record it); optional timeout
  in seconds (0/omitted = forever); writes `$timeout`; abort interrupts
### Argument passing (unified model)

Every command/`\call` argument is one of exactly two tenses:

- **Compile-time constant** — embedded in the bytecode: an inline i32
  (tag `imm`) or a string-table entry (tag `str`)
- **Runtime value** — an i32 in a register (tag `reg`): `$var`, or a
  slot reference desugared to READSLOT

The three physical operand tags are exactly this: inline constant /
table constant / register. There is no fourth kind of argument.

Each signature type declares which forms it accepts, and the two tenses
partition the types cleanly:

| Type | Compile-time form | Runtime form |
|---|---|---|
| `int` / `color` | literal (incl. `0x` hex) | `$var` / slot |
| `point` / `rect` | per-element, same as int | per-element, same as int |
| `str` | literal text (only form) | **none** — strings are not values |
| handle types | **none** — resources exist only at runtime | `$var` of matching type (only form) |

**Literal text has exactly two fates in the DSL**: in a *text position*
it compiles to keystroke instructions (CHAR/KEY); in an *argument
position* it compiles to a string-table constant — either a name the
runtime resolves (signal/ns/fn) or opaque `str` data passed through to
the receiving function. It never enters a register, a slot, or a return
value. A `str` argument's content is the raw brace text with the
brace-matching escapes (`\{` `\}` `\\`) unescaped and surrounding
whitespace stripped; `$` inside is a compile error (no interpolation).
- Direction rule: **macro-initiated request/response is `\call`;
  world-initiated asynchronous events are signals + `\wait`**. Slots
  are never a function-return channel

### Namespaces registered on device

| ns.fn | args | returns in `$ret` |
|---|---|---|
| `sys.free` | — | free heap bytes (int) |
| `net.send` | str tag | clients reached (int); the push is a `net.msg` envelope |
| `img.snap` | rect (native px) | snap handle ≥ 0 (lossless RGB snapshot) |
| `img.at` | snap handle, point | packed RGB color at the snapshot-local point |
| `img.diff` | snap ×2, int tol | pixels diverging beyond tol (0 = identical) |

`net.msg` is a registered signal: a host's `event.push` wakes
`\wait{net.msg}` and publishes `$net.msg.*` slots. img errors return
`$ret < 0` (details on the serial console). Handles are RAM-resident
and released per the ownership rules (overwrite releases, `\set` moves,
macro exit frees all).

## 7. Boundaries and version history

Boundaries (deliberate, not missing): i32 only; strings are not
first-class — the only text channel is the compile-time `str` literal
argument, and **runtime data values, when a future function needs them,
enter as bytes handles** (handle-table kind `bytes`; text uses a UTF-8
convention at the function boundary), never as str values; no
expressions/multiplication/division (`\set`/`\add` cover counting,
complex math sinks into ext functions); no runtime subroutines; no
`\while`; no else-if; `\val` is decimal only; constructor sugar
(`\rgb{}`) is a deferred v3.1 item.

| Version | Changes |
|---|---|
| v1 (gist era) | Text typing, basic keys, escapes |
| v2 | Mouse (click/move/hold), `\delay`/`\pace` split, `\rep`, `\kdown`/`\kup`, line continuation, comments |
| v3 | `\def` user macros; variables/`\set`/`\add`; `$ret`/`$timeout`; slots; `\ifnum`; `\val`; `\use`/`\call`/`\wait`; value-position interpolation rules; signature type system (incl. point/rect compounds and handle ownership) |
