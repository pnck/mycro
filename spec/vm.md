# MYCRO VM Specification

**Status: normative.** This document specifies MYCRO macro bytecode and the **VM v4** execution machine. It is implementation-language independent; ports in CircuitPython, C, Rust and other languages conform to this specification.

- **Part I** covers the execution model, registers, container format, opcodes, predicate descriptors, guards, and register-write mediation.
- Middleware contracts are specified in [runtime.md](runtime.md).
- DSL surface syntax is specified in [dsl.md](dsl.md).

### Table of Contents
- [§1. Execution Model, Profile Constants & Halting Decidability](#1-execution-model-profile-constants--halting-decidability)
  - [§1.0 Profile Constants](#10-profile-constants)
  - [§1.1 Execution State Machine](#11-execution-state-machine)
  - [§1.2 Halting Decidability & Control Flow](#12-halting-decidability--control-flow)
  - [§1.3 Peripheral Safety Invariants](#13-peripheral-safety-invariants)
- [§2. Register File](#2-register-file)
- [§3. Bytecode Container Format (VERSION 4)](#3-bytecode-container-format-v4)
- [§4. Opcode Set (v4)](#4-opcode-set-v4)
- [§5. Predicate Lowering & `OP_BRA` Offset Semantics](#5-predicate-lowering--op_bra-offset-semantics)
  - [§5.1 Native Branch-on-False Semantics](#51-native-branch-on-false-semantics)
  - [§5.2 Recursive Native-Branch Lowering](#52-recursive-native-branch-lowering)
  - [§5.3 Converse Operator Mapping](#53-converse-operator-mapping)
  - [§5.4 Commutative Normalization & Ephemeral Slot Pre-Loading](#54-commutative-normalization--ephemeral-slot-pre-loading)
- [§6. Flat Iterative Guard Model](#6-flat-iterative-guard-model)
- [§7. Register-Write Mediation (VM ↔ Middleware Interface)](#7-register-write-mediation-vm--middleware-interface)
- [§8. Version Policy and History](#8-version-policy-and-history)

---

# Part I — VM Core

## 1. Execution Model, Profile Constants & Halting Decidability

### 1.0 Profile Constants

The table defines the DSL/VM-visible resource capacities, operand limits, and timing bounds shared by `vm.md`, `dsl.md`, and `runtime.md`. Conforming implementations MUST enforce these bounds. Deployment memory budgets are specified in [Appendix A](#appendix-a--implementation-profile).

| Constant | Value | Units / Scope | Semantics |
|---|---|---|---|
| `MAX_REGS` | `32` | Registers (`0x00`..`0x1F`) | General scalar-or-Handle register file size. |
| `MAX_VARS` | `32` | Identifiers | Maximum unique user variables in a DSL script, mapped one-to-one to registers. |
| `MAX_RUNTIME_NEST` | `8` | Frames | Capacity of `loop_stack` and compile-time lexical nesting limit for `\rep`, `\if`, and `\guarded` blocks. |
| `MAX_GUARD_NEST` | `4` | Slots | Capacity of `guard_slots` and maximum active guard depth. |
| `MAX_MACRO_EXPAND` | `16` | Stack Frames | Maximum compile-time `\def` macro expansion recursion depth on `_nest_stack`. |
| `MAX_PRED_NEST` | `4` | Levels | Compile-time maximum `\all`/`\any` block nesting depth ([dsl.md §8.1](dsl.md#81-predicate-grammar)). |
| `MAX_COMBO_KEYS` | `8` | Source key tokens | Maximum source tokens in one `+` combination; expansion can produce additional physical key events. |
| `MAX_SLOTS` | `256` | Receptor slots | Maximum distinct `(signal, field)` projection slots. |
| `MAX_POINTS` | `256` | Points per request | Maximum point count in one `img.frame` `points` selector, bounding its JSON response within the frame cap. |
| `RECT_MAX_EDGE` | `128` | Pixels | Maximum width or height of a `\pixelrect` matrix ($128^2 \times 3 = 48\text{ KiB}$). |
| `SNAP_SLOT_BYTES` | `49152` | Bytes ($48\text{ KiB}$) | Capacity of a snapshot buffer slot ($128 \times 128 \times 3$ bytes RGB24). |
| `MAX_CODE_BYTES` | `65535` | Bytes ($64\text{ KiB} - 1$) | Maximum opcode-segment length, including its terminal `END`; uses the `u16` code-relative addressing horizon. |
| `MAX_STRINGS` | `255` | Pool Entries | Maximum shared string-table entries (`u8` index). |
| `MAX_STRING_BYTES` | `255` | Bytes per Entry | Maximum UTF-8 byte length of a string-table entry (`u8 len`); longer entries are compile-time errors. |
| `DELAY_SLICE_QUANTUM` | `20` | Milliseconds | Base guard-inspection slice for sleeps, waits, pacing, and internal click holds. |
| `DEFAULT_CLICK_HOLD_MS` | `10` | Milliseconds | Default pulse duration for key taps, mouse clicks, and consumer-control pulses; the `click_hold_ms` configuration domain is `[0,65535]` milliseconds. |
| `DEFAULT_PACE_MS` | `0` | Milliseconds | Execution-start value of the task-global `pace_ms`; `PACE` is its sole runtime writer. |
| `EXT_TIMEOUT_MS` | `5000` | Milliseconds | Timeout for extension calls requiring a provider round trip (`img.*`); expiry returns `ERR_TIMEOUT` ([runtime.md §3.0](runtime.md#30-universal-return-and-error-contract)). |

### 1.1 Execution State Machine

The MYCRO VM is a linear bytecode register machine. Each task has:
- `regs[]`: `MAX_REGS = 32` general registers plus special status registers ([§2](#2-register-file)).
- `ip`: Opcode-segment-relative instruction pointer; the first opcode has address `0`.
- `loop_stack`: A pre-allocated array of `MAX_RUNTIME_NEST = 8` frames, each holding `(remaining_count: u8, loop_body_pc: u16)`.
- `str_table`: Read-only string pool loaded from the container header.
- `guard_slots` / `guard_depth`: A pre-allocated array of `MAX_GUARD_NEST = 4` entries and its active-depth cursor ([§6](#6-flat-iterative-guard-model)).
- `pace_ms`: Task-global pacing state, initialized to `DEFAULT_PACE_MS`. `PACE` is its sole runtime writer; scope entry and exit leave it unchanged.
- `active_consumer_usage`: The Consumer Control usage between press and release, with an inactive state at execution start and after release.

### 1.2 Halting Decidability & Control Flow
- **Control flow**: `BRA`, `JMP`, and guard-failure exits from `GUARD_ENTER` use unsigned forward offsets (`off >= 0`). For each, `base` is the byte immediately after its complete instruction and `target = base + off`, which lies after the current instruction. `LOOP_END` supplies the backward edge, jumping to its matching loop body while the active frame counter is greater than 0.
- **Bounded execution**: Iteration uses bounded `LOOP` / `LOOP_END` frames, and each wait has an upper bound of $2147483647\text{ ms}$. These bounds make halting decidable. External cancellation (`POST /macro/abort`) can terminate a task.

### 1.3 Peripheral Safety Invariants
- **Abnormal exit**: On unhandled exceptions, task aborts, or guard violations ([§6](#6-flat-iterative-guard-model)), the VM releases all held keys and mouse buttons through `hid.keys_release_all()` and `hid.mouse_release(0x1F)`, releases any `active_consumer_usage` through `hid.consumer_release(usage_id)`, and marks that usage inactive.
- **Normal completion**: Keys and mouse buttons held through `KEY_DOWN` / `MDOWN` remain held after normal termination until a subsequent command or abort releases them.
- **Resource reclamation**: Every exit path invokes `runtime.release_handles()`.

---

## 2. Register File

- General registers `0x00`..`0x1F` each hold one signed two's-complement **i32** scalar ($[-2^{31}, 2^{31} - 1]$) or one opaque Handle reference. Runtime type metadata distinguishes these cases independently of the stored numeric value; arithmetic uses modular wraparound.
- Bytecode register operands are `u8` addresses. Addresses `0x20`–`0xFC` are reserved; `0xFD`–`0xFF` are special system registers:
  - `0xFD`: `$err` (also `${err}`); read-only to scripts, written by `CALL_EXT` and handle faults ([§7](#7-register-write-mediation-vm--middleware-interface)). `0` indicates success; other values are standard error codes.
  - `0xFE`: `$timeout` (also `${timeout}`); read-only to scripts, written by `WAIT`. `0` indicates event arrival or a consumed latch; `1` indicates timeout.
  - `0xFF`: `$ret` (also `${ret}`); read-only to scripts, written by `CALL_EXT`. On success it holds an integer scalar or allocated Handle ID; void calls and failures produce `0`.
- Execution start initializes every general and special register to `0`.

---

## 3. Bytecode Container Format (v4)

All multi-byte integer fields are little-endian; signed fields use two's-complement encoding.

A complete program comprises a version marker, a string table, and an opcode segment. This diagram defines logical fields and decoding order; storage adjacency and physical placement are implementation choices.

```
Program:
  u8 VERSION (0xA6)
  StringTable:
    u8 str_count
    str_count entries of [ u8 len + UTF-8 string bytes ]
  OpcodeSegment:
    contiguous opcode-and-operand bytes ending with END
```

`0xA6` is the v4 generation marker. It follows the preceding `0xA5` marker, has the high bit set, and is invalid as standalone UTF-8 or ASCII DSL source. The complete octet forms one opaque generation code and is compared as a whole. It occurs only in the program's `VERSION` field, which the VM consumes before reading the string table. The value is reserved from opcode assignments. Version checks use exact marker equality; its numeric value is a generation identifier independent of the v4 ordinal.

Execution and disassembly accept `VERSION = 0xA6`; every other value is rejected. A well-formed opcode segment ends with `END`. Reaching the segment boundary first follows the abnormal-termination path ([§1.3](#13-peripheral-safety-invariants)).

The opcode segment has logical address `0` at its first opcode. `ip`, branch bases and targets, `loop_body_pc`, and `abort_ip` all use this code-relative address system. Its length includes every opcode, operand and the terminal `END`, and is bounded by `MAX_CODE_BYTES = 65535`. Code capacity and string-table capacity are independent; the string table follows `MAX_STRINGS` and `MAX_STRING_BYTES`.

The compiler interns string-table entries in order of first use and deduplicates them by exact content.

### Physical Typed Operand Encoding

Standard multi-operand instructions use three physical tags:
```
u8 tag:  0 = Immediate (TAG_IMM, i32, 4 bytes)
         1 = Register (TAG_REG, u8 index, 1 byte)
         2 = Name (TAG_NAME, u8 string-table index, 1 byte)
```

A `typed(T)` operand contains a 1-byte tag followed by its payload:
- `TAG_IMM` (`0`): A 4-byte signed `i32` literal. The logical interval of `T` bounds compiler immediates ([dsl.md §6.7](dsl.md#67-physical-operand-ranges)).
- `TAG_REG` (`1`): A 1-byte register address. A built-in opcode clamps its current `i32` scalar to the logical interval of `T`. `CALL_EXT` passes scalar values as raw signed `i32` operands for registered-signature validation.
- `TAG_NAME` (`2`): A 1-byte string-table index. This tag occurs in signatures that explicitly accept a Name operand, such as `CALL_EXT` label arguments.

Typed operands accept `{0, 1}`, with `2` added for signatures that permit Name operands. Any other tag MUST cause container rejection during execution or disassembly; additional structural validation is covered by [Appendix A](#appendix-a--implementation-profile).

A Value position accepts physical Immediate and Register operands. For `$slot.<sig>.<field>` or `${slot.<sig>.<field>}`, the compiler emits `OP_READSLOT` immediately before the host instruction to load the value into an ephemeral register, then passes `TAG_REG` to the host opcode. `\pixelrect` lowering is confined to `PixelRectStmt`: it emits `CALL_EXT sys.const` with an immediate constant ID, a `BRA $err, =, 0` failure branch to the instruction following the assignment, and `SET destination, TAG_REG $ret`. This resource-construction path uses the destination and `$ret` directly. Slot reads use the scratch allocation in [dsl.md §6.5](dsl.md#65-parameter-categories-and-lowering). `GUARD_ENTER` predicate descriptors use their own 2-bit tags (`0: IMM, 1: REG, 2: SLOT`) for delay-slice polling ([§4.1](#41-compact-predicate-descriptor-specification-pred_descriptor)).

---

## 4. Opcode Set (v4)

Instruction decoding starts at logical address `0` of the opcode segment. `0xA6` is assigned exclusively to the program's `VERSION` field; encountering it in an opcode position triggers program rejection.

| Opcode | Mnemonic | Operands | Operational Semantics |
|---|---|---|---|
| `0x01` | `CHAR` | `u8 ascii` | Type ASCII character (Shift state handled automatically by keymap). Also the lowering target of lone printable-character key commands ([dsl.md §5.1](dsl.md#51-keyboard-operations-and-chording)). |
| `0x02` | `KEY` | `u8 keycode` | Single key tap: press, hold `click_hold_ms`, release. |
| `0x03` | `COMBO` | `u8 mod_mask, u8 keycode` | **Deprecated**: advances `ip += 3` with peripheral and register state unchanged. Chording lowers to `KEY_DOWN`/`SLEEP click_hold_ms`/`KEY_UP` sequences ([dsl.md §5.1](dsl.md#51-keyboard-operations-and-chording)). |
| `0x04` | `SLEEP` | `typed(u16 ms)` | One-shot suspension (DSL `\delay`), with duration in $0..65535\text{ ms}$. |
| `0x05` | `MCLICK` | `typed(u8 btn), typed(u16 count)` | Mouse click (`btn`: 1=L, 2=R, 4=M, 8=B, 16=F; `count`: $1..65535$). |
| `0x06` | `MMOVE` | `typed(i16 dx), typed(i16 dy)` | Relative mouse move. Physical USB mouse reports cap relative movements to $\pm 127$; underlying `HIDProvider.mouse_move` implementations chunk larger deltas into repeated reports (see [runtime.md §2](runtime.md#2-hidprovider-peripheral-abstraction-layer)). |
| `0x07` | `PACE` | `typed(u16 ms)` | Set task-global `pace_ms` to a value in $0..65535\text{ ms}$, corresponding to DSL `\pace{t}`. See **Automatic Pacing** below. |
| `0x08` | `CONSUMER_KEY` | `typed(u16 usage_id)` | Single Consumer Control pulse (USB HID Page 0x0C): `hid.consumer_press(usage_id)`, guard-sliced `click_hold_ms` suspension, then `hid.consumer_release(usage_id)`. The VM tracks the active usage between press and release. |
| `0x10` | `LOOP` | `typed(u8 count), u16 len` | Let `loop_base` be the byte immediately after the complete `LOOP`; `len` is the body byte length including `LOOP_END`. For count 0, leave `loop_stack` unchanged and set `ip = loop_base + len`. For a nonzero count, push `(remaining_count = count, loop_body_pc = loop_base)` and continue at `ip = loop_base`. |
| `0x11` | `LOOP_END` | — | Decrement the top `remaining_count`. If the result is greater than 0, set `ip = loop_body_pc`; otherwise pop the frame and advance to the following instruction. |
| `0x12` | `KEY_DOWN` | `typed(u8 keycode)` | Press and hold key. Keycode 0 is the HID no-event value: it performs no key press or release; instruction pacing still applies. |
| `0x13` | `KEY_UP` | `typed(u8 keycode)` | Release held key (keycode 0 = release all). |
| `0x14` | `MDOWN` | `typed(u8 btn)` | Press and hold mouse button. |
| `0x15` | `MUP` | `typed(u8 btn)` | Release mouse button. |
| `0x20` | `SET` | `u8 reg, typed(i32 rhs)` | Register assignment (mediated by `runtime.reg_write`). |
| `0x21` | `ADD` | `u8 reg, typed(i32 rhs)` | Atomic signed two's complement addition: $u = (\text{reg} + \text{rhs}) \bmod 2^{32}$; $\text{reg} = u \text{ if } u < 2^{31} \text{ else } u - 2^{32}$ (mediated). |
| `0x22` | `BRA` | `u8 cc, u8 reg, typed(i32 rhs), u16 off` | Branch on false under signed `i32` ordering: on a false condition, set `ip = base + off`, where `base` follows the complete instruction. `cc`: 0`=`, 1`!=`, 2`<`, 3`<=`, 4`>`, 5`>=`. `off >= 0`, and the target lies after the current instruction. |
| `0x23` | `JMP` | `u16 off` | Unconditional forward jump with `off >= 0`: set `ip = base + off`, where `base` follows the complete instruction and the target lies after the current instruction. |
| `0x24` | `SUB` | `u8 reg, typed(i32 rhs)` | Atomic signed two's complement subtraction: $u = (\text{reg} - \text{rhs}) \bmod 2^{32}$; $\text{reg} = u \text{ if } u < 2^{31} \text{ else } u - 2^{32}$ (mediated). |
| `0x25` | `GUARD_ENTER` | `u16 abort_off, u8 header, [u16 throttle_ms], [pred_descriptor]` | Push the guard and perform the §6.2 entry inspection of every active guard before the protected body, setting each `last_check_ms` to that inspection time. A false predicate follows the guard-abort path. `abort_off >= 0`; `abort_ip` is the instruction base plus `abort_off`. |
| `0x26` | `GUARD_EXIT` | — | Pop current guard slot; deactivate invariant monitoring. |
| `0x28` | `READSLOT` | `u8 reg, u8 sig_str_idx, u8 field_str_idx` | Read slot value into register (mediated). |
| `0x29` | `WAIT` | `u8 sig_str_idx, u8 field_str_idx, typed(i32 timeout_ms)` | Suspend until `$slot.<sig>.<field>` receives a projection, a pending event is consumed, or timeout expires. Signed `i32` timeout domain: $> 0$ bounded wait; $\le 0$ non-blocking immediate poll (consumes a pending event if present). Writes status (0=arrival/consumed, 1=timeout) to `$timeout` (`0xFE`, mediated). |
| `0x2A` | `CALL_EXT` | `u8 ns_id, u8 fn_id, u8 argc, argc × typed` | Invoke the registered extension signature (`ns_id`/`fn_id` are compact activated-namespace and function table indices). Scalar arguments pass as raw signed `i32` values; Name arguments use `TAG_NAME`. The signature governs scalar and Handle arguments and declares the return type. On success, `$ret` (`0xFF`, mediated) holds the returned scalar or Handle ID; void calls and failures produce `0`. `$err` (`0xFD`, mediated) receives `0` on success or the failure code. |
| `0x2B` | `TYPEREG` | `u8 reg` | Type the register's decimal integer value as ASCII taps, each held for `click_hold_ms` (DSL `\typenum{$var}` / `\typenum{${var}}`). |
| `0xFF` | `END` | — | Halt execution successfully. |

**Scalar-Only Operand Handle Faults**: Runtime type metadata identifies Handles independently of their numeric IDs. A Handle in any scalar-only typed operand of `SLEEP`, `PACE`, `LOOP`, `MCLICK`, `MMOVE`, `KEY_DOWN`, `KEY_UP`, `MDOWN`, `MUP`, `CONSUMER_KEY`, `BRA`, `ADD`, `SUB`, or the `WAIT` timeout produces `ERR_HANDLE_MISUSE`; a Handle in a `TYPEREG` source or an `ADD`/`SUB` destination produces the same fault. The VM writes `$err` through mediation, skips the complete host action and automatic pacing, and preserves the destination and controlled peripheral, pacing, slot, and timeout state. Fault completion uses these control transfers:
- `BRA` takes its false target.
- `LOOP` leaves `loop_stack` unchanged and sets `ip = loop_base + len`.
- Every other listed instruction advances to its following instruction. A faulty `WAIT` advances directly with an empty waiter set and preserves `slot.pending` and `$timeout`.

An evaluated predicate triplet with either operand Handle-typed is FALSE and sets `$err = ERR_HANDLE_MISUSE`, for both `BRA` and guard descriptors. `SET` copies scalar or Handle type through mediation. `CALL_EXT` forwards argument types to the registered runtime signature, which governs acceptance, execution, and status; its declared return signature determines whether a successful `$ret` write installs a scalar or a Handle reference. Runtime type comes exclusively from metadata.

**Automatic Pacing**: The VM applies the current `pace_ms` after `CHAR`, `KEY`, `KEY_DOWN`, `KEY_UP`, `MDOWN`, `MUP`, `MCLICK` (between clicks and after the last), `MMOVE`, `CONSUMER_KEY`, and each `TYPEREG` character (including the last). This is the complete paced-opcode set. A tap completes its press, internal `click_hold_ms`, and release before pacing starts; `pace_ms` then covers the interval between that completed atomic action and the next action. The two intervals compose in sequence. Each pacing suspension is guard-sliced per [§6.2](#62-dual-trigger-inspection-scheduling).

### 4.1 Compact Predicate Descriptor Specification (`pred_descriptor`)

`0x25 GUARD_ENTER` encodes invariants using a packed bitfield header and contiguous condition triplets:

#### Instruction Header Encoding
```
u16 abort_off: Unsigned relative jump offset on guard failure, including zero (base point: byte immediately following this complete instruction, after all header, throttle, and descriptor bytes)
u8 header: Control bitfield packing
   bit 7:    logic_mode (0 = ALL conjunction, 1 = ANY disjunction)
   bit 4..6: triplet_cnt - 1 (3 bits, encoding 0..7 representing 1..8 triplets)
   bit 0:    has_throttle (0 = default 0ms unthrottled; 1 = explicit u16 throttle_ms follows)
   bit 1..3: Reserved (value 0)
[u16 throttle_ms]: Present if has_throttle == 1
```
`has_throttle` is `1` iff the source decorator is present (an explicit `[0ms]` encodes as `has_throttle = 1` with value `0` — identical runtime semantics to the default, distinct encoding).

#### Condition Triplet Encoding (`triplet_cnt` entries)
Each triplet consists of a 1-byte specification header followed by variable-length operand payloads:
```
u8 spec: Triplet specification header packing
   bit 0..2: cc relational operator (0: =, 1: !=, 2: <, 3: <=, 4: >, 5: >=)
   bit 3..4: lhs_tag (2 bits: 0: IMM, 1: REG, 2: SLOT)
   bit 5..6: rhs_tag (2 bits: 0: IMM, 1: REG, 2: SLOT)
   bit 7:    Reserved (value 0)
lhs_payload: Operand entity determined by lhs_tag
rhs_payload: Operand entity determined by rhs_tag
```

Operand payload byte layouts:
- `0 = IMM`: 4-byte signed scalar immediate (`i32`).
- `1 = REG`: 1-byte register index (`u8 reg`).
- `2 = SLOT`: 2-byte string table indices (`u8 sig_str_idx, u8 field_str_idx`).

#### Structure and Evaluation
- A descriptor is flat: 1 to 8 condition triplets combined by one `logic_mode`. `\all` within `\any` and `\any` within `\all` are compile-time errors.
- Relational operators use signed 32-bit two's-complement ordering ($-2^{31} \le x \le 2^{31}-1$).
- `triplet_cnt` and the operand tags determine the descriptor length and locate `<protected_body>` directly.
- `GUARD_ENTER` deserializes the descriptor once into `(throttle_ms, last_check_ms, abort_ip, loop_depth, parsed_pred)`. The entry inspection in §6.2 evaluates every active guard before body dispatch and sets each `last_check_ms = monotonic_ms`. A false result at any depth runs §6.3 before any body instruction. Throttles govern later checks.

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
- `abort_off`: Unsigned offset (`>= 0`) pointing to `<bytecode for on_abort>` (or `end_guarded` if `on_abort` is omitted). Base point is the byte immediately following the complete `GUARD_ENTER` instruction, so the target lies after the current instruction.
- `skip_abort_off`: Emitted only when `on_abort` is present to skip the abort handler upon normal completion of `protected_body`.

---

## 5. Predicate Lowering & `OP_BRA` Offset Semantics

`\if` statements lower directly into standard `OP_BRA` and `OP_JMP` sequences.

### 5.1 Native Branch-on-False Semantics
`OP_BRA` branches forward when its condition is false. Every emitted leaf retains the source relational `cc`, apart from the converse mapping used for operand swapping in §5.3. A Handle fault makes that leaf false and selects the same false target.

`\if` blocks lower into the following byte patterns (offsets backpatched; base point = byte immediately following the complete instruction):

```
\if{pred}{then}         →  emit(pred, then_start, end); then_start: <then>; end:
\if{pred}{then}{else}   →  emit(pred, then_start, else_start); then_start: <then>; JMP end; else_start: <else>; end:
```

The compiler materializes every label selected by these patterns at an instruction boundary, including labels adjacent to an empty `CodeBlock`. Ordinary false comparisons and Handle faults share the same emitted `false_target`. Recursive labels therefore remain forward and resolved for every branch shape, with `end` designating the instruction following the complete `\if` lowering.

### 5.2 Recursive Native-Branch Lowering
The compiler recursively emits `emit(node, true_target, false_target)` with forward labels:

1. **Triplet**: Emit any source-ordered slot reads, then `BRA cc, lhs_reg, rhs, off -> false_target`, followed by `JMP true_target`. The native `BRA` handles both an ordinary false comparison and a Handle fault.
2. **Conjunction (`\all`)**: Emit each child in source order. A child's false target is the parent's `false_target`; its true target is a label immediately before the next child, or the parent's `true_target` for the last child.
3. **Disjunction (`\any`)**: Emit each child in source order. A child's true target is the parent's `true_target`; its false target is a label immediately before the next child, or the parent's `false_target` for the last child.

This construction uses native `BRA` conditions plus forward `JMP` instructions. It applies unchanged at every nesting level through `MAX_PRED_NEST`. A Handle-faulted child of `\any` proceeds to the next child, while a Handle-faulted child of `\all` selects the conjunction's false target. Logical inverse-operator lowering is absent, preserving fault polarity for disjunction and mixed nesting.

### 5.3 Converse Operator Mapping
Operand swapping uses the identity $A \text{ op } B \iff B \text{ converse}(op) A$ and this mapping:

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

During leaf triplet emission, the compiler applies these steps in order:
1. **Ephemeral Slot Pre-Loading**: Process slot operands in source order. Emit each `READSLOT` into ephemeral scratch under [dsl.md §6.5](dsl.md#65-parameter-categories-and-lowering), and use that register as the operand.
2. **Immediate/Register Swapping**: After value pre-loading, if `lhs` is an immediate and `rhs` is a register, swap the operands and apply the Converse Operator Mapping ([§5.3](#53-converse-operator-mapping)). The register then occupies the `lhs` position required by `OP_BRA`.
3. **Static Constant Elimination**: Evaluate and eliminate triplets whose operands are compile-time constants. A predicate that folds to a constant emits its taken branch directly.

---

## 6. Flat Iterative Guard Model

### 6.1 Static Flat Slot Management
The VM manages guard execution using a statically pre-allocated flat array and an active depth cursor:
```python
MAX_GUARD_NEST = 4  # Static compile-time nesting ceiling
guard_slots = [None] * MAX_GUARD_NEST  # Entries: (throttle_ms, last_check_ms, abort_ip, loop_depth, pred_desc)
guard_depth = 0  # Active guard cursor (0..4)
```
- `GUARD_ENTER`: Increments `guard_depth += 1` and stores descriptor parameters in `guard_slots[guard_depth - 1]`. The entry inspection evaluates every active guard from outermost to innermost before body dispatch and sets their `last_check_ms = monotonic_ms`; a false result follows §6.3. Throttle scheduling starts after this inspection.
- `GUARD_EXIT`: Decrements `guard_depth -= 1` in $O(1)$ time.
- **Execution Boundary Reset**: Macro launch sets `guard_depth = 0`; normal completion (`END`), unhandled exceptions, and abort paths reset all guard slots.

### 6.2 Dual-Trigger Inspection Scheduling
The VM evaluates active guards cooperatively from outermost to innermost (`0` through `guard_depth - 1`). A false guard at level $i$ triggers the abort sequence in [§6.3](#63-guard-abort-and-nested-scope-rollback).
1. **Guard Entry**: `GUARD_ENTER` evaluates every active guard from outermost to innermost before the body and sets each `last_check_ms` to that inspection time. This inspection satisfies the instruction-boundary check for `GUARD_ENTER`; throttles apply to subsequent checks.
2. **Instruction Boundaries**: Guards are checked after an instruction completes and before the next dispatch. Each evaluation updates `last_check_ms`:
   - `throttle_ms == 0`: Check after every completed macro instruction.
   - `throttle_ms > 0`: Check when `monotonic_ms - last_check_ms >= throttle_ms`.
3. **Suspension Slicing**: `OP_SLEEP`, automatic pacing, and every internal `click_hold_ms` suspension in `CHAR`, `KEY`, `MCLICK`, `CONSUMER_KEY`, and `TYPEREG` use the same guard slices while guards are active:
   $$\Delta t = \min(t_{\text{remaining}}, \Delta t_{\text{slice}})$$
   $$\Delta t_{\text{slice}} = \min_{0 \le j < \text{guard\_depth}} (t_{\text{throttle}, j} > 0 \ ? \ t_{\text{throttle}, j} : \text{DELAY\_SLICE\_QUANTUM})$$
   Guards are evaluated at each slice wake-up; a violation aborts immediately. Resumption continues the remaining host suspension after successful checks.
4. **Wait Slicing**: `OP_WAIT` with active guards uses the same slices. At the initial poll and every slice wake-up, the VM evaluates guards before arrival or timeout processing. A false guard at level $i$ first unregisters the current waiter, preserves the target slot's `pending` latch and the prior `$timeout`, and then runs §6.3. After all active guards evaluate true, the VM processes pending-latch arrival or timeout; timeout processing rechecks the latch before selecting arrival versus `$timeout = 1`.
5. **Extension Calls**: Extension calls run to completion between guard checks. The VM evaluates active guards on entry and return.

### 6.3 Guard Abort and Nested Scope Rollback
When a guard at level $i$ evaluates to false:
1. **Hardware Reset**: Call `hid.keys_release_all()` and `hid.mouse_release(0x1F)`. When `active_consumer_usage` is active, call `hid.consumer_release(usage_id)` and mark it inactive.
2. **Loop Rollback**: Apply in-place `del loop_stack[guard_slots[i].loop_depth:]`, preserving outer loop frames.
3. **Register Preservation**: Preserve general-purpose register values.
4. **Jump and Depth Rollback**: Set `ip = guard_slots[i].abort_ip` and `guard_depth = i`. This unwinds the failed guard and inner guards while outer guards remain active. Execution resumes at `on_abort` or `end_guarded`.

---

## 7. Register-Write Mediation (VM ↔ Middleware Interface)

All register writes pass through middleware before the destination changes:
```python
runtime.reg_write(dst: u8, value: i32, src: u8 | None, handle: bool)
```

`dst` is the destination register, `value` is the scalar or Handle ID to install, and `handle` supplies its runtime type. `src` identifies the source register for `SET reg←reg` and is `None` at other sites. Source value and type are captured before destination mediation. Middleware performs one atomic operation:
1. Detach the destination's old typed reference and remember its displaced Handle, when present.
2. Install `value` with the type selected by `handle`. For `SET reg←reg`, the source remains a shared reference; self-assignment reinstalls the detached reference in the same destination.
3. Scan every general and special register for remaining typed references to the displaced Handle. Release that Handle when the scan finds zero references. Ownership tracking considers typed Handle references only.

The write sites are:

| Site | dst | value | src | handle |
|---|---|---|---|---|
| `SET imm` | reg | immediate | `None` | `False` |
| `SET reg` | reg | source register value | source reg | source runtime type |
| `ADD` | reg | wrapped sum | `None` | `False` |
| `SUB` | reg | wrapped difference | `None` | `False` |
| `READSLOT` | reg | slot scalar | `None` | `False` |
| `WAIT` | `0xFE` (`$timeout` / `${timeout}`) | wait status | `None` | `False` |
| `CALL_EXT` | `0xFD` (`$err` / `${err}`) | extension status | `None` | `False` |
| `CALL_EXT` | `0xFF` (`$ret` / `${ret}`) | extension result | `None` | `True` for a successful Handle return declared by the signature; `False` otherwise |
| Scalar-operand or predicate Handle fault | `0xFD` (`$err` / `${err}`) | `ERR_HANDLE_MISUSE` | `None` | `False` |
| Execution start (zeroing) | all general regs, `0xFD`, `0xFE`, `0xFF` | `0` | `None` | `False` |

The registered `CALL_EXT` return signature determines Handle installation in `$ret`; runtime type metadata distinguishes scalar values and Handle IDs independently of their numeric values. Execution start also invokes `runtime.release_handles()`.

---

## 8. Version Policy and History

`VERSION` gates the container format and opcode semantics. A v4 consumer accepts `VERSION = 0xA6` and applies the rejection rule in §3 to every other marker.

| Version | Key Additions and Changes |
|---|---|
| **v1** (gist era) | Unformatted bytecode. Immediate-only operands; fused sleep/pace. |
| **v2** | Versioned container header (`VERSION`). `SLEEP` and `PACE` split. |
| **v3** | Container marker `0xA5`; typed operands (`TAG_IMM=0`, `TAG_REG=1`, `TAG_STR=2`); 32-register file; `SET`/`ADD`/`BRA`/`JMP`/`TYPEREG`; string table + `READSLOT`/`WAIT`/`CALL_EXT`; register-write mediation. |
| **v4** | Container marker `VERSION = 0xA6`; `$err` at `0xFD`; `TAG_STR` narrowed to `TAG_NAME`; `COMBO` becomes a three-byte NOP; `SUB`, `GUARD_ENTER`, and `GUARD_EXIT` added; signed `i32` wraparound arithmetic; converse operand normalization and recursive native-`BRA` predicate lowering; uniform runtime Handle faults and typed register-write mediation; flat guards with immediate entry evaluation, suspension slicing, wait guard ordering, and loop rollback; exact `LOOP` base semantics and unsigned forward offsets; opcode-segment-relative addressing and independent `MAX_CODE_BYTES` capacity; `WAIT` gains explicit `(sig_str_idx, field_str_idx)` operands and signed `i32` timeout polling; automatic peripheral-action pacing; Consumer Control press/hold/release with active-usage cleanup; lone printable keys lower to `CHAR`; built-in physical operands use uniform clamping while `CALL_EXT` preserves scalar `i32` values; runtime `\pixelrect` materialization through `sys.const`; `MAX_COMBO_KEYS = 8` and `MAX_SLOTS = 256`. |

Compatibility applies exclusively to frozen v4 artifacts.

---

# Appendix A — Implementation Profile

- **Hardware Target**: ESP32-S3 with 8 MiB Octal PSRAM. CircuitPython places the Python heap, bulk image buffers, const stores, and network reassembly buffers in PSRAM.
- **Bulk-Memory Discipline**: Embedded targets use the GC heap for small control-plane objects. Bulk buffers follow these rules:
  1. *On demand*: Allocate each buffer on first touch.
  2. *Retained ownership*: The managing layer retains each allocated buffer and its steady-state capacity. Macro execution streams use at most one 48 KiB snapshot slot (`SNAP_SLOT_BYTES`). Full-frame UI captures bypass device RAM through out-of-band direct LAN fetching.
  3. *Hot path*: Steady-state operations are limited to pointer arithmetic and free-list operations.
  4. *Exhaustion*: Return `$err = ERR_RESOURCE_EXHAUSTED` and `$ret = 0`; the server task remains running.
  5. *Copy budget*: Copy hot-path bytes at most twice.
- **Arena Taxonomy**:
  - *Slot pool*: Fixed-size entries with independent lifetimes (snapshots).
  - *Bump arena*: Variable-size entries with batch lifetime (const store, reset wholesale per compilation).
  - *Stream slot*: One 48 KiB buffer matching `SNAP_SLOT_BYTES`.
- **Budgets**: Snapshot slots: 48 KiB ($128^2$ RGB) $\times$ 8; const arena: 256 KiB; stream assembly slot: 48 KiB; per-connection reassembly: $2 \times (8\text{ KiB} + 8)$.
- **Cache Discipline**: The implementation profile places Handle ownership flags adjacent to the register file and caches hot metadata in dispatch-loop locals.
- **Slot Registry**: `(signal, field)` receptor slots are created on first projection, with capacity bounded by `MAX_SLOTS = 256`; they hold scalars and flags as control-plane GC objects. Projection exhaustion follows the runtime contract.
- **Port Validation**: The `VERSION` and operand-tag rejection in [§3](#3-bytecode-container-format-v4) is mandatory. Ports accepting containers from untrusted sources MUST perform complete structural validation before execution or disassembly: opcode and operand lengths, all tags and indices, reserved bits and values, register and special-register access, instruction boundaries, branch and guard targets, loop lengths and frame pairing, stack underflow and capacity, complete guard descriptors, and the terminal `END`. Every malformed structure follows container rejection.

