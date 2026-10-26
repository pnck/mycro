# MYCRO VM Specification

**Status: normative.** This document is the authoritative specification for the MYCRO macro bytecode and its execution machine for **VM v4**. It is written to be independent of implementation language (the reference implementation is CircuitPython in `src/lib/macro.py`; C/Rust ports conform to this specification).

- **Part I** specifies the VM Core: execution model, register machine, bytecode container format, opcode set, predicate descriptor encoding, flat guard model, and register-write mediation.
- For middleware contracts (runtime registry, type discipline, handle ownership, peripheral providers, and standard library namespaces), see [runtime.md](runtime.md).
- For DSL surface syntax, see [dsl.md](dsl.md).

### Table of Contents
- [§1. Execution Model, Profile Constants & Halting Decidability](#1-execution-model-profile-constants--halting-decidability)
  - [§1.0 Authoritative Profile Constants Table](#10-authoritative-profile-constants-table)
  - [§1.1 Execution State Machine](#11-execution-state-machine)
  - [§1.2 Halting Decidability & Control Flow](#12-halting-decidability--control-flow)
  - [§1.3 Peripheral Safety Invariants](#13-peripheral-safety-invariants)
- [§2. Register File](#2-register-file)
- [§3. Bytecode Container Format (VERSION 4)](#3-bytecode-container-format-version-4)
- [§4. Opcode Set (v4)](#4-opcode-set-v4)
- [§5. Predicate Lowering & `OP_BRA` Offset Semantics](#5-predicate-lowering--op_bra-offset-semantics)
  - [§5.1 Native Branch-on-False Semantics](#51-native-branch-on-false-semantics)
  - [§5.2 Logical Negation Mapping (for Branch-on-False Short-Circuiting)](#52-logical-negation-mapping-for-branch-on-false-short-circuiting)
  - [§5.3 Converse Operator Mapping (for Commutative Operand Swapping)](#53-converse-operator-mapping-for-commutative-operand-swapping)
  - [§5.4 Commutative Normalization & Ephemeral Slot Pre-Loading](#54-commutative-normalization--ephemeral-slot-pre-loading)
- [§6. Flat Iterative Guard Model](#6-flat-iterative-guard-model)
- [§7. Register-Write Mediation (VM ↔ Middleware Interface)](#7-register-write-mediation-vm--middleware-interface)
- [§8. Version Policy and History](#8-version-policy-and-history)

---

# Part I — VM Core

## 1. Execution Model, Profile Constants & Halting Decidability

### 1.0 Authoritative Profile Constants Table

The following table constitutes the normative source of truth for all **DSL/VM-visible** resource capacities, operand limits, and timing bounds across the MYCRO system (`vm.md`, `dsl.md`, and `runtime.md`). Conforming implementations MUST NOT exceed these bounds. Deployment memory budgets (arena sizes, connection buffers) are implementation-profile concerns and live in [Appendix A](#appendix-a--implementation-notes-non-normative-but-binding-for-ports), not in this table:

| Constant | Value | Units / Scope | Normative Semantics & Architectural Rationale |
|---|---|---|---|
| `MAX_REGS` | `32` | Registers (`0x00`..`0x1F`) | Physical scalar register file size. u8 opcode operand ceiling is 256; `0xF0`–`0xFF` reserved for special registers. |
| `MAX_VARS` | `32` | Identifiers | Maximum unique user variables in DSL script; 1-to-1 static mapping to `MAX_REGS`. |
| `MAX_RUNTIME_NEST` | `8` | Frames | Static capacity of `loop_stack`; simultaneously the **lexical nesting ceiling for all logical blocks** (`\rep` / `\if` / `\guarded`), enforced statically at compile time ([dsl.md §1.4](dsl.md#14-axiom-iv-bounded-resources--deterministic-state)). |
| `MAX_GUARD_NEST` | `4` | Slots | Static capacity of `guard_slots`. Bounded invariant depth prevents microcontroller call-frame exhaust. |
| `MAX_MACRO_EXPAND` | `16` | Stack Frames | Maximum compile-time `\def` macro expansion recursion depth on `_nest_stack`. |
| `MAX_PRED_NEST` | `4` | Levels | Maximum `\all`/`\any` logic-block nesting depth in predicates (compile-time; nesting emits no bytecode, so no output-side bound applies — see [dsl.md §8.1](dsl.md#81-predicate-grammar-ebnf)). |
| `RECT_MAX_EDGE` | `128` | Pixels | Maximum width or height of `\pixelrect` matrix ($128^2 \times 3 = 48\text{ KiB}$). |
| `SNAP_SLOT_BYTES` | `49152` | Bytes ($48\text{ KiB}$) | Fixed byte capacity of a snapshot buffer slot ($128 \times 128 \times 3$ bytes RGB 24-bit). |
| `MAX_BYTECODE` | `65535` | Bytes ($64\text{ KiB} - 1$) | Maximum byte length of binary bytecode stream per container (`u16` addressing horizon). |
| `MAX_STRINGS` | `255` | Pool Entries | Maximum entries in shared string table (`u8` index ceiling). |
| `MAX_STRING_BYTES` | `255` | Bytes per Entry | Maximum UTF-8 byte length of a single string-table entry (`u8 len` field); overflow is a static compile error. |
| `DELAY_SLICE_QUANTUM` | `20` | Milliseconds | Base time slice for continuous guard inspection during `\delay` / `\wait`. |
| `DEFAULT_CLICK_HOLD_MS` | `10` | Milliseconds | Profile default pulse duration for single-shot key taps, mouse clicks, and consumer control pulses. |
| `DEFAULT_PACE_MS` | `0` | Milliseconds | Default inter-action pacing delay. |

### 1.1 Execution State Machine

The MYCRO VM is a linear bytecode register machine. Execution state per task:
- `regs[]`: The register file containing `MAX_REGS = 32` general registers plus special status registers (see [§2](#2-register-file)).
- `ip`: Instruction pointer advancing monotonically through the linear opcode stream.
- `loop_stack`: Counting loop frames (remaining count + return address). Implemented as a fixed-capacity pre-allocated array of depth `MAX_RUNTIME_NEST = 8`, holding tuples of `(remaining_count: u8, loop_body_pc: u16)`; zero runtime dynamic heap allocation.
- `str_table`: Read-only string pool loaded from the container header.
- `guard_slots` / `guard_depth`: Flat array of capacity `MAX_GUARD_NEST = 4` and depth cursor for continuous invariant monitoring (see [§6](#6-flat-iterative-guard-model)).
- `pace_ms`: Global inter-action pacing state, initialized to `DEFAULT_PACE_MS` at execution start and modified only by `PACE` (no per-scope save or restore).

### 1.2 Halting Decidability & Control Flow
- **Monotonic Forward Control Flow**: The instruction pointer `ip` advances strictly forward, with the **sole exception of `LOOP_END`**, which jumps backward to its matching loop body start while the active loop frame counter $> 0$. All arbitrary branch and jump instructions (`BRA`, `JMP`, `GUARD_ENTER`) strictly utilize positive forward offsets ($\text{target} > \text{pc}$).
- **Halting Decidability**: Iteration is handled exclusively via bounded counting loops (`LOOP` / `LOOP_END`). Unbounded backward branches (`while`, `goto`) do not exist. Macro execution is statically guaranteed to halt in a finite number of cycles, time out deterministically (as all wait operations enforce an upper bound $\le 2147483647\text{ ms}$), terminate via external task cancellation (`POST /macro/abort`), or terminate via guard failure.

### 1.3 Peripheral Safety Invariants
- **Emergency Safety Fallback**: On unhandled exceptions, task aborts, or guard violations ([§6](#6-flat-iterative-guard-model)), the VM immediately releases all physically held keys and mouse buttons via `hid.keys_release_all()` and `hid.mouse_release(0x1F)`.
- **Normal Completion**: Physical keys and mouse buttons held via `KEY_DOWN` / `MDOWN` persist across normal script termination until explicitly released by subsequent commands or an abort.
- **Resource Reclamation**: All managed Handle resources are reclaimed on every exit path via `runtime.release_handles()`.

---

## 2. Register File

- `MAX_REGS`: Build-profile constant defining the number of general-purpose scalar registers. The normative profile is **32 registers** (`0x00`..`0x1F`), each holding one signed two's complement **i32** scalar ($[-2^{31}, 2^{31} - 1]$) with modular wraparound arithmetic.
- **Addressing Ceiling**: Register operands in bytecode are `u8` addresses (architectural ceiling of 256 registers). Addresses `0x20`–`0xFC` are reserved. Addresses `0xFD`–`0xFF` are reserved for special system registers.
- **Special System Registers**:
  - `0xFD`: `$err` (also accessed as `${err}`; read-only system register; written by `0x2A CALL_EXT` and by handle-misuse type faults, see [§7](#7-register-write-mediation-vm--middleware-interface); `0` indicates success, non-zero indicates standard error code).
  - `0xFE`: `$timeout` (also accessed as `${timeout}`; read-only system register; written exclusively by `0x29 WAIT`; `0` on event arrival / consumed latch, `1` on timeout).
  - `0xFF`: `$ret` (also accessed as `${ret}`; read-only system register; written exclusively by `0x2A CALL_EXT`; holds integer scalar return value or allocated Handle ID on success, or `0` on void/failure).
- **Initialization**: All general-purpose registers (`0x00`..`0x1F`) and special registers (`0xFD`, `0xFE`, `0xFF`) are initialized to `0` at execution start.

---

## 3. Bytecode Container Format (VERSION 4)

```
u8 MAGIC (0xA5)
u8 VERSION (4)
u8 str_count, followed by str_count entries of [ u8 len + UTF-8 string bytes ]  -- Shared String Table
opcode bytecode instruction stream through EOF
```

Execution and disassembly MUST reject any container with mismatched `MAGIC` or `VERSION`. A well-formed container's instruction stream terminates with `END`; a VM reaching end-of-stream without an `END` instruction halts via the abnormal-termination path (peripheral release and handle reclamation, [§1.3](#13-peripheral-safety-invariants)).

**String Table Discipline**: Entries are interned by the compiler in order of first use and deduplicated by exact content.

### Physical Typed Operand Encoding

Standard multi-operand instructions encode operands using a closed 3-tag system:
```
u8 tag:  0 = Immediate (TAG_IMM, i32 LE, 4 bytes)
         1 = Register (TAG_REG, u8 index, 1 byte)
         2 = Name (TAG_NAME, u8 string-table index, 1 byte)
```

A `typed(T)` operand is a 1-byte physical tag (`0 = Immediate (TAG_IMM)`, `1 = Register (TAG_REG)`) followed by its physical payload:
- If `tag == 0` (`TAG_IMM`): A 4-byte signed `i32 LE` literal, which the instruction's operational semantics cast or truncate to `T` (e.g., `u16`, `u8`, `i16`).
- If `tag == 1` (`TAG_REG`): A 1-byte register address `u8 reg`, whose current runtime `i32` scalar is cast or clamped to `T` by the opcode semantics.
- `tag == 2` (`TAG_NAME`): A 1-byte string table index `u8 str_idx`, permitted only in instructions whose signature explicitly accepts a Name category operand (such as `CALL_EXT` label arguments).
Any tag outside `{0, 1}` (or `{0, 1, 2}` where permitted) triggers a container validation rejection.

> [!NOTE]
> **Operand Tag 3-Variant Closure**: Standard VM opcodes permanently consist of the above 3 variants. They do NOT introduce a 4th slot tag. When a command consumes `$slot.<sig>.<field>` (or delimited `${slot.<sig>.<field>}`) in a Value position, the compiler emits `0x28 OP_READSLOT` immediately preceding the host instruction to load the slot value into an ephemeral register, passing `tag = 1 (REG)` to the host opcode.
> The only exception is the internal predicate descriptor of `0x25 GUARD_ENTER`, which defines its own 2-bit tag field (`0: IMM, 1: REG, 2: SLOT`) for zero-overhead delay-slice polling (see [§4.1](#41-compact-predicate-descriptor-specification-pred_descriptor)).

---

## 4. Opcode Set (v4)

| Opcode | Mnemonic | Operands | Operational Semantics |
|---|---|---|---|
| `0x01` | `CHAR` | `u8 ascii` | Type ASCII character (Shift state handled automatically by keymap). |
| `0x02` | `KEY` | `u8 keycode` | Single key tap: press, hold `click_hold_ms`, release (then pacing applies). |
| `0x03` | `COMBO` | `u8 mod_mask, u8 keycode` | **Deprecated**: recognized for backward container compatibility, advances `ip += 3` (1-byte opcode + 2-byte operands), performing NO action (NOP) in VM v4. Arbitrary chording lowers to `KEY_DOWN`/`SLEEP click_hold_ms`/`KEY_UP` sequences ([dsl.md §5.1](dsl.md#51-keyboard-operations--chording)). |
| `0x04` | `SLEEP` | `typed(u16 ms)` | One-shot suspension (DSL `\delay`). Duration strictly bounded to `u16` ($0..65535\text{ ms}$). |
| `0x05` | `MCLICK` | `typed(u8 btn), typed(u16 count)` | Mouse click (`btn`: 1=L, 2=R, 4=M, 8=B, 16=F; `count`: $1..65535$). |
| `0x06` | `MMOVE` | `typed(i16 dx), typed(i16 dy)` | Relative mouse move. Physical USB mouse reports cap relative movements to $\pm 127$; underlying `HIDProvider.mouse_move` implementations chunk larger deltas into repeated reports (see [runtime.md §2](runtime.md#2-hidprovider-peripheral-abstraction-layer)). |
| `0x07` | `PACE` | `typed(u16 ms)` | Set the global pacing state `pace_ms`. Strictly bounded to `u16` ($0..65535\text{ ms}$). See **Automatic Pacing** below. |
| `0x08` | `CONSUMER_KEY` | `typed(u16 usage_id)` | Single Consumer Control pulse (USB HID Page 0x0C, e.g., volume, playback, brightness) via `hid.consumer_pulse()`. |
| `0x10` | `LOOP` | `typed(u8 count), u16 len` | Push loop frame onto `loop_stack`; `len` = body byte length including `LOOP_END`. Count 0 skips loop body. |
| `0x11` | `LOOP_END` | — | Decrement top loop counter; jump back to loop body start if $> 0$, else pop loop frame. Sole backward branch instruction in the VM. |
| `0x12` | `KEY_DOWN` | `typed(u8 keycode)` | Press and hold key (keycode 0 = release all). |
| `0x13` | `KEY_UP` | `typed(u8 keycode)` | Release held key (keycode 0 = release all). |
| `0x14` | `MDOWN` | `typed(u8 btn)` | Press and hold mouse button. |
| `0x15` | `MUP` | `typed(u8 btn)` | Release mouse button. |
| `0x20` | `SET` | `u8 reg, typed(i32 rhs)` | Register assignment (mediated by `runtime.reg_write`). |
| `0x21` | `ADD` | `u8 reg, typed(i32 rhs)` | Atomic signed two's complement addition: $u = (\text{reg} + \text{rhs}) \bmod 2^{32}$; $\text{reg} = u \text{ if } u < 2^{31} \text{ else } u - 2^{32}$ (mediated). |
| `0x22` | `BRA` | `u8 cc, u8 reg, typed(i32 rhs), u16 off` | Branch on False: If condition evaluates to **FALSE** under signed `i32` ordering ($-2^{31} \le x \le 2^{31}-1$), `ip += off`; `cc`: 0`=`, 1`!=`, 2`<`, 3`<=`, 4`>`, 5`>=`. Offsets are strictly positive forward; the base point is the byte immediately following the complete instruction (`target = base + off`). |
| `0x23` | `JMP` | `u16 off` | Unconditional forward jump: `ip += off`. Base point is the byte immediately following the complete instruction. |
| `0x24` | `SUB` | `u8 reg, typed(i32 rhs)` | Atomic signed two's complement subtraction: $u = (\text{reg} - \text{rhs}) \bmod 2^{32}$; $\text{reg} = u \text{ if } u < 2^{31} \text{ else } u - 2^{32}$ (mediated). |
| `0x25` | `GUARD_ENTER` | `u16 abort_off, u8 header, [u16 throttle_ms], [pred_descriptor]` | Push guard into flat slot; activate continuous invariant monitoring. |
| `0x26` | `GUARD_EXIT` | — | Pop current guard slot; deactivate invariant monitoring. |
| `0x28` | `READSLOT` | `u8 reg, u8 sig_str_idx, u8 field_str_idx` | Read slot value into register (mediated). |
| `0x29` | `WAIT` | `u8 sig_str_idx, u8 field_str_idx, typed(i32 timeout_ms)` | Suspend until slot `$slot.<sig>.<field>` receives projection (or consumes pending event) or timeout expires. Signed `i32` timeout domain: $> 0$ bounded wait; $\le 0$ non-blocking immediate poll (consumes pending event if present). Writes status (0=arrival/consumed, 1=timeout) to `$timeout` (`0xFE`, mediated). |
| `0x2A` | `CALL_EXT` | `u8 ns, u8 fn, u8 argc, argc × typed` | Invoke extension function (`ns`/`fn` are string-table indices of the namespace and function names). Callee writes return value or Handle ID to `$ret` (`0xFF`, mediated; `0` on void/failure), and error code to `$err` (`0xFD`, mediated; `0` on success, non-zero on error). |
| `0x2B` | `TYPEREG` | `u8 reg` | Type register decimal integer value as ASCII keystrokes with active pacing (DSL `\typenum{$var}` / `\typenum{${var}}`). |
| `0xFF` | `END` | — | Halt execution successfully. |

**Automatic Pacing**: After completing any peripheral-action opcode — `CHAR`, `KEY`, `KEY_DOWN`, `KEY_UP`, `MDOWN`, `MUP`, `MCLICK` (between individual clicks and after the last), `MMOVE`, `CONSUMER_KEY`, and per character of `TYPEREG` — the VM suspends for the current `pace_ms` (guard-sliced per [§6.2](#62-dual-trigger-inspection-scheduling)). Control-flow, register, `READSLOT`, `WAIT`, `SLEEP`, `PACE`, and `CALL_EXT` opcodes are never paced.

### 4.1 Compact Predicate Descriptor Specification (`pred_descriptor`)

`0x25 GUARD_ENTER` encodes invariants using a packed bitfield header and contiguous condition triplets:

#### Instruction Header Encoding
```
u16 abort_off: Relative forward jump offset on guard failure (base point: byte immediately following this complete instruction, after all header, throttle, and descriptor bytes)
u8 header: Control bitfield packing
   bit 7:    logic_mode (0 = ALL conjunction, 1 = ANY disjunction)
   bit 4..6: triplet_cnt - 1 (3 bits, encoding 0..7 representing 1..8 triplets)
   bit 0:    has_throttle (0 = default 0ms unthrottled; 1 = explicit u16 throttle_ms follows)
   bit 1..3: Reserved (must be 0)
[u16 throttle_ms]: Present only if has_throttle == 1 (2 bytes LE)
```
`has_throttle` is `1` iff the source decorator is present (an explicit `[0ms]` encodes as `has_throttle = 1` with value `0` — identical runtime semantics to the default, distinct encoding).

#### Condition Triplet Encoding (`triplet_cnt` entries)
Each triplet consists of a 1-byte specification header followed by variable-length operand payloads:
```
u8 spec: Triplet specification header packing
   bit 0..2: cc relational operator (0: =, 1: !=, 2: <, 3: <=, 4: >, 5: >=)
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

#### Structural Constraints & Streaming Properties
- **Flat Invariant Constraint**: A `GUARD_ENTER` descriptor is strictly flat, consisting of 1 header byte followed by 1 to 8 condition triplets. Nested composite logic (`\all` within `\any` or vice versa) is strictly prohibited and rejected at compile time.
- **Signed Integer Comparisons**: All relational operator evaluations operate strictly under signed 32-bit two's complement integer ordering ($-2^{31} \le x \le 2^{31}-1$).
- **Zero Redundant Length Headers**: Deserialization is deterministically governed by `triplet_cnt` and operand tags; `ip` advances directly to `<protected_body>` without an outer `pred_len`.
- **Zero Runtime Deserialization Overhead**: Deserialized once upon `GUARD_ENTER` into a fixed in-memory tuple `(throttle_ms, last_check_ms, abort_ip, loop_depth, parsed_pred)` for zero-unpacking slice re-evaluation.

### 4.2 Guarded Block Bytecode Lowering Pattern

`\guarded` blocks are lowered into a pair of `GUARD_ENTER` and `GUARD_EXIT` framing instructions:

```
GUARD_ENTER abort_off, header, [throttle_ms], [pred_descriptor]
<bytecode for protected_body>
GUARD_EXIT
[JMP skip_abort_off]    # Emitted only if on_abort is present
<bytecode for on_abort>
end_guarded:
```
- `abort_off`: Relative forward jump offset pointing to `<bytecode for on_abort>` (or `end_guarded` if `on_abort` is omitted). Base point is the byte immediately following the complete `GUARD_ENTER` instruction.
- `skip_abort_off`: Emitted only when `on_abort` is present to skip the abort handler upon normal completion of `protected_body`.

---

## 5. Predicate Lowering & `OP_BRA` Offset Semantics

`\if` statements lower directly into standard `OP_BRA` and `OP_JMP` sequences.

### 5.1 Native Branch-on-False Semantics
`OP_BRA` execution semantics are strictly: **Branch forward if condition evaluates to FALSE**.

`\if` blocks lower into the following byte patterns (offsets backpatched; base point = byte immediately following the complete instruction):

```
\if{pred}{then}         →  BRA cc, reg, rhs, off→end; <then>; end:
\if{pred}{then}{else}   →  BRA cc, reg, rhs, off→else_start; <then>; JMP end; else_start: <else>; end:
```

### 5.2 Logical Negation Mapping (for Branch-on-False Short-Circuiting)
1. **Conjunction Node (`\all`)**: False branches of each sub-predicate $P_i$ converge on the parent's `false_target`. The first $N-1$ successful tests fall through to $P_{i+1}$, and the final child falls through to the parent's `true_target`.
2. **Disjunction Node (`\any`)**: The compiler maps the first $N-1$ sub-predicates to their **Logical Dual Operators (Inverse `cc`)**, emitting `OP_BRA(invert(cc), ..., then_target)`. Any sub-predicate evaluating to true immediately jumps to the then body. The final child emits its native operator `OP_BRA(cc, ..., false_target)`.
3. **Logical Negation Mapping Table**:
   - `=` (0) $\leftrightarrow$ `!=` (1)
   - `<` (2) $\leftrightarrow$ `>=` (5)
   - `<=` (3) $\leftrightarrow$ `>` (4)

Lowering the short-circuit control flow itself requires zero temporary registers and zero runtime stack allocations (slot-valued operands may require an ephemeral scratch register via `READSLOT`, see [§5.4](#54-commutative-normalization--ephemeral-slot-pre-loading)); relative offsets are resolved via backpatching.

### 5.3 Converse Operator Mapping (for Commutative Operand Swapping)
When `lhs` is an immediate literal and `rhs` is a variable/register, the compiler swaps the operand positions so that the register occupies the `lhs` position in `OP_BRA` ($A \text{ op } B \iff B \text{ converse}(op) A$). Swapping operands requires the **Converse Operator Mapping**:

| Original Operator (`A op B`) | Converse Operator (`B op' A`) | Original `cc` | Converse `cc'` |
|---|---|---|---|
| `=` | `=` | 0 | 0 |
| `!=` | `!=` | 1 | 1 |
| `<` | `>` | 2 | 4 |
| `<=` | `>=` | 3 | 5 |
| `>` | `<` | 4 | 2 |
| `>=` | `<=` | 5 | 3 |

### 5.4 Commutative Normalization & Ephemeral Slot Pre-Loading
The rules in this section apply to `\if` lowering into `OP_BRA` only; `GUARD_ENTER` descriptors encode operands in source order (an immediate `lhs` is natively supported by the descriptor tags), and a single-triplet descriptor sets `logic_mode = 0`.

During leaf triplet emission:
1. **Commutative Operand Swapping**: If `lhs` is an immediate literal and `rhs` is a variable/register, operands are swapped and the relational operator is transformed via the Converse Operator Mapping ([§5.3](#53-converse-operator-mapping-for-commutative-operand-swapping)) to ensure the register occupies the `lhs` position in `OP_BRA`.
2. **Ephemeral Slot Pre-Loading**: If an operand references `$slot.<sig>.<field>` (or delimited `${slot.<sig>.<field>}`), the compiler emits `READSLOT` to an ephemeral register immediately prior to `OP_BRA`.
3. **Static Constant Elimination**: Constant triplets (both operands compile-time constants) are evaluated and eliminated individually; if an entire predicate folds to a constant, only the taken branch is emitted (no `BRA`/`JMP` at all).

---

## 6. Flat Iterative Guard Model

### 6.1 Static Flat Slot Management
The VM manages guard execution using a statically pre-allocated flat array and an active depth cursor:
```python
MAX_GUARD_NEST = 4  # Static compile-time nesting ceiling
guard_slots = [None] * MAX_GUARD_NEST  # Entries: (throttle_ms, last_check_ms, abort_ip, loop_depth, pred_desc)
guard_depth = 0  # Active guard cursor (0..4)
```
- `GUARD_ENTER`: Increments `guard_depth += 1` and stores descriptor parameters into `guard_slots[guard_depth - 1]`.
- `GUARD_EXIT`: Decrements `guard_depth -= 1` in $O(1)$ constant time.
- **Execution Boundary Reset**: `guard_depth = 0` at macro launch; normal completion (`END`), unhandled exceptions, and abort paths reset all guard slots.

### 6.2 Dual-Trigger Inspection Scheduling
When evaluating active guards, the VM iterates cooperatively from outermost to innermost (`0` through `guard_depth - 1`). If any guard layer $i$ is violated, an immediate abort is triggered:
1. **Instruction Boundary Throttling**: Evaluated cooperatively at each instruction boundary (after an instruction completes, before the next dispatch). `last_check_ms` is updated at every guard evaluation:
   - If `throttle_ms == 0`: Checks all active guards after every completed macro instruction.
   - If `throttle_ms > 0`: Checks guards when `monotonic_ms - last_check_ms >= throttle_ms`.
2. **Delay Slicing**: When suspending during `\delay` (`OP_SLEEP`) or an automatic pacing suspension, if `guard_depth > 0`, the VM slices total sleep duration into micro-quanta:
   $$\Delta t = \min(t_{\text{remaining}}, \Delta t_{\text{slice}})$$
   $$\Delta t_{\text{slice}} = \min_{0 \le j < \text{guard\_depth}} (t_{\text{throttle}, j} > 0 \ ? \ t_{\text{throttle}, j} : \text{DELAY\_SLICE\_QUANTUM})$$
   (base quantum `DELAY_SLICE_QUANTUM = 20ms`). Guards are evaluated upon each slice wake-up; violations trigger an immediate abort.
3. **Wait Slicing**: During `\wait` (`OP_WAIT`), if guards are active, suspension is sliced into the same per-quantum wake-ups, evaluating active guards at every quantum boundary to ensure long bounded waits remain guarded.
4. **CALL_EXT Blind Window**: Guard evaluation cannot cut into the interior of an extension call. Active guards are evaluated once on entry and once on return; long operations should use push streaming (`img.watch`) + `\wait`.

### 6.3 Force-Abort & Nested Scope Isolation
When a guard at level $i$ evaluates to false:
1. **Hardware Peripheral Reset**: Calls `hid.keys_release_all()` and `hid.mouse_release(0x1F)` to prevent hardware key hangs.
2. **Loop Frame Rollback**: `del loop_stack[guard_slots[i].loop_depth:]` in-place, truncating loop frames belonging to the aborted scope while preserving outer loop contexts without dynamic memory reallocation.
3. **Register Preservation**: Current values in general-purpose registers remain intact.
4. **Targeted Jump & Depth Rollback**: Sets `ip = guard_slots[i].abort_ip` and `guard_depth = i`. Unwinds the failed guard and any inner guards while leaving outer guards active. Execution resumes at `on_abort` (or `end_guarded`).

---

## 7. Register-Write Mediation (VM ↔ Middleware Interface)

The VM guarantees that **every** register write passes through the middleware before the value lands:
```python
runtime.reg_write(dst: u8, src: u8 | None)
```

`dst` is the register being written; `src` is the source register when the value originates from another register (`SET reg←reg`), else `None`. The complete, closed set of write sites:

| Site | dst | src |
|---|---|---|
| `SET imm` | reg | `None` |
| `SET reg` | reg | source reg |
| `ADD` | reg | `None` |
| `SUB` | reg | `None` |
| `READSLOT` | reg | `None` |
| `WAIT` | `0xFE` (`$timeout` / `${timeout}`) | `None` |
| `CALL_EXT` | `0xFD` (`$err` / `${err}`) | `None` |
| `CALL_EXT` | `0xFF` (`$ret` / `${ret}`) | `None` |
| Handle-misuse type fault (`BRA` / `ADD` / `SUB` / `TYPEREG` receiving a handle operand) | `0xFD` (`$err` / `${err}`) | `None` |
| Execution start (zeroing) | all general regs, `0xFD`, `0xFE`, `0xFF` | `None` |

Execution start and every exit path additionally invoke `runtime.release_handles()`.

---

## 8. Version Policy and History

`VERSION` gates the container format and opcode semantics. Bytecode consumers MUST reject unknown versions.

**v3 → v4 Incompatibility**: The `VERSION` bump from 3 to 4 marks a hard bytecode break — a v4 consumer MUST reject `VERSION = 3` containers, and v3 bytecode is never executed under v4 semantics. Breaking changes at the bytecode level:
- `0x03 COMBO` deprecated to NOP (arbitrary chording now lowers to `KEY_DOWN`/`SLEEP`/`KEY_UP` sequences; a v3-era COMBO under v4 semantics would silently perform nothing);
- `0x29 WAIT` operand layout revised to explicit `(sig_str_idx, field_str_idx)` with a signed `i32` timeout domain;
- Operand tag 2 narrowed from general string (`TAG_STR`) to Name category (`TAG_NAME`);
- `0xFD` reassigned from reserved boundary register to `$err`;
- New opcodes `0x24 SUB`, `0x25 GUARD_ENTER`, `0x26 GUARD_EXIT`.

Source-level v3 scripts additionally require migration (e.g., `\ifnum` removed outright, `\rect` → `\pixelrect`, `net.send` labels restricted to identifier grammar, `\wait` semantics redefined; see [dsl.md §10](dsl.md#10-boundaries-and-version-history)).

| Version | Key Additions and Changes |
|---|---|
| **v1** (gist era) | Unformatted bytecode. Immediate-only operands; fused sleep/pace. |
| **v2** | Container header (`MAGIC + VERSION`). `SLEEP` and `PACE` split. |
| **v3** | Typed operands (`TAG_IMM=0`, `TAG_REG=1`, `TAG_STR=2`); 32-register file; `SET`/`ADD`/`BRA`/`JMP`/`TYPEREG`; string table + `READSLOT`/`WAIT`/`CALL_EXT`; register-write mediation. |
| **v4** | Container `VERSION = 4`; split dedicated `$err` (`0xFD`) error register from `$ret` (`0xFF`); operand tag 2 renamed `TAG_STR` → `TAG_NAME` (semantic narrowing: Name-category operands only); `COMBO` opcode deprecated (recognized as NOP advancing `ip += 3`); native atomic subtraction `0x24 SUB`; signed `i32` two's complement wraparound arithmetic; converse operator mapping for commutative normalization; flat invariant guard opcodes `0x25 GUARD_ENTER` and `0x26 GUARD_EXIT` with delay slicing and in-place loop frame rollback; revised `0x29 WAIT` with explicit `(sig_str_idx, field_str_idx)` and signed `i32` timeout domain supporting non-blocking immediate polling. |

v4 was frozen from pre-release draft iterations; no compatibility is claimed for artifacts produced against pre-freeze v4 drafts.

---

# Appendix A — Implementation Notes (Non-Normative but Binding for Ports)

- **Hardware Platform Target**: ESP32-S3 microcontroller with 8 MiB Octal PSRAM. CircuitPython places the Python heap in PSRAM; bulk image buffers, const stores, and network frame reassembly buffers reside there.
- **Bulk-Memory Discipline**: On memory-constrained embedded targets, GC heap is restricted to small control-plane objects. Five rules bind every bulk buffer:
  1. *On demand*: Buffers allocate on first touch, costing zero RAM if unused.
  2. *Own forever*: Once allocated, owned by the managing layer; steady-state buffers never shrink. Macro execution streams are strictly bounded by one snapshot slot (48 KiB, `SNAP_SLOT_BYTES`). Full-frame UI captures bypass device RAM via out-of-band direct LAN fetching.
  3. *Hot path allocates nothing*: Steady state performs pointer arithmetic and free-list operations only.
  4. *Deterministic failure*: Exhaustion fails gracefully, setting standard error sentinels `$err = ERR_RESOURCE_EXHAUSTED` and `$ret = 0`, never crashing the server task.
  5. *Copy budget*: Hot-path bytes are copied at most twice.
- **Arena Taxonomy**:
  - *Slot pool*: Fixed-size, independent lifetimes (snapshots).
  - *Bump arena*: Variable-size, batch lifetime (const store, reset wholesale per compilation).
  - *Stream slot*: Single 48 KiB buffer matching `SNAP_SLOT_BYTES`.
- **Budgets (Authoritative Profile Constants)**: Snapshot slot 48 KiB ($128^2$ RGB) $\times$ 8; const arena 256 KiB; stream assembly slot 48 KiB; per-connection reassembly $2 \times (8\text{ KiB} + 8)$. See [§1.0](#10-authoritative-profile-constants-table).
- **Cache Discipline**: Handle ownership flags should reside adjacent to the register file; hoisting flags into dispatch-loop locals enables single-cycle fast paths.
- **Slot Registry**: `(signal, field)` receptor slots are created on first projection and are unbounded by the normative profile; they hold only scalars and flags (control-plane GC objects, not bulk buffers).

