# MYCRO VM Specification

**Status: normative.** This document is the authoritative specification for the MYCRO macro bytecode and its execution machine for **VM v4**. It is written to be independent of implementation language (the reference implementation is CircuitPython in `src/lib/macro.py`; C/Rust ports conform to this specification).

- **Part I** specifies the VM Core: execution model, register machine, bytecode container format, opcode set, predicate descriptor encoding, flat guard model, and register-write mediation.
- For middleware contracts (runtime registry, type discipline, handle ownership, peripheral providers, and standard library namespaces), see [runtime.md](runtime.md).
- For DSL surface syntax, see [dsl.md](dsl.md).

### Table of Contents
- [§1. Execution Model & Halting Decidability](#1-execution-model--halting-decidability)
- [§2. Register File](#2-register-file)
- [§3. Bytecode Container Format (VERSION 4)](#3-bytecode-container-format-version-4)
- [§4. Opcode Set (v4)](#4-opcode-set-v4)
- [§5. Predicate Lowering & `OP_BRA` Offset Semantics](#5-predicate-lowering--op_bra-offset-semantics)
- [§6. Flat Iterative Guard Model](#6-flat-iterative-guard-model)
- [§7. Register-Write Mediation (VM ↔ Middleware Interface)](#7-register-write-mediation-vm--middleware-interface)
- [§8. Version Policy and History](#8-version-policy-and-history)

---

# Part I — VM Core

## 1. Execution Model & Halting Decidability

The MYCRO VM is a linear bytecode register machine. Execution state per task:
- `regs[]`: The register file (see [§2](#2-register-file)).
- `ip`: Instruction pointer advancing monotonically through the linear opcode stream.
- `loop_stack`: Counting loop frames (remaining count + return address), bounded by bytecode size; zero runtime heap allocation.
- `str_table`: Read-only string pool loaded from the container header.
- `guard_slots` / `guard_depth`: Flat array and cursor for invariant monitoring (see [§6](#6-flat-iterative-guard-model)).

### Halting Decidability & Control Flow
- **Forward Control Flow**: The instruction pointer `ip` advances strictly forward. Branch and jump instructions (`BRA`, `JMP`) strictly utilize positive forward offsets ($\text{target} > \text{pc}$).
- **Halting Decidability**: Iteration is handled exclusively via bounded counting loops (`LOOP` / `LOOP_END`). Unbounded backward branches (`while`, `goto`) do not exist. Macro execution is guaranteed to halt in finite cycles, terminate via task cancellation, or suspend awaiting an external event (exitable via abort or guard failure).

### Peripheral Safety Invariants
- **Emergency Safety Fallback**: On unhandled exceptions, task aborts, or guard violations ([§6](#6-flat-iterative-guard-model)), the VM immediately releases all physically held keys and mouse buttons via `hid.keys_release_all()` and `hid.mouse_release(0x1F)`.
- **Normal Completion**: Physical keys and mouse buttons held via `KEY_DOWN` / `MDOWN` persist across normal script termination until explicitly released by subsequent commands or an abort.
- **Resource Reclamation**: All managed Handle resources are reclaimed on every exit path via `runtime.release_all()`.

---

## 2. Register File

- `MAX_REGS`: Build-profile constant defining the number of general-purpose scalar registers. The normative profile is **32 registers** (`0x00`..`0x1F`), each holding one signed two's complement **i32** scalar ($[-2^{31}, 2^{31} - 1]$) with modular wraparound arithmetic.
- **Addressing Ceiling**: Register operands in bytecode are `u8` addresses (architectural ceiling of 256 registers). Addresses `0xF0`–`0xFF` are reserved for special registers.
- **Special Registers**:
  - `0xFD`: Reserved boundary register (never stores).
  - `0xFE`: `$timeout` (written exclusively by `0x29 WAIT`; 1 on timeout, 0 on event arrival).
  - `0xFF`: `$ret` (written exclusively by `0x2A CALL_EXT`; 0 if callee returns None).
- **Initialization**: All general-purpose registers are zeroed at execution start.

---

## 3. Bytecode Container Format (VERSION 4)

```
u8 MAGIC (0xA5)
u8 VERSION (4)
u8 str_count, followed by str_count entries of [ u8 len + UTF-8 string bytes ]  -- Shared String Table
opcode bytecode instruction stream through EOF
```

Execution and disassembly MUST reject any container with mismatched `MAGIC` or `VERSION`.

### Physical Typed Operand Encoding

Standard multi-operand instructions encode operands using a closed 3-tag system:
```
u8 tag:  0 = Immediate (i32 LE, 4 bytes)
         1 = Register (u8 index, 1 byte)
         2 = String (u8 string table index, 1 byte)
```

> [!NOTE]
> **Operand Tag 3-Variant Closure**: Standard VM opcodes permanently consist of the above 3 variants. They do NOT introduce a 4th slot tag. When a command consumes `$slot.<sig>.<field>` in a Value position, the compiler emits `0x28 OP_READSLOT` immediately preceding the host instruction to load the slot value into an ephemeral register, passing `tag = 1 (REG)` to the host opcode.
> The only exception is the internal predicate descriptor of `0x25 GUARD_ENTER`, which defines its own 2-bit tag field (`0: IMM, 1: REG, 2: SLOT`) for zero-overhead delay-slice polling (see [§4.1](#41-compact-predicate-descriptor-specification-pred_descriptor)).

---

## 4. Opcode Set (v4)

| Opcode | Mnemonic | Operands | Operational Semantics |
|---|---|---|---|
| `0x01` | `CHAR` | `u8 ascii` | Type ASCII character (Shift state handled automatically by keymap). |
| `0x02` | `KEY` | `u8 keycode` | Single key tap. |
| `0x03` | `COMBO` | `u8 mod_mask, u8 keycode` | Modifier combo (mask bits 0..3 = Ctrl/Shift/Alt/GUI; keycode 0 = modifier tap). |
| `0x04` | `SLEEP` | `typed(u16 ms)` | One-shot suspension (DSL `\delay`). Word length strictly bounded to `u16` ($0..65535\text{ ms}$). |
| `0x05` | `MCLICK` | `typed(u8 btn), typed(u16 count)` | Mouse click (`btn`: 1=L, 2=R, 4=M, 8=B, 16=F). |
| `0x06` | `MMOVE` | `typed(i16 dx), typed(i16 dy)` | Relative mouse move. |
| `0x07` | `PACE` | `typed(u16 ms)` | Configure inter-action pacing delay. Strictly bounded to `u16` ($0..65535\text{ ms}$). |
| `0x08` | `CONSUMER_KEY` | `typed(u16 usage_id)` | Single Consumer Control pulse (USB HID Page 0x0C, e.g., volume, playback, brightness). |
| `0x10` | `LOOP` | `typed(u8 count), u16 len` | Push loop frame; `len` = body byte length including `LOOP_END`. Count 0 skips. |
| `0x11` | `LOOP_END` | — | Decrement top loop counter; jump back to loop body start if $> 0$, else pop frame. |
| `0x12` | `KEY_DOWN` | `typed(u8 keycode)` | Press and hold key (keycode 0 = release all). |
| `0x13` | `KEY_UP` | `typed(u8 keycode)` | Release held key (keycode 0 = release all). |
| `0x14` | `MDOWN` | `typed(u8 btn)` | Press and hold mouse button. |
| `0x15` | `MUP` | `typed(u8 btn)` | Release mouse button. |
| `0x20` | `SET` | `u8 reg, typed(i32 rhs)` | Register assignment (mediated by `runtime.reg_write`). |
| `0x21` | `ADD` | `u8 reg, typed(i32 rhs)` | Atomic register addition: `reg = (reg + rhs) & 0xFFFFFFFF` (mediated). |
| `0x22` | `BRA` | `u8 cc, u8 reg, typed(i32 rhs), u16 off` | Branch on False: If condition evaluates to **FALSE**, `ip += off`; `cc`: 0`=`, 1`!=`, 2`<`, 3`<=`, 4`>`, 5`>=`. |
| `0x23` | `JMP` | `u16 off` | Unconditional forward jump: `ip += off`. |
| `0x24` | `SUB` | `u8 reg, typed(i32 rhs)` | Atomic register subtraction: `reg = (reg - rhs) & 0xFFFFFFFF` (mediated). |
| `0x25` | `GUARD_ENTER` | `u16 abort_off, u8 header, [u16 throttle_ms], [pred_descriptor]` | Push guard into flat slot; activate continuous invariant monitoring. |
| `0x26` | `GUARD_EXIT` | — | Pop current guard slot; deactivate invariant monitoring. |
| `0x28` | `READSLOT` | `u8 reg, u8 sig_str_idx, u8 field_str_idx` | Read slot value into register (mediated). |
| `0x29` | `WAIT` | `u8 sig_str_idx, u8 field_str_idx, typed(u32 timeout_ms)` | Suspend until slot `$slot.<sig>.<field>` receives projection (or consumes pending event) or times out; writes status to `$timeout`. |
| `0x2A` | `CALL_EXT` | `u8 ns, u8 fn, u8 argc, argc × typed` | Invoke extension function; result written to `$ret` (mediated). |
| `0x2B` | `TYPEREG` | `u8 reg` | Type register decimal integer value as ASCII keystrokes with active pacing. |
| `0xFF` | `END` | — | Halt execution successfully. |

### 4.1 Compact Predicate Descriptor Specification (`pred_descriptor`)

`0x25 GUARD_ENTER` encodes invariants using a packed bitfield header and contiguous condition triplets:

#### Instruction Header Encoding
```
u16 abort_off: Relative forward jump offset on guard failure (base point: byte immediately following this instruction)
u8 header: Control bitfield packing
   bit 7:    logic_mode (0 = ALL conjunction, 1 = ANY disjunction)
   bit 4..6: triplet_cnt - 1 (3 bits, encoding 0..7 representing 1..8 triplets)
   bit 0:    has_throttle (0 = default 0ms unthrottled; 1 = explicit u16 throttle_ms follows)
   bit 1..3: Reserved (must be 0)
[u16 throttle_ms]: Present only if has_throttle == 1 (2 bytes LE)
```

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

#### Streaming Properties
- **Zero Redundant Length Headers**: Deserialization is deterministically governed by `triplet_cnt` and operand tags; `ip` advances directly to `<protected_body>` without an outer `pred_len`.
- **Zero Runtime Deserialization Overhead**: Deserialized once upon `GUARD_ENTER` into a fixed in-memory tuple `(throttle_ms, last_check_ms, abort_ip, loop_depth, parsed_pred)` for zero-unpacking slice re-evaluation.

---

## 5. Predicate Lowering & `OP_BRA` Offset Semantics

`\if` statements lower directly into standard `OP_BRA` and `OP_JMP` sequences.

### 5.1 Native Branch-on-False Semantics
`OP_BRA` execution semantics are strictly: **Branch forward if condition evaluates to FALSE**.

### 5.2 Syntax-Directed Short-Circuit Lowering Rules
1. **Conjunction Node (`\all`)**: False branches of each sub-predicate $P_i$ converge on the parent's `false_target`. The first $N-1$ successful tests fall through to $P_{i+1}$, and the final child falls through to the parent's `true_target`.
2. **Disjunction Node (`\any`)**: The compiler maps the first $N-1$ sub-predicates to their **Logical Dual Operators (Inverse `cc`)**, emitting `OP_BRA(invert(cc), ..., then_target)`. Any sub-predicate evaluating to true immediately jumps to the then body. The final child emits its native operator `OP_BRA(cc, ..., false_target)`.
3. **Dual Operator Inversion Mapping**:
   - `=` (0) $\leftrightarrow$ `!=` (1)
   - `<` (2) $\leftrightarrow$ `>=` (5)
   - `<=` (3) $\leftrightarrow$ `>` (4)

Lowering requires zero temporary registers and zero runtime stack allocations; relative offsets are resolved via backpatching.

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
When evaluating active guards, the VM iterates from outermost to innermost (`0` through `guard_depth - 1`). If any guard layer $i$ is violated, an immediate abort is triggered:
1. **Instruction Boundary Throttling**: At the head of the main dispatch loop:
   - If `throttle_ms == 0`: Checks all active guards after every completed macro instruction.
   - If `throttle_ms > 0`: Checks guards when `monotonic_ms - last_check_ms >= throttle_ms`.
2. **Delay Slicing**: When suspending during `\delay` (`OP_SLEEP`) or inter-keystroke pacing (`OP_PACE`), if `guard_depth > 0`, the VM slices total sleep duration into micro-quanta:
   $$\Delta t = \min(t_{\text{remaining}}, \Delta t_{\text{slice}})$$
   $$\Delta t_{\text{slice}} = \min_{0 \le j < \text{guard\_depth}} (t_{\text{throttle}, j} > 0 \ ? \ t_{\text{throttle}, j} : \text{DELAY\_SLICE\_QUANTUM})$$
   (base quantum `DELAY_SLICE_QUANTUM = 20ms`). Guards are evaluated upon each slice wake-up; violations trigger an immediate abort.
3. **Wait Slicing**: During `\wait` (`OP_WAIT`), if guards are active, suspension is sliced into the same per-quantum wake-ups, evaluating active guards at every quantum boundary to ensure indefinite waits remain guarded.
4. **CALL_EXT Blind Window**: Guard evaluation cannot cut into the interior of an extension call. Active guards are evaluated once on entry and once on return; long operations should use push streaming (`img.watch`) + `\wait`.

### 6.3 Force-Abort & Nested Scope Isolation
When a guard at level $i$ evaluates to false:
1. **Hardware Peripheral Reset**: Calls `hid.keys_release_all()` and `hid.mouse_release(0x1F)` to prevent hardware key hangs.
2. **Loop Frame Rollback**: `loop_stack = loop_stack[:guard_slots[i].loop_depth]`, rolling back loop frames belonging to the aborted scope while preserving outer loop contexts.
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
| `WAIT` | `0xFE` (`$timeout`) | `None` |
| `CALL_EXT` | `0xFF` (`$ret`) | `None` |
| Execution start (zeroing) | all general regs | `None` |

Execution start and every exit path additionally invoke `runtime.release_all()`.

---

## 8. Version Policy and History

`VERSION` gates the container format and opcode semantics. Bytecode consumers MUST reject unknown versions.

| Version | Key Additions and Changes |
|---|---|
| **v1** (gist era) | Unformatted bytecode. Immediate-only operands; fused sleep/pace. |
| **v2** | Container header (`MAGIC + VERSION`). `SLEEP` and `PACE` split. |
| **v3** | Typed operands (`TAG_IMM=0`, `TAG_REG=1`, `TAG_STR=2`); 32-register file; `SET`/`ADD`/`BRA`/`JMP`/`TYPEREG`; string table + `READSLOT`/`WAIT`/`CALL_EXT`; register-write mediation. |
| **v4** | Container `VERSION = 4`; native atomic subtraction `0x24 SUB`; continuous invariant guard opcodes `0x25 GUARD_ENTER` and `0x26 GUARD_EXIT` with flat slot model and delay slicing; revised `0x29 WAIT` with explicit `(sig_str_idx, field_str_idx)`. |

---

# Appendix A — Implementation Notes (Non-Normative but Binding for Ports)

- **Bulk-Memory Discipline**: On memory-constrained embedded targets, GC heap is restricted to small control-plane objects. Five rules bind every bulk buffer:
  1. *On demand*: Buffers allocate on first touch, costing zero RAM if unused.
  2. *Own forever*: Once allocated, owned by the managing layer; steady-state buffers never shrink. Macro execution streams are strictly bounded by one snapshot slot (48 KiB, `SNAP_SLOT_BYTES`). Full-frame UI captures bypass device RAM via out-of-band direct LAN fetching.
  3. *Hot path allocates nothing*: Steady state performs pointer arithmetic and free-list operations only.
  4. *Deterministic failure*: Exhaustion fails gracefully (returning error sentinels `$ret < 0`), never crashing the server task.
  5. *Copy budget*: Hot-path bytes are copied at most twice.
- **Arena Taxonomy**:
  - *Slot pool*: Fixed-size, independent lifetimes (snapshots).
  - *Bump arena*: Variable-size, batch lifetime (const store, reset wholesale per compilation).
  - *Stream slot*: Single 48 KiB buffer matching `SNAP_SLOT_BYTES`.
- **Budgets (Profile Constants)**: Snapshot slot 48 KiB ($128^2$ RGB) $\times$ 8; const arena 256 KiB; stream assembly slot 48 KiB; per-connection reassembly $2 \times (8\text{ KiB} + 8)$.
- **Cache Discipline**: Handle ownership flags should reside adjacent to the register file; hoisting flags into dispatch-loop locals enables single-cycle fast paths.
