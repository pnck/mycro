# MYCRO Runtime Environment & Standard Library Specification

**Status: normative.** This document specifies the MYCRO macro runtime architecture, the Signal/Slot event model, the peripheral abstraction layer (`HIDProvider`), and standard library namespaces for **v4**.

- For surface DSL syntax and grammatical rules, see [dsl.md](dsl.md).
- For binary bytecode container format and VM execution semantics, see [vm.md](vm.md).
- For all system capacities, buffer limits, and timing thresholds, see the Authoritative Profile Constants Table in [vm.md §1.0](vm.md#10-authoritative-profile-constants-table).

---

## 1. Runtime Architecture & Signal / Slot Reset

`src/lib/runtime.py` governs four dimensions of runtime resources:
- **Namespaces**: Functional and peripheral boundaries (`sys`, `net`, `img`).
- **Function Signatures**: Input parameter schemas (Value types vs Name types vs Handle types), return value contracts, and standard error code conventions.
- **Signals**: Pure Name-category external projection channels (`net.msg`, `img.frame`).
- **Slots**: Pure Value-category state receptors, mandatory-prefixed with `$slot.` (e.g., `$slot.net.msg.val`, `$slot.img.hp.val`), holding scalar payloads and arrival latches.

### 1.1 Signal / Slot Orthogonal Paradigm Axioms

The runtime establishes two orthogonal invariants separating external projection from internal reception:

1. **Signals are Strictly Name-Category Projection Channels (Projectors)**:
   - **Syntactic Form**: Whenever Signal semantics appear, **they MUST be pure static identifiers (Name category, never prefixed with `$`)**.
   - **Semantic Role**: Represents an external projection channel triggered exclusively by the outside world (host Protocol v1, OBS capture providers, network push).
   - **Statelessness**: Signals maintain no persistent storage. They cannot be read, cannot participate in arithmetic, and cannot be awaited directly.
2. **Slots are Strictly Value-Category Stateful Receptors**:
   - **Syntactic Form**: Whenever Slot semantics appear, **they MUST be variables mandatory-prefixed with `$slot.`** (canonical form: `$slot.<sig>.<field>` or delimited `${slot.<sig>.<field>}`).
   - **No-Shorthand Law**: Slot field names MUST be written explicitly in source code. Because multi-level identifiers split at the last dot, an omitted form like `$slot.<sig>` resolves to signal `<prefix>` + field `<last_segment>`, conflicting with the shorthand intent. A single deterministic rule outranks convenience.
   - **Default Projection Rule**: When an external signal `<sig>` is emitted without specific sub-fields (e.g., a single scalar integer or notification), the **default projection field is uniformly mapped to `val`** (`$slot.<sig>.val`). When emitted with a key-value dictionary, payloads project to corresponding `$slot.<sig>.<k>` fields.
   - **Multi-Level Splitting**: After stripping `$slot.`, the remainder is split at the LAST dot: signal channel is everything to the left, and field identifier is the last segment (e.g., `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`).
   - **Event Latch Storage Model**: Each `(signal, field)` slot maintains:
     - `val`: Latest scalar payload (`i32`, initialized to 0).
     - `pending`: Unconsumed event arrival flag (`i32` scalar flag: `1` = event pending, `0` = idle/consumed; initialized to `0`). External signal projections atomically set `pending = 1` and wake waiting coroutines. **The flag is per-(signal, field)**; a multi-field projection sets the flag on every field it writes.
3. **The Sole Waitable Receptor Law**:
   - **Receptor slots starting with `$slot.` are the ONLY valid asynchronous entities eligible for waiting (`\wait`)**. Guard predicates observe external asynchronous state exclusively through `$slot.` receptors.
   - General-purpose registers (`$var`, `${var}`) represent private synchronous state inside the single-threaded execution context; attempting to `\wait` on `$var` is statically rejected.
   - **Coalescing Latest-Value Latch Waiting**: Slots maintain a coalescing latest-value latch. If external signal projections arrive faster than consumption, the latest payload overwrites `val`, and `pending` remains `1` (unconsumed events coalesce; slots are not unbounded FIFO queues). Executing `\wait[timeout]{ $slot.<sig>.<field> }` (or delimited `${slot.<sig>.<field>}`):
     - If `slot.pending == 1` upon entry: Consumes the event immediately, resets `slot.pending = 0`, writes `0` to special status register `0xFE` (`$timeout`), and returns immediately with zero delay, eliminating race conditions.
     - If `slot.pending == 0`: Suspends execution until the next external projection sets `pending = 1` (or until `timeout` expires).
     - Upon projection arrival: Consumes the event (setting `pending = 0`), writes `0` to `$timeout`, and resumes execution.
     - Upon timeout expiration: Leaves `slot.pending = 0`, writes `1` to `$timeout`, and resumes.
   - **Atomic Latch Guarantee**: Between inspecting `slot.pending == 1` and registering the waiter callback/coroutine, there MUST be NO intervening `await` suspension point, ensuring atomic event latch consumption in single-threaded cooperative asyncio.
   - **Timeout Domain & Immediate Polling**: The `timeout` parameter domain is a signed 32-bit integer (`i32 LE`):
     - `timeout > 0`: Bounded suspension up to `timeout` milliseconds.
     - `timeout <= 0`: **Non-blocking immediate poll** (evaluates `slot.pending`; if `1`, consumes and writes `$timeout = 0`; else immediately writes `$timeout = 1`).
     - Omitted `[timeout]`: Lowered by the compiler to $2147483647\text{ ms}$ ($2^{31}-1$, $\approx 24.8$ days). Indefinite wait without timeout is strictly prohibited to guarantee halting decidability.
   - **Status Reporting Channel**: Status is written exclusively to special register `0xFE` (`$timeout` / `${timeout}`).

### 1.2 Unidirectional Asynchronous Flow

Slots serve exclusively as passive data containers and event latches with zero reverse control flow or re-entrant callbacks:
$$\text{External Signal Projection} \longrightarrow \text{Slot Event Latch} \longrightarrow \text{Internal Wait / Guard Inspection}$$

### 1.3 Cooperative Concurrency & Slot Consistency

- The device executes on a cooperative single-threaded event loop (`asyncio`).
- External network packets (Protocol v1 `event.push`) and VM execution share the main thread dispatcher.
- The VM yields execution via `await asyncio.sleep` during `\delay` micro-slices or `\wait` suspensions. Computational bursts and predicate evaluations execute uninterrupted.
- **Consistency Guarantee**: Single-threaded dispatch guarantees mutual exclusion for slot reads and writes, providing deterministic memory consistency without mutex locks.
- **Single Active Task Exclusivity**: MYCRO supports exactly ONE active macro task at any given time. While a macro task is executing (`state: busy`), any incoming HTTP request to compile or execute a macro (`POST /macro`, `POST /compile`) or wire Protocol envelope (`macro.submit`) MUST be rejected immediately with HTTP 409 Conflict / protocol error (`state: busy`).

---

## 2. HIDProvider Peripheral Abstraction Layer

The VM core and compiler remain fully decoupled from physical hardware. All peripheral interactions are funneled through the `HIDProvider` interface:

| Method | Parameters | Description |
|---|---|---|
| `key_press` | `keycode: int` | Physically asserts key. |
| `key_release` | `keycode: int` | Releases physically asserted key. |
| `keys_release_all` | — | Emergency release of all asserted keys. |
| `mouse_press` | `btn: int` | Presses mouse buttons (bitmask: 1=L, 2=R, 4=M, 8=B, 16=F). |
| `mouse_release` | `btn: int` | Releases mouse buttons. |
| `mouse_move` | `dx: int, dy: int` | Emits relative mouse movement. Physical USB HID mouse report coordinates are signed 8-bit integers ($-127$ to $+127$). `HIDProvider.mouse_move` implementations MUST internally decompose movements where $|dx| > 127$ or $|dy| > 127$ into chunks $\le 127$ and emit consecutive reports until the total displacement is satisfied. |
| `char_keycodes` | `char: str` | Resolves character to physical keycode and Shift state. |
| `consumer_pulse` | `usage_id: int` | Sends a single USB HID Consumer Control press and release pulse (Usage Page 0x0C, e.g., volume, playback, power; VM opcode `0x08 CONSUMER_KEY`). |

Platform implementations:
- CircuitPython: `src/lib/hid_adafruit.py` (backed by `adafruit_hid`).
- Unit Testing: `tests/unit/mock_hid.py`.
- MicroPython (target architecture): Backed by `machine.USBDevice`.


---

## 3. Standard Library Namespaces

### 3.0 Universal Return & Error Contract

Every standard library function invocation (`0x2A CALL_EXT`) establishes a strict two-channel reporting contract:
- **Return Channel (`$ret`, register `0xFF`)**: Holds the functional return value (a scalar integer result, client count, or positive integer Handle ID). Evaluates to `0` if the function is void or if an execution error occurs.
- **Error Channel (`$err`, register `0xFD`)**: Holds the execution status:
  - On success: `$err = ERR_OK (0)`.
  - On error: `$err != 0`, holding a standard non-zero error code.

#### Standard Error Code Enumeration Table
| Error Code | Constant Identifier | Operational Meaning |
|---|---|---|
| `0` | `ERR_OK` | Success (no error). |
| `1` | `ERR_GENERIC` | Generic runtime or extension failure. |
| `2` | `ERR_INVALID_ARG` | Invalid argument value, format, or parameter out of bounds. |
| `3` | `ERR_NO_PROVIDER` | Capture provider not connected or unavailable. |
| `4` | `ERR_OUT_OF_BOUNDS` | Sampling coordinates exceed buffer dimensions. |
| `5` | `ERR_HANDLE_INVALID` | Unknown, unallocated, or expired Handle ID. |
| `6` | `ERR_RESOURCE_EXHAUSTED` | Handle table capacity or snapshot slot pool exhausted. |
| `7` | `ERR_TIMEOUT` | Extension operation or capture request timed out. |

### 3.1 `sys` Namespace (System Control)

- **`sys.free`**: Takes 0 arguments. Returns current free heap bytes (`i32`) in `$ret`, `$err = ERR_OK (0)`.
- **`sys.const`**: Internal runtime intrinsic. Takes 1 immediate constant ID (`cid`). Retrieves and materializes a read-only Handle for compile-time constants (e.g., `\pixelrect` pixel literals); returns Handle ID in `$ret`, `$err = ERR_OK (0)`.

### 3.2 `net` Namespace (Network Communication)

- **`net.send{tag}`**: Broadcasts a `net.msg` frame carrying the label `tag` to all authenticated Protocol v1 clients. `tag` is a **Name-category label**: identifier grammar (`Identifier ("." Identifier)*`), case-sensitive and case-preserving (the compiler MUST NOT fold case); empty or non-identifier labels are compile errors. Lowered as a string-table index (`TAG_NAME = 2`). Returns in `$ret` the count of clients the frame was actually queued to, with `$err = ERR_OK (0)`.
  - **0 Connected Clients Non-Error Invariant**: When no authenticated clients are connected, `net.send` queues to 0 clients, returns `$ret = 0`, and sets `$err = ERR_OK (0)` (broadcasting to zero listeners is normal operation, NOT a runtime error).
- **`net.msg` Signal**: Built-in static signal. Triggered when a host client transmits an `event.push` frame, projecting into `$slot.net.msg.*` and latching `pending = 1`. Macros synchronize via `\wait{ $slot.net.msg.val }`.

### 3.3 `img` Namespace (Vision & Telemetry)

#### 1. Exact Pixel Matching Family
- **`\call{img.snap}{rect}`**: Captures screen region (`rect` tuple `{x, y, w, h}`); returns positive snap Handle in `$ret` and `$err = ERR_OK (0)` on success. On capture failure / timeout / no provider, returns `$ret = 0` and sets `$err != 0` (e.g. `ERR_NO_PROVIDER`, `ERR_TIMEOUT`, or `ERR_RESOURCE_EXHAUSTED`).
- **`\call{img.at}{snap_handle}{point}`**: Samples packed 24-bit RGB color at coordinates (`point` tuple `{x, y}`); returns packed `0xRRGGBB` in `$ret` and `$err = ERR_OK (0)` on success. If the handle is invalid or coordinates are out of bounds, returns `$ret = 0` and sets `$err != 0` (`ERR_HANDLE_INVALID` or `ERR_OUT_OF_BOUNDS`).
- **`\call{img.diff}{snap_a}{snap_b}{tol}`**: Compares two snapshots; returns count of pixels exceeding color tolerance `tol` in `$ret` and `$err = ERR_OK (0)` on success. If handles are invalid or dimensions mismatch, returns `$ret = 0` and sets `$err != 0` (`ERR_HANDLE_INVALID` or `ERR_INVALID_ARG`).

#### 2. Push-Model Telemetry Subscriptions (`img.watch`)
Initiates continuous push telemetry from the capture provider:
```dsl
\call{img.watch}{name}{coord}{interval_ms, duration_ms}
```
- `name`: Stream identifier; registers external signal `img.<name>` (e.g., `img.hp`).
- `coord`: Sampling point `{x, y}` or bounding rect `{x, y, w, h}`.
- `interval_ms`: Sampling period on the host (ms).
- `duration_ms`: Mandatory life lease duration (ms). Capture provider terminates stream upon lease expiration.

> [!IMPORTANT]
> **Protocol Delta Precondition**: This subscription mechanism requires Protocol v1 to gain companion frame types (subscription request, telemetry push frames, and lease management semantics). Until [docs/protocol.md](protocol.md) gains the corresponding specification chapter, this clause states target semantics only and does not constitute an implementable specification.

#### 3. Slot Addressing & Coordination
- **Slot Storage**: Arriving telemetry frames project into signal `img.<name>`, writing the sampled scalar into `$slot.img.<name>.val` and setting `pending = 1`.
- **Zero-Copy Reading**: Macro code reads the latest sample directly via `$slot.img.<name>.val` (initialized to 0).
- **Frame Synchronization**: Awaited via `\wait{ $slot.img.<name>.val }`.

#### 4. Signal Validation Tiers
- **Tier 1 (Built-In Signals)**: Statically recognized signals (`net.msg`) require no `\use` declaration.
- **Tier 2 (Dynamic Namespace Signals)**: Signals belonging to imported namespaces (e.g., `img.<name>` under `\use{img}`) are dynamically validated at compile time against registered extension providers.
- **Tier 3 (Undeclared Signals)**: Any signal path referencing an unknown or unimported namespace triggers an immediate static compile error (`unknown signal namespace`).

---

## 4. Handle Lifecycle & Ownership Management

1. **Resource Type Isolation**: Heavy objects (such as snapshot framebuffers) reside in the runtime's internal object store; macros interact strictly through opaque positive `i32` Handle IDs ($1, 2, \dots$).
2. **Shared Reference Model**:
   - Variable assignment (`\set{b}{$a}`) copies the handle ID from `reg_a` to `reg_b`. Both registers hold identical handle IDs referencing the same underlying resource (shared reference semantics).
   - If an extension call creates a new handle, the handle ID is written to `$ret` (`0xFF`), placing it under active reference.
3. **Table-Driven Liveness on Register Overwrite**:
   - Every register write passes through `runtime.reg_write(dst, src)`.
   - When register `dst` is overwritten: if `dst` previously held a handle ID, the runtime inspects all general-purpose registers (`regs[0..MAX_REGS-1]`) and `$ret` (`0xFF`). If NO active register references that handle ID, the handle entry and its buffer slot are automatically freed and returned to the pool.
4. **Handle Type Invariants & Prohibitions**:
   - **Arithmetic Invariance**: Resource handles cannot participate in arithmetic; passing a handle to `\add` or `\sub` triggers an immediate static compile error.
   - **Keystroke Rendering Invariance**: Resource handles cannot be rendered as keystroke numbers; passing a handle to `\typenum` triggers an immediate static compile error (`typenum: cannot render a <type> handle`).
   - **Handle Comparison Prohibition**: Resource handles cannot be compared in conditional or guarded predicates (`\if{ {$h, ==, 0} }` triggers static compile error `type error: cannot compare Handle with Scalar`). Success or failure of handle-producing calls is verified exclusively by checking the error channel: `\if{ {$err, !=, 0} }`.
5. **Deterministic Cleanup**: Upon macro completion, task abort, or unhandled exception, `runtime.release_handles()` executes unconditionally in a `finally` block, reclaiming all active Handles and freeing all allocated buffer slots.

---

## 5. Compile-Time Const Store

Compile-time constants too large or structured for operand encoding (such as `\pixelrect` pixel literals) are preprocessed by the compiler into a middleware **const store** (`add_const`/`const_entry`), reset on each compilation.
- **Bulk Const Store Materialization (`\pixelrect`)**: Matrix dimensions are bounded by `RECT_MAX_EDGE = 128` pixels ($128^2 \times 3 = 48\text{ KiB}$ matching `SNAP_SLOT_BYTES`). The compiler parses the 2D pixel array, serializes row-major 24-bit RGB bytes, and pre-populates the runtime const store. The bytecode references entries by integer ID via `sys.const <cid>`, materializing standard snapshot Handles at zero runtime heap allocation overhead.
- **Scalar Folded Constants (`\rgb`)**: In contrast to bulk constant structures, static scalar constructors such as packed RGB colors (`\rgb{r, g, b}`) fold at compile time directly into signed 24-bit integer scalar immediates (`TAG_IMM (0)`), incurring zero const store allocation and zero runtime construction bytecode.
- **Ownership Rules**: Handle ownership rules (shared references, overwrite-release, exit-release) apply to materialized constant handles unchanged.
- **Concurrency Barrier**: Any compilation request received while a macro is actively running MUST be rejected as busy (`state: busy`) to prevent clobbering active constants.

