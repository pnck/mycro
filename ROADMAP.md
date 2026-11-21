# ROADMAP — MYCRO Evolution Plan

> Operational manual & runtime architecture: see [AGENTS.md](AGENTS.md).  
> **Architectural discipline**: The compiler, virtual machine, and wire protocol remain strictly runtime-agnostic; all physical hardware interactions funnel through the `HIDProvider` abstraction.

---

## 1. Macro DSL

The domain-specific language designed for expressing hardware-level input automation, physical timing, and reactive logic with zero host software footprint.

### Milestones & Status
- [x] **v1 Baseline**: Verbatim typewriter keystroke stream, basic delays (`\delay`), and single-level repetition loops (`\rep`).
- [x] **v2 Control Flow**: Line continuations (`\`), line comments (`\#`), split single-shot sleep (`\delay`) vs cadence pacing (`\pace`), and nested repetition loops (depth &le; 4).
- [x] **v3 Programmable Extensions**:
  - Compile-time macro expansion (`\def`).
  - 32 integer registers (`\set`, `\add`), legacy conditional branches (`\ifnum`), and register value typing (`\typenum`).
  - External namespace dependency declarations (`\use`) and extension invocations (`\call`).
  - Asynchronous named signal reception (`\wait`) with slot payload projection.
- [x] **v4 Structured Logic & Layout**:
  - Preamble syntax layout directives (`\#!syntax: typewriter|free`) suppressing unwanted whitespace events.
  - Verbatim text blocks (`\text{}...\endtext{}`) immune to bracket balancing and command collisions.
  - Structured predicate grammar: `\if{ {left, op, right} }` with composite `\all{...}` / `\any{...}` logic (deprecating `\ifnum`).
  - Subtraction arithmetic (`\sub`) and uniform handle fault codes (`$err`).
  - USB Consumer Control media keys (HID Usage Page 0x0C).
  - Flat iterative guarded blocks (`\guarded`) for deterministic exit handling.
  - Configurable compile-time press-hold duration (`hold_ms`, default 10 ms, range `[0, 65535]`) with tap-release invariants and explicit `\tap` command.

---

## 2. Bytecode VM & Runtime Engine

The microcontroller execution engine, decoupled from host operating systems and physical hardware providers via the `HIDProvider` abstraction.

### Milestones & Status
- [x] **v1 Foundation & HID Abstraction**: Decoupled compiler and virtual machine from platform libraries via the `HIDProvider` interface; cooperative non-blocking asyncio main loop.
- [x] **v2 Safe Execution Model**: Guaranteed hardware release on error or abort (keyboard keys, mouse buttons); forward jump support.
- [x] **v3 Register Machine & Machine Protocol**:
  - 32 general-purpose i32 registers and dedicated status registers (`$ret`, `$timeout`).
  - Uniform typed operand encoding (immediate, register, string).
  - High-speed raw TCP message channel (`:7373`, protocol v1) with framed binary transport and reverse requests.
  - Centralized runtime registry mediating register writes, signals, and slots.
- [x] **v4 Guarded Execution & Telemetry**:
  - Typed register write mediation with handle resource lifecycle management and `$err` fault dispatch.
  - Single-point scalar telemetry projection (`$slot.img.<name>.val`).
  - Modularized streaming compiler with bounded memory and zero Python recursion.
  - Modular host and network services package (`lib/services/`: network, http, raw, img, runner).
  - Guard invariant evaluation on signal arrival during wait with timer-jitter tolerance.
  - Program-bound immutable press-hold timing enforcement across all paced atomic actions.

---

## 3. Web Studio & Tooling

The zero-install authoring, telemetry, and diagnostics environment served directly from microcontroller RAM.

### Milestones & Status
- [x] **Web Studio Core**: Single-file bundled Web IDE served over device HTTP with zero external dependencies.
- [x] **Network Discovery & mDNS**: RFC 6762 mDNS discovery (resolving to `http://mycro.local/`), canonical HTTP 302 redirection from raw IP, and configurable companion port (`:7373`).
- [x] **Preset & Execution Management**: Live macro execution, compile disassembly preview, abort control, and local JSON preset export/import.
- [x] **Language Support (LSP) & Token Inspector**:
  - Real-time syntax highlighting and spec-aligned bracket depth validation.
  - Dynamic token inspector and context-aware candidate completion dictionary.
- [x] **Interactive Quick Manual**: Searchable DSL documentation drawer with live parameter assembly forms and 1-click snippet insertion.
- [x] **Pixel Assist & Visual Telemetry**:
  - Live workspace overlay with 11&times;11 pixel loupe and crosshairs.
  - Real-time coordinate sampling, color format conversion (Hex/RGB/DSL), and 1-click macro snippet generation.
  - Pure standard-library OBS Studio companion provider (`obs/obs_mycro.py`) with in-memory OBS-WS v5 RPC, lossless crops, and transport vs business error differentiation.
  - Ephemeral direct preview over HTTP with Chrome Private Network Access (PNA 204) and HMAC-SHA256 authenticated ticket distribution.
  - 7-domain headless diagnostic test runner (`tests/obs/obstest.py`).
  - Interactive Tk 9 closed-loop hardware verification GUI (`tests/obs/full_link_test.py`) with automatic coordinate calibration and multi-frequency perception latency profiling.
  - Table-driven 6-domain acceptance suite in `tests/device/devicetest.py` (Domains A through G).

---

## Future Directions

The next phase of MYCRO advances the boundary between host environments and microcontroller execution:

- **DSL v5**:
  - Real-time execution logs in Web Studio with bounded delivery and no interference with hardware timing.
  - Compile-time repetition through `\expand{N}{<BLOCK>}`, while keeping the emitted runtime program flat.
  - Monotonic checkpoints and elapsed-time reads for profiling, deadlines, and hardware timing logic.
  - Structured interruption and transfer for direct hardware state-machine exits.
  - Resumable program chaining with persistent external slot and peripheral state, plus direct loading and saving of executable bytecode.
- **Host-Device Interoperability & Vision Feedback**:
  - Advancing bidirectional perception mechanisms between the host operating environment and MYCRO (including closed-loop visual feedback and image recognition workflows).
  - Streamlining low-latency telemetry channels for reactive event delivery.
- **Physical Peripheral Integration**:
  - Deepening microcontroller physical hardware interactions and native hardware-level event triggering.
  - Exposing on-device peripheral states directly into the reactive DSL signal and slot system.
