# MYCRO VM Specification

**Status: normative.** This document is the authoritative contract for the
MYCRO macro bytecode and its execution machine. It is written to be
independent of the implementation language (the reference implementation
is CircuitPython; a C/Rust port must be able to conform without reading
the reference source).

- **Part I** specifies the VM core: bytecode container, register file,
  execution semantics, and the register-write mediation points. The VM
  core knows nothing about resources, namespaces, or peripherals.
- **Part II** specifies the middleware contract that *this* macro system
  layers on the VM: the runtime registry, the type discipline, and the
  handle ownership model. A different host system could reuse the Part I
  core with different middleware.

The DSL surface syntax is *not* part of this specification; it is a
compiler input language documented in AGENTS.md / ROADMAP.md. What this
specification binds is the bytecode the compiler must emit and the
semantics the VM must preserve.

---

# Part I — VM Core

## 1. Execution model

A register machine executing a linear bytecode stream. State per
execution:

- `regs[]`: the register file (see §2)
- `ip`: instruction pointer into the opcode stream
- a loop stack (LOOP/LOOP_END frames: remaining count + return address),
  bounded by bytecode size; no runtime heap growth
- string table (read-only, from the container header)

Execution is cooperative: SLEEP/PACE/WAIT/CALL_EXT may suspend the
coroutine; abort is task cancellation. On **every** exit path (normal,
error, abort) the VM releases all held keys/buttons via the HID provider
and releases all handle resources (Part II §4) — no exception.

## 2. Register file

- `MAX_REGS` general registers, each holding one **i32** (two's
  complement, wraparound arithmetic). `MAX_REGS` is a **build-profile
  constant**, not a specification limit; the current profile is 32.
- Register operands in bytecode are **u8 addresses**; the specification
  ceiling is therefore 256 registers. Addresses 0xF0–0xFF are reserved
  for special registers.
- Current special registers: 0xFD = reserved boundary (never stores),
  0xFE = `$timeout` (written by WAIT only), 0xFF = `$ret` (written by
  CALL_EXT only).
- All general registers are zeroed at execution start.

## 3. Bytecode container

```
u8 MAGIC (0xA5)
u8 VERSION (3)
u8 str_count, then str_count × (u8 len + UTF-8 bytes)   -- string table
opcode stream to EOF
```

Execute/disassemble MUST reject a mismatched MAGIC/VERSION.

### Typed operand encoding

Multi-operand instructions use typed operands:

```
tag u8:  0 = immediate (i32 LE)   1 = register (u8)   2 = string (u8 index)
```

The three tags are exactly the argument tenses of the DSL (spec/dsl.md
§6): inline compile-time constant / string-table compile-time constant /
runtime register. Tag 2 is valid only in CALL_EXT arguments. Semantic
types (Part II §2) never reach bytecode.

## 4. Opcode set (v3)

| Opcode | Operands | Semantics |
|---|---|---|
| 0x01 CHAR | u8 ascii | Type character (Shift auto via provider keymap) |
| 0x02 KEY | u8 keycode | Single key tap |
| 0x03 COMBO | u8 mod_mask, u8 keycode | mask bit0-3 = Ctrl/Shift/Alt/GUI; keycode 0 = pure modifier |
| 0x04 SLEEP | typed(u16 ms) | One-shot sleep (DSL `\delay`) |
| 0x05 MCLICK | typed(u8 btn), typed(u16 count) | Mouse click |
| 0x06 MMOVE | typed(i16 dx), typed(i16 dy) | Relative mouse move |
| 0x07 PACE | typed(u16 ms) | Set inter-action interval (DSL `\pace`) |
| 0x10 LOOP | typed(u8 count), u16 len | len = body bytes incl. LOOP_END; count 0 skips |
| 0x11 LOOP_END | — | Decrement/jump back or pop |
| 0x12/0x13 KEY_DOWN/KEY_UP | typed(u8 keycode) | keycode 0 = release all |
| 0x14/0x15 MDOWN/MUP | typed(u8 btn) | Mouse button hold/release |
| 0x20/0x21 SET/ADD | u8 reg, typed(i32) | Register write (mediated, §5); ADD wraps i32 |
| 0x22 BRA | u8 cc, u8 reg, typed(i32 rhs), u16 off | If condition false, `ip += off`; cc: 0`=`,1`!=`,2`<`,3`<=`,4`>`,5`>=` |
| 0x23 JMP | u16 off | Unconditional forward skip |
| 0x28 READSLOT | u8 reg, u8 sig, u8 field | Read middleware slot into register (mediated, §5) |
| 0x29 WAIT | u8 sig, typed(u32 timeout_ms) | Suspend until signal's next fire; `$timeout` = 1 on timeout (mediated write) |
| 0x2A CALL_EXT | u8 ns, u8 fn, u8 argc, argc × typed | Middleware call; return written to `$ret` (mediated, claim per Part II §4) |
| 0x2B TYPEREG | u8 reg | Type register's decimal digits (one pacing action) |
| 0xFF END | — | Halt |

Branches are forward-only; there is no general backward jump except
LOOP_END. There is no arithmetic beyond SET/ADD.

## 5. Register-write mediation (VM ↔ middleware interface)

The VM core guarantees that **every** register write passes through the
middleware before the value lands:

```
runtime.reg_write(dst: u8, src: u8 | None)
```

`dst` is the register being written; `src` is the source register when
the value comes from another register (SET reg←reg), else None. The
complete, closed set of write sites:

| Site | dst | src |
|---|---|---|
| SET imm | reg | None |
| SET reg | reg | source reg |
| ADD | reg | None |
| READSLOT | reg | None |
| WAIT | 0xFE | None |
| CALL_EXT | 0xFF | None |
| execution start (zeroing) | all general regs | None |

The VM core does not know what the middleware does with these calls
(including nothing). Execution start and every exit path additionally
call `runtime.release_all()`.

## 6. Version policy and history

VERSION gates the container format and opcode semantics. A bytecode
consumer MUST reject unknown versions. Behavior changes that keep the
format and opcode semantics intact (e.g. raising the profile's MAX_REGS)
do not bump VERSION.

| Version | Changes |
|---|---|
| v1 (gist era) | No container header. CHAR/KEY/COMBO/DELAY(fused sleep+pace)/MCLICK/MMOVE/LOOP/LOOP_END/KEY_DOWN/KEY_UP/MDOWN/MUP/END; immediate-only operands |
| v2 | Container header (MAGIC+VERSION). DELAY split into SLEEP (one-shot) + PACE (inter-action interval) |
| v3 | Typed operands (imm/reg/str); register file (16 → profile constant); SET/ADD/BRA/JMP/TYPEREG; string table + READSLOT/WAIT/CALL_EXT; register-write mediation contract (this specification) |

---

# Part II — Macro-System Middleware

The middleware is everything the VM delegates to: the runtime registry,
the type discipline, and resource (handle) ownership. Normative for
MYCRO; not part of the VM core.

## 1. Runtime registry

- **Namespaces**: `ns → {fn: (arg_types, return_type, callable)}`.
  Registration is platform wiring (code.py on device, fakes in tests).
- **Signals**: named events with `asyncio`-style semantics; `\wait`
  observes the **next fire after entry** — a fire with no waiter is
  discarded.
- **Slots**: last-published state under a signal name, readable anytime
  via `$sig.field`; missing field reads as 0.

## 2. Type discipline (constraint-only)

The type system constrains **argument passing** — it performs no type
computation, inference, or propagation beyond the copy rules below.

Argument types and the forms they accept:

| Type | Accepted argument form |
|---|---|
| `int` | immediate (incl. `0x` hex) / `$var` / slot reference |
| `color` | as `int` (packed RGB i32; `\rgb{}` sugar is a deferred v3.1 item) |
| `str` | literal source text (string table); **compile-time form only — never a runtime value** (cannot be produced, stored, or returned; runtime data enters as `bytes` handles) |
| `point` / `rect` | one brace group of 2 / 4 comma-separated values; each element is an `int`-form argument; the compiler expands them into consecutive typed operands |
| handle types (`snap`, `tpl`, …) | **only** a `$var` whose recorded type matches |

Compile-time **type environment** (copy rules, exhaustive):

| Event | Environment update |
|---|---|
| `\call{ns.fn}...` | type(`$ret`) := fn's declared return type |
| `\set{s}{$ret}` | type(s) := type(`$ret`); handle types **move** (type(`$ret`) reverts to int) |
| `\set{s}{$other}` | type(s) := type(other); handle types **move** (type(other) reverts to int) |
| `\set{s}{literal}` / `\add` | type(s) := int |
| `\wait` | type(`$timeout`) := int (constant) |

A handle-typed variable used where a non-matching type is required (or
vice versa), an untraceable handle argument, `\add` on a handle, and
`\ifnum` on a handle are **compile errors** — with one exception:
`$ret` is always comparable in `\ifnum`, because it carries error
sentinels (negative values) alongside results, and checking it is the
mandatory failure path. The `$ret` exemption covers comparison only;
passing `$ret` as a handle argument still requires the current call to
have returned that handle type.

## 3. Write discipline (exhaustive)

| Storage | Written by |
|---|---|
| user registers | SET / ADD only |
| `$ret` | CALL_EXT only (None return writes 0) |
| `$timeout` | WAIT only |
| signal slots | the middleware's event publish only — never a function-return channel |

## 4. Handle ownership model

Handles are i32 identifiers of middleware-side resource table entries
(snapshot pixels, templates, …). The VM holds no resource knowledge;
the middleware owns tables, per-register ownership flags, and release.

`runtime.reg_write(dst, src)` semantics, in order:

1. If `dst` owns a handle → release the resource (release may be
   asynchronous/deferred).
2. If `src` is given and owns a handle → **move** ownership to `dst`
   (the source register reverts to a plain integer; reusing it as a
   handle fails at the middleware).
3. If a handle-returning CALL_EXT has just completed and `dst` is 0xFF →
   the pending handle is claimed by `$ret`.

`release_all()` runs at execution start and on every exit path.
Consequences: live handles are bounded by the register count; overwrite
*is* release (`\set{s}{0}` frees); looping re-snapshotting into the same
variable cannot leak; macro end frees everything.

Error convention for resource namespaces: `$ret < 0` on failure, details
to the debug console.

## 5. Compile-time const store

Compile-time constants too big or too structured for operand encoding
(e.g. `\rect` pixel literals) are preprocessed by the compiler into a
middleware-side **const store** (`add_const`/`const_entry`), reset on
each compilation. The bytecode references them by small integer id via
`CALL_EXT sys.const <cid>`, which materializes a normal handle — no
bytecode format change, and the standard ownership rules (claim/move/
overwrite-release/exit-release) apply unchanged. Const bytes are
batch-lifetime bulk resources (§6): a per-compile reset invalidates
them wholesale.

## 6. Bulk resource backing

Bulk byte resources (handle payloads, const-store pixels) are backed by
middleware-managed storage, not by per-operation allocation from the
host runtime's default memory manager. Only the observable behavior is
normative here:

- A handle's backing storage is bound to the handle entry; release
  through ANY path (§4) returns the storage to the managing layer,
  where it is reusable by later operations.
- Backing budgets are profile constants with the same standing as
  MAX_REGS (Part I §2) — platform budgets, not spec limits.
- Budget exhaustion fails the producing operation deterministically
  (error convention, `$ret < 0`; a compile error at compile time) —
  never silently, never by stalling on the host allocator.

How the middleware organizes that storage — arena structures, growth
policy, copy discipline — is an implementation matter and does not
belong to this contract; the current implementation's design is
recorded in Appendix A.

## 7. Peripheral abstraction

All hardware/host interaction goes through providers (HIDProvider today;
the img capture provider over the Phase 2 channel). The VM and compiler
never import runtime-specific modules.

---

# Appendix A — Implementation notes (non-normative but binding for ports)

- **Bulk-memory discipline** (the current implementation of Part II §6;
  revise freely as long as §6's observable behavior holds). On a
  memory-constrained embedded target the host runtime's default memory
  management is never acceptable for bulk data; the GC heap is for
  small control-plane objects only (envelopes, bytecode, tables,
  handle entries). Five rules bind every bulk buffer:
  1. *On demand*: buffers appear on first touch, never speculatively
     at boot; an arena the workload never touches costs nothing.
  2. *Own forever*: once allocated, owned by the managing layer, never
     returned to the GC; may grow to the workload's high-water mark,
     never shrinks.
  3. *Hot path allocates nothing*: steady state does pointer
     arithmetic and free-list moves only; cold paths (first use,
     growth past the previous high-water mark) may allocate, once.
  4. *Deterministic failure*: exhaustion/overflow fails the operation
     itself — never a GC retry or an allocation stall.
  5. *Copy budget*: a byte on the receive hot path is copied at most
     twice (socket→reassembly, reassembly→destination); transports
     receive into self-managed storage and hand consumers views.
     Isolating copies off the hot path (e.g. UI-held data that must
     outlive later traffic) are permitted and must be justified in a
     comment.
- **Arena taxonomy** (structure follows access pattern — axes:
  concurrent live count, entry size, lifetime): *slot pool* (several
  concurrent, fixed-size, independent lifetimes — snapshots; future
  match frames); *bump arena* (many, variable-size, batch lifetime —
  the const store, reset wholesale per compile); *high-water buffer*
  (one tenant, variable size, short lifetime — stream assembly, one
  buffer because reverse requests are single-in-flight). Do not force
  one structure on all buffers: a contiguous consumer cannot gather
  from scattered pool slots without an extra full-size copy;
  batch-free is O(1) under a bump arena but O(n) under a pool;
  arbitrary-order release inside one flat buffer is a general heap —
  the fragmentation this discipline exists to prevent.
- **Current budgets** (profile constants, Part II §6): snapshot slot
  48 KiB (128² RGB) × 8; const arena 256 KiB; stream rejection cap
  2 MiB; per-connection reassembly 2×(8 KiB+8).
- **Memory verification**: steady-state free_mem stays flat under
  sustained bulk traffic; pool exhaustion fails deterministically and
  recovers on release. A hot path found allocating (GC audit, a
  traffic-correlated free_mem sawtooth) is a defect, not a tuning
  issue.
- **Cache discipline**: handle-ownership flags must be stored adjacent
  to the register file (same struct, same cache line). In compiled
  implementations, hoist the flags word into a dispatch-loop local so
  the per-write cost is one register-resident AND+branch; naive layouts
  that chase a pointer per write can dominate a hot loop (double memory
  traffic). Releases are always deferred off the write path.
- **Fast path**: a zero live-handle count short-circuits all mediation
  checks to a single perfectly-predicted branch.
- CircuitPython API verification discipline applies to any device-side
  code: shared-bindings docs > same-pattern library usage > REPL probe;
  never infer from MicroPython/CPython.
