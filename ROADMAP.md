# ROADMAP — MYCRO Evolution Plan

> Current state, bug details, and capability research conclusions: see [AGENTS.md](AGENTS.md).
> Target hardware: ESP32-S3 **with PSRAM** (≥8MB PSRAM recommended — the vision phase needs large buffers); runtime CircuitPython 10.x, library list in requirements-device.txt (per-device version snapshots are kept out of the repo, in private memory).
> **Architectural discipline (always in effect)**: the compiler/VM/protocol layers must not import any runtime-specific modules; all hardware interaction goes through the HIDProvider abstraction — this carries a possible MicroPython migration in Phase 3 and is also the mock point for unit tests.

---

## Phase 1 — Foundation Rework

**Goal**: formalize the skeleton + fix critical bugs + asyncio-ify. No new features.

### 1.1 Formalization baseline ✅
- [x] Repo structure: `src/` (CIRCUITPY layout: code.py / lib/macro.py / index.html), `tests/`, `tools/deploy.sh`
- [x] **HID abstraction layer**: `macro.py`'s hardware dependencies funneled into a HIDProvider interface (keyboard/layout/mouse operations + Keycode table), decoupled from adafruit_hid
- [x] CPython unit-test baseline: mock HIDProvider, 100% of compiler/VM logic covered on PC
- [x] Deploy script: rsync to the CIRCUITPY mount point + version-pinned `circup install` (`requirements-device.txt`)

### 1.2 Critical bug fixes ✅
- [x] B1: enforce the nesting depth limit (`_cmd_rep` carries a depth counter, compile error over 2 levels)
- [x] B2: `execute()` wrapped in try/finally; every exception path releases all keys/buttons
- [x] B3: input decoding chain converged — `htmldecode` removed; `urldecode` kept with a note on the pinned library dependency
- [x] B4: docs aligned with code (bytecode cap 4096, nesting levels, index.html cheat sheet)
- [x] B5: dead code removed (`_emit_i8`; unreachable OP_CHAR fallback now raises explicitly)

### 1.3 DSL v2 syntax overhaul ✅

**Lexer layer**
- [x] Line continuation: `\` + newline (LF/CRLF) → drop the newline (no Enter emitted), so long macros can be wrapped into logical blocks
- [x] Comments: `\#` to end of line (consumes the newline, no Enter)
- [x] Fixed `_read_braces` depth counting: skips `\{` `\}` `\\` escapes

**Command semantics layer**
- [x] Split `\delay`'s dual semantics: `\delay{t}` = sleep once (OP_SLEEP); new `\pace{t}` = set the interval after subsequent actions (OP_PACE)
- [ ] Argument-style unification audit: `\kdown{ctrl}` brace style vs `\ctrl+c` chained style (minor; decided together with later structural capabilities)

**Bytecode format reserve**
- [x] Bytecode gains a version header (MAGIC 0xA5 + VERSION 2); LOOP's len becomes live semantics (bounds check + count=0 skips the body)

### 1.4 asyncio-ification ✅
- [x] Main loop `asyncio.run()`: HTTP `server.poll()` as one task, macro execution as another
- [x] VM: `execute()` is `async def`; all internal `time.sleep` became `await asyncio.sleep`
- [x] New `/macro/abort` → `task.cancel()`; `/macro/status` query; HTTP stays responsive during macro execution
- [x] Raw socket listener stub task (port 7373, fully exception-isolated); stub verified running on device; live-traffic stability lands with Phase 2 (#10775)
- [x] Bearer token auth (`MYCRO_TOKEN` via settings.toml; unset = dev mode)

**Acceptance status**
- ✅ All P0 bugs have unit tests, all green
- ✅ DSL v2: index.html example macros pinned by regression tests; new lexer/semantics features each have unit tests
- ✅ On-device validation passed (user-confirmed 2026-10-04): deploy, runtime WiFi connect, HTTP service, macro execution/abort

---

## DSL v3 — Syntax Extension (finalized; all stages landed before Phase 2)

### Core rules
1. **Value-position interpolation**: `$name` interpolation only takes effect in **value positions** (each command's parameter list declares its types explicitly); in code-body positions (the bodies of `\rep`/`\def`/`\ifnum`) and in plain text, `$` is always a literal character
2. Within a value position, `$name` matches `[a-z0-9_.]` greedily; `${name}` delimits explicitly; a literal `$` is written `\$`
3. **Dual-semantics split**: `$name` (value position) = integer semantics (counts/ms/keycodes/coordinates/comparands); `\val{name}` (text position) = renders the decimal digit key sequence
4. `\def` parameters `#1`–`#9` are pure textual single-pass substitution, expanded first; semantics are uniquely determined by the landing site
5. Built-in command names are ≥2 letters (single letters permanently reserved for bare keys)
6. Values not decidable at compile time → emit runtime-evaluated instructions (value position tag=reg / text position OP_TYPEREG / slot positions desugared to READSLOT)

### Type system (the extensibility foundation)
- Variables are physically **i32** only, with three semantic roles: numbers / packed small values (color, keycode, etc.) / object handles
- **Semantic types hang on ext function signatures** (the ns registry is precise to parameter types/return type/produced-slot contract), checked at compile time, erased at runtime
- Large objects live in the **runtime object table + i32 handles** (tpl, conn, etc.); handles and numbers cannot be mixed arithmetically (static check)
- Bytecode physical tags are always just imm/reg/str — semantic types never enter the bytecode — **a future new type = registering a new signature + new ext functions, zero syntax growth**
- Domain predicates (e.g. color tolerance comparison) are always ext functions returning i32 to `$ret`; branching always goes through `\ifnum`

### Stage A — User macros (pure compile-time, bytecode unchanged) ✅
- [x] `\def{name}[argc]{body}` (argc 0–9); a `\name` call takes argc brace groups; resolution priority: built-in commands → user macros → key names
- [x] Constraints: top-level only, define-before-use, no redefinition, no clash with built-ins/key names; circular references / wrong argc → compile error; expansion depth counts into MAX_NEST

### Stage B — Variables / conditionals / rendering (bytecode v3) ✅
- [x] 16 i32 registers (zeroed at execution start), `\set{x}{i32}` / `\add{x}{i32}`; `0x` hex literals supported
- [x] Special read-only: `$ret` (0xFF), `$timeout` (0xFE); signal slots `$ns.field` (READSLOT)
- [x] `\ifnum{$a}{op}{b}{then}[{else}]` (op ∈ `= != < <= > >=`; left operand must be a variable, right operand i32 or `$var`; compiles to forward BRA/JMP, zero runtime stack growth)
- [x] `\val{name}` (OP_TYPEREG; counts as one pacing action; the argument must be defined, else compile error)
- [x] Static checks: reading before `\set` is an error (conservative lexical-order judgement); `\call` arguments checked against the function signature
- [x] All value positions accept `$var` interpreted per the position's type (incl. `\kdown{$k}` with a register keycode)

### Bytecode v3 ✅
- [x] Header: MAGIC(0xA5) + VERSION(3) + string table (u8 count; u8 len + bytes; signal/ns/fn/template names, deduplicated)
- [x] New opcodes: 0x20 SET, 0x21 ADD (reg, i32); 0x22 BRA (cc, reg, i32, off16), 0x23 JMP (off16); 0x28 READSLOT (reg, sig, field); 0x29 WAIT (sig, timeout_ms u32, 0 = wait forever); 0x2A CALL_EXT (ns, fn, argc, typed args); 0x2B TYPEREG (reg)
- [x] Typed-operand rework: MMOVE / SLEEP / PACE / MCLICK.count numeric operands gain a tag prefix (0=imm, 1=reg); tag=0 is wire-identical to v2

### Stage C — Waiting / runtime extension (syntax and VM landed; net/img semantics arrive with the phases) ✅
- [x] `\wait{signal}` / `\wait{signal}{sec}`: suspend the coroutine by name (asyncio.Event); runtime event sources register signals and write payload slots; timeout writes `$timeout`; abort can interrupt
- [x] `\use{ns}`: compile-time dependency declaration checked against the runtime namespace table; missing = compile error; emits no bytecode
- [x] `\call{ns.fn}{arg}...`: return value written to `$ret`, structured output written to `$ns.*` slots; the `sys` namespace is registered as minimal validation (`sys.free`); `net` with Phase 2, `img` with Phase 3
- [x] Runtime registry (`src/lib/runtime.py`): unified registration and lookup of namespaces/signatures/signals/slots; platform wiring in code.py, fakes in tests
- [x] Two-pass compiler: Pass 1 collects top-level `\def` (build the table, check circular references) and `\use` (check namespaces); Pass 2 does expansion + variable allocation + static checks; argument parsing upgraded to descriptors (imm/reg/str); shared deduplicated string table
- [x] Nesting limit MAX_NEST = 4; compiler nesting is iterative (explicit heap stack, no Python recursion) — CircuitPython's pystack is only a few KB, so nesting depth is bounded by bytecode size, not call frames

### Boundaries
- Variables are i32 only; strings are not first-class (strings pass opaquely via the string table / signal slots)
- No general expressions / multiplication / division (`\set`/`\add` cover counting; complex math sinks into ext functions; the interpreter's own internals are not bound by this)
- User macros are compile-time expansion only (no runtime subroutines)
- BRA/JMP are forward-only (no `\while`); no else-if syntax (nest `\ifnum` instead)
- `\val` is decimal only
- Constructor sugar (e.g. `\rgb{r,g,b}`) is **NOT in v3**; arrives as v3.1/v4 with Phase 3's img namespace

### Scheduling
- **Stages A, B, C**: all landed before Phase 2 (as planned); the `\call` argument convention feeds into Phase 2 protocol design
- **Stage C namespaces**: `sys` minimal validation done; `net` semantics with Phase 2, `img` semantics with Phase 3

---

## Phase 2 — Network Message Protocol

**Goal**: unified message semantics across HTTP (human/debug) and a raw TCP machine channel, supporting remote orchestration, status push, and future vision event streams.

### 2.1 Encoding selection ✅
- [x] JSON selected (ADR-1 in docs/protocol.md): neither CircuitPython bundle ships a msgpack library, so msgpack would mean an unpinned vendored codec; on-device benchmark (ESP32-S3, CP 10.3.0): 0.42 ms / 416 B churn for an 89 B envelope, 3.27 ms / 2.5 KB for a 1.2 KB macro-submit envelope — far below macro-execution timescales; bulk binary bypasses JSON via binary frames
- [x] Wire format settled: u32 LE frame header (bit31 kind, 8 KB caps) + fixed-length auth prologue (magic + version + SHA-256 token digest); codec landed in `lib/codec.py`

### 2.2 Protocol implementation
- [x] Message envelope: `{v, type, id, ts, payload}` (v = protocol version)
- [x] First batch of types: `macro.submit` / `macro.status` / `macro.abort` / `macro.result` / `device.info` / `event.push` + `net.msg` (inbound event / outbound macro push for the DSL `net` namespace)
- [x] Raw socket frame format: u32 LE length prefix (bit31 kind) + envelope body
- [x] REST stays as the human/debug channel; the machine channel is raw TCP only (single-channel decision: no WebSocket/SSE — pushes broadcast on the same authenticated stream)
- [x] Fill Phase 1's socket stub into a full codec + dispatcher

**Acceptance**
- The same `macro.submit` behaves identically over HTTP and raw TCP
- The PC-side reference client (`tools/client.py`) runs end to end
- The protocol documentation is sufficient for a third party (host companion program) to implement a client independently
- Dual-end on-device test suite (`tools/devicetest.py`) fully green on real hardware

---

## Phase 3 — Onboard Image Recognition

**Goal**: pixel/template matching MVP first; CNN decisions driven by measured data.

### 3.1 Pre-validation & platform decision (finish before writing any vision code)

**Principle: verify before migrating — no research on unselected platforms; MVP code is only written on the decided platform.**

- [ ] **Dual benchmarks in parallel**:
  - CP side: measure ulab convolve/dot at the target resolution → judge latency feasibility of template matching and a micro CNN forward pass
  - MP side: **MicroPython spike brought forward** (a few days) — verify ① stability of micropython-lib `usb-device-keyboard/mouse` composite HID on S3 (incl. coexistence with REPL) ② microlite+esp-nn inference benchmark ③ WiFi+HID concurrency
- [ ] Image source decision: host screenshots pushed over the network (A, expected winner, reuses the Phase 2 protocol directly) vs `espcamera` onboard camera (B)
- [ ] Measure PSRAM heap with large buffers (bitmap + intermediate arrays)
- [ ] **Platform decision ADR**: CP template matching sufficient → stay on CP; insufficient and spike passes → migrate to MP (vision developed directly on MP); spike fails → stay on CP + dual-chip fallback

### 3.2 Template matching MVP (platform decided by 3.1)
- [ ] `VisionProvider` abstraction (`capture() -> ulab.ndarray`), network screenshot source first
- [ ] ulab matcher: grayscale NCC template matching + color threshold / region diff detection
- [ ] Macro ↔ vision integration: via the extension mechanism designed by then (`\wait` primitive + signal/slot + runtime-exposed functions, see the deferred items in 1.3); the DSL surface and opcode semantics are decided then

### 3.3 CNN recognition (trigger: 3.1 benchmarks / 3.2 field results show template matching is insufficient)
> The platform is already decided in 3.1; this section is just execution, no more migration uncertainty.
- [ ] If staying on CP: hand-written micro CNN forward pass on ulab (96×96 grayscale, ≤3 layers, im2col+dot+convolve); if that falls short → dual-chip (a second S3 running ESP-IDF+ESP-DL, returning labels/coordinates over UART/ESP-NOW/WiFi, zero changes on the CP side)
- [ ] If migrated to MP: go straight to microlite (TFLM+esp-nn, ~10× speedup)
- [ ] Keep host-side inference throughout (device uploads screenshots, receives labels) as the control baseline
- [ ] Training/deploy pipeline: train on PC → export weights to a device-readable format (binary + Phase 2 protocol wrapping) → push updates over the network

**Acceptance**
- MVP: screenshot → match → trigger macro end-to-end latency is measurable (target < 500ms, revised after measurement)
- CNN: the chosen task (fixed UI-element classification) meets accuracy/latency targets, or produce a formal decision record for the host-side approach

---

## Cross-phase items (ongoing)

- **Version management**: the library list and compatibility constraints (e.g. the form_data no-decode assumption) live in the repo; per-device firmware/library version snapshots live in private memory, not the repo; library upgrades must re-verify the decoding behavior
- **On-device regression**: run on-device regression at the end of each Phase; no phase counts as done without hardware validation
- **Documentation sync**: update AGENTS.md at the end of each Phase (architecture changes, new opcodes, protocol summary)
- **Risks & mitigations**:
  - CircuitPython asyncio + raw TCP stability (#10775) → early on-device validation in 1.4; fallback is the proven HTTP channel
  - PSRAM large-buffer behavior differences → measure first in 3.1
