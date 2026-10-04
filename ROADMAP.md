# ROADMAP — MYCRO Evolution Plan

> Current state, bug details, and capability research conclusions: see [AGENTS.md](AGENTS.md).
> Target hardware: ESP32-S3 **with PSRAM** (N8R8 recommended); current runtime CircuitPython 10.x, library versions pinned via circup.
> **Architectural discipline (always in effect)**: the compiler/VM/protocol layers must not import any runtime-specific modules; all hardware interaction goes through the HIDProvider abstraction — this carries a possible MicroPython migration in Phase 3 and is also the mock point for unit tests.

---

## Phase 1 — Foundation Rework

**Goal**: formalize the skeleton + fix critical bugs + asyncio-ify. No new features.

### 1.1 Formalization baseline
- [ ] Repo structure: `src/` (CIRCUITPY layout: code.py / lib/macro.py / index.html), `tests/`, `tools/deploy.sh`
- [ ] **HID abstraction layer**: funnel `macro.py`'s hardware dependencies into a HIDProvider interface (keyboard/layout/mouse operations + Keycode table), decoupled from adafruit_hid
- [ ] CPython unit-test baseline: mock HIDProvider, 100% of compiler/VM logic coverable on PC
- [ ] Deploy script: rsync to the CIRCUITPY mount point + version-pinned `circup install` (`requirements-device.txt`)

### 1.2 Critical bug fixes (details in AGENTS.md §5)
- [ ] B1: enforce the nesting depth limit (`_cmd_rep` carries a depth counter, compile error over 2 levels)
- [ ] B2: wrap `execute()` in try/finally so every exception path releases all keys/buttons
- [ ] B3: converge the input decoding chain — remove `htmldecode`; `urldecode` paired with the pinned library version, or parse `request.body` directly
- [ ] B4: align docs with code (bytecode cap 4096, nesting levels, index.html cheat sheet)
- [ ] B5: remove dead code (`_emit_i8`, unreachable OP_CHAR fallback)

### 1.3 DSL v2 syntax overhaul (right after bug fixes, before asyncio)

**Design principles**: stay backward-compatible with common usage (plain text, `\key`, `\ctrl+c`, `\rep` unchanged); new capabilities all live in the `\` escape namespace and never occupy printable characters; **keep the syntax core minimal — this round only fixes the lexer and clarifies semantics; no logic/variables/wait or other new structures**.

**Lexer layer**
- [ ] Line continuation: `\` + newline (LF/CRLF) → drop the newline (no Enter emitted), so long macros can be wrapped into logical blocks
- [ ] Comments: `\#` to end of line is not sent; combines with line continuation for maintainable long macros
- [ ] Fix `_read_braces` depth counting: skip `\{` `\}` `\\` escapes (a body containing escaped braces currently misreports "Unmatched {")

**Command semantics layer**
- [ ] Split `\delay`'s dual semantics: `\delay{t}` = sleep once only; new `\pace{t}` = set the interval after subsequent actions (old behavior deprecated, compile warning during migration)
- [ ] Argument-style unification audit: `\kdown{ctrl}` brace style vs `\ctrl+c` chained style — decide one set of rules and document it

**Bytecode format reserve (no new syntax)**
- [ ] Add a version header byte to bytecode; generalize LOOP's len field into the uniform jump offset for all block structures (solves the dead body_len parameter)

**Deferred to a unified design (explicitly NOT in v2, to avoid complexity blowup)**: subroutines `\def`/`\call`, the `\wait` primitive + signal/slot, `\call{ext_functions}` + runtime exposure/import, logic expressions and variables — these capabilities are mutually coupled; design the DSL surface and opcode semantics together when the real requirements of Phase 2 (network messages) / Phase 3 (vision) land.

### 1.4 asyncio-ification
- [ ] Main loop `asyncio.run()`: HTTP `server.poll()` as one task, macro execution as another
- [ ] VM: `execute()` becomes `async def`; all internal `time.sleep` become `await asyncio.sleep`
- [ ] New `/macro/abort` → `task.cancel()` (with B2's finally guaranteeing key release)
- [ ] Raw socket listener stub task (protocol filled in Phase 2); **early on-device validation** of CircuitPython asyncio TCP stability (known risk #10775); if unstable, Phase 2's machine channel uses WebSocket
- [ ] Enable Bearer token auth (built into adafruit_httpserver)

**Acceptance**
- All P0 bugs have unit tests, all green
- DSL v2: all index.html example macros compile to unchanged results (pinned by regression tests); line continuation/comments/`\pace`/brace-escape fix each have unit tests; cheat sheet and AGENTS.md §3 updated in sync
- During a long macro (`\delay{30}`), `/compile` and `/macro/abort` respond normally; no stuck keys after abort
- On-device 1h stress test (random macros + concurrent requests): no hard fault, stable `gc.mem_free()`

---

## Phase 2 — Network Message Protocol

**Goal**: unified message semantics across HTTP / WebSocket / raw TCP, supporting remote orchestration, status push, and future vision event streams.

### 2.1 Encoding selection
- [ ] msgpack (compact, needs porting validation) vs JSON (built-in, zero-dependency fallback): decide by measured codec speed, RAM peak, and JS-side interoperability on the S3
- [ ] Output: benchmark data + decision record (ADR)

### 2.2 Protocol implementation
- [ ] Message envelope: `{v, type, id, ts, payload}` (v = protocol version)
- [ ] First batch of types: `macro.submit` / `macro.status` / `macro.abort` / `macro.result` / `device.info` / `log.push` (downstream over SSE/WS)
- [ ] Raw socket frame format: u32 LE length prefix + envelope body; WebSocket reuses the same envelope
- [ ] REST stays as the human/debug channel; machine channels go over socket/WS
- [ ] Fill Phase 1's socket stub into a full codec + dispatcher

**Acceptance**
- The same `macro.submit` behaves identically across all three channels
- A PC-side Python client example runs end to end
- The protocol documentation is sufficient for a third party (host companion program) to implement a client independently

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

- **Version pinning**: record firmware / bundle / mpy-cross versions in the repo; library upgrades must re-verify B3's decoding behavior
- **On-device regression**: run on-device regression at the end of each Phase; no phase counts as done without hardware validation
- **Documentation sync**: update AGENTS.md at the end of each Phase (architecture changes, new opcodes, protocol summary)
- **Risks & mitigations**:
  - CircuitPython asyncio + raw TCP stability (#10775) → early on-device validation in 1.4; fallback is the WebSocket channel
  - PSRAM large-buffer behavior differences → measure first in 3.1
  - msgpack compatibility → selection validation in 2.1, JSON as fallback
