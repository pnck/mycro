# MYCRO Runtime Environment & Standard Library Specification

**Status: normative.** This document specifies the MYCRO v4 macro runtime architecture, Signal/Slot event model, peripheral abstraction layer (`HIDProvider`), and standard library namespaces.

- For surface DSL syntax and grammatical rules, see [dsl.md](dsl.md).
- For binary bytecode container format and VM execution semantics, see [vm.md](vm.md).
- For system capacities, buffer limits, and timing thresholds, see the Authoritative Profile Constants Table in [vm.md §1.0](vm.md#10-profile-constants).

---

## 1. Runtime Architecture and Signal / Slot Model

The runtime architecture defines four resource boundaries:
- **Namespaces**: Functional and peripheral boundaries (`sys`, `net`, `img`).
- **Function signatures**: Input schemas (Value, Name, and Handle types), return contracts, and standard error codes.
- **Signals**: Name-category external projection channels (`net.msg`, `img.<name>`).
- **Slots**: Value-category state receptors prefixed with `$slot.` (for example, `$slot.net.msg.val` and `$slot.img.hp.val`), holding scalar payloads and arrival latches.

### 1.1 Signals and Slots

Signals project external events; slots receive their state.

1. **Signals: Name-category projection channels**
   - **Syntax**: A signal is a static Name-category identifier. A `$`-prefixed form is invalid.
   - **Source**: External sources trigger projections: host Protocol v1 clients, OBS capture providers, or network push.
   - **State**: Signals are stateless projection channels. Reading a signal, using it in arithmetic, or awaiting it directly is invalid.
2. **Slots: Value-category state receptors**
   - **Syntax**: A slot uses `$slot.<sig>.<field>` or delimited `${slot.<sig>.<field>}` form.
   - **Explicit fields**: Every source reference includes the field name. `$slot.<sig>` splits into signal `<prefix>` and field `<last_segment>` at the last dot.
   - **Default projection**: An external signal `<sig>` carrying `fields: {"val": <i32>}` writes the scalar to `$slot.<sig>.val` and sets its arrival latch. Empty `fields: {}` is a notification: it sets only `$slot.<sig>.val.pending = 1` and retains the current `val`. A multi-field object projects each entry to its corresponding `$slot.<sig>.<field>` receptor.
   - **Multi-level splitting**: After stripping `$slot.`, split at the last dot. The left side is the signal channel and the last segment is the field: `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`.
   - **Event latch storage**: Each `(signal, field)` slot maintains:
     - `val`: Latest scalar payload (`i32`, initialized to 0).
     - `pending`: Unconsumed arrival flag (`i32`: `1` = pending, `0` = idle/consumed; initialized to `0`). External scalar projections atomically set `pending = 1` and wake waiting coroutines. A multi-field projection sets the flag on every field it writes; a notification sets the flag on `val` alone.
   - **Receptor capacity**: `MAX_SLOTS = 256` counts distinct `(signal, field)` receptors. Only initial creation of a receptor consumes capacity; updates to an existing receptor remain available at every occupancy level. Before applying a projection, the runtime counts every new receptor required by the complete field set. Insufficient remaining capacity rejects the entire projection with protocol error `resource_exhausted`, preserving the receptor set, `$err`, and every existing slot value and latch.
3. **Wait receptors and latch semantics**
   - Valid asynchronous `\wait` targets and guard inputs for external state are `$slot.` receptors.
   - General-purpose registers (`$var`, `${var}`) hold private synchronous state in the single-threaded execution context. `\wait` on `$var` is a static error.
   - **Coalescing**: A slot stores one latest value and one latch. Projections arriving faster than consumption overwrite `val` while `pending` remains `1`. Executing `\wait[timeout]{ $slot.<sig>.<field> }` (or delimited `${slot.<sig>.<field>}`):
     - If `slot.pending == 1` on entry: consume the event, set `slot.pending = 0` and `$timeout = 0`, and return with zero delay.
     - If `slot.pending == 0`: suspend until a projection sets `pending = 1` or the timeout expires.
     - On projection arrival: consume the event, set `pending = 0` and `$timeout = 0`, and resume.
     - On timeout expiration: re-inspect `slot.pending`. Consume a projection latched before the timeout handler executes as an arrival (`pending = 0`, `$timeout = 0`). If `pending` remains `0`, set `$timeout = 1`, leave `slot.pending = 0`, and resume.
   - **Atomic latch operation**: Latch inspection and waiter registration run in one uninterrupted synchronous section between `await` suspension points. This makes event consumption atomic under single-threaded cooperative asyncio.
   - **Latest-value reads**: A completed `\wait` establishes that at least one unconsumed projection existed. A subsequent read returns the latest `val` at read time, which coalescing may have advanced beyond the payload that woke the waiter.
   - **Timeout domain and polling**: `timeout` is a signed 32-bit integer (`i32 LE`):
     - `timeout > 0`: Suspend for at most `timeout` milliseconds.
     - `timeout <= 0`: Poll immediately. For `slot.pending == 1`, consume the event and set `$timeout = 0`; otherwise set `$timeout = 1`. This applies to literals and dynamic values. Negative literals support countdown idioms.
     - Omitted `[timeout]`: The compiler supplies $2147483647\text{ ms}$ ($2^{31}-1$, approximately 24.8 days), giving every wait a finite bound.
   - **Status**: Wait status uses special register `0xFE` (`$timeout` / `${timeout}`).

### 1.2 Unidirectional Asynchronous Flow

The asynchronous data path is unidirectional. Slot updates are passive stores and latch operations; control flow remains with VM dispatch.

$$\text{External Signal Projection} \longrightarrow \text{Slot Event Latch} \longrightarrow \text{Internal Wait / Guard Inspection}$$

### 1.3 Cooperative Concurrency and Slot Consistency

- The device executes on a cooperative single-threaded `asyncio` event loop.
- External network packets (Protocol v1 `event.push`) and VM execution share the main-thread dispatcher.
- The VM yields through `await asyncio.sleep` during `\delay` micro-slices and `\wait` suspensions. Computational bursts and predicate evaluations execute uninterrupted.
- **Consistency**: Single-threaded dispatch makes slot reads and writes mutually exclusive and deterministic.
- **Single active task**: MYCRO runs one active macro task. While a macro executes (`state: busy`), compile or execute requests (`POST /macro`, `POST /compile`, or `macro.submit`) receive HTTP 409 Conflict or protocol error `state: busy` immediately.
- **Slot lifecycle**: Slot `val`/`pending` state persists across macro executions because receptors track the external world. Aborting a macro removes its wait registrations and retains slot data.

---

## 2. HIDProvider Peripheral Abstraction Layer

Peripheral interactions use the `HIDProvider` interface, isolating the VM core and compiler from physical hardware:

| Method | Parameters | Description |
|---|---|---|
| `key_press` | `keycode: int` | Physically asserts key. |
| `key_release` | `keycode: int` | Releases physically asserted key. |
| `keys_release_all` | — | Emergency release of all asserted keys. |
| `mouse_press` | `btn: int` | Presses mouse buttons (bitmask: 1=L, 2=R, 4=M, 8=B, 16=F). |
| `mouse_release` | `btn: int` | Releases mouse buttons. |
| `mouse_move` | `dx: int, dy: int` | Emits relative mouse movement. USB HID mouse report coordinates are signed 8-bit integers ($-127$ to $+127$). For $|dx| > 127$ or $|dy| > 127$, implementations decompose the movement into chunks of at most 127 and emit consecutive reports until reaching the exact total displacement. Chunk pairing across axes is provider-defined. |
| `char_keycodes` | `char: str` | Returns `(keycodes: sequence[u8], shift: bool)`. The layout baseline is **US-ANSI** per USB HID Usage Tables Keyboard/Keypad page `0x07`, covering printable ASCII 32–126 with exactly one keycode per character; the CircuitPython reference mapping is `adafruit_hid.KeyboardLayoutUS`, which ports reproduce. Tab/LF/CR lower to `KEY` before this interface ([dsl.md §3.3](dsl.md#33-scanner-behavior)). Unmappable characters are compile errors ([dsl.md §6.8](dsl.md#68-global-compile-diagnostics)). `TYPEREG` emits `[0-9-]`. |
| `consumer_press` | `usage_id: int` | Presses one USB HID Consumer Control usage (Usage Page 0x0C, for example volume, playback, or power; VM opcode `0x08 CONSUMER_KEY`). |
| `consumer_release` | `usage_id: int` | Releases one active USB HID Consumer Control usage. |

For character entry, a `shift = true` mapping presses Shift followed by `keycodes` in sequence, holds for `click_hold_ms`, and releases in reverse order: reversed `keycodes`, then Shift. A `shift = false` mapping presses and releases `keycodes` in the corresponding forward and reverse orders.

`CONSUMER_KEY` calls `consumer_press(usage_id)`, holds for the execution-time `click_hold_ms` using guard-sliced timing, and calls `consumer_release(usage_id)`. The VM tracks the active consumer usage, and every macro exit path, including completion, abort, and unhandled exception, releases that usage.

Platform implementations:
- CircuitPython: `src/lib/hid_adafruit.py` (backed by `adafruit_hid`).
- Unit testing: `tests/unit/mock_hid.py`.
- MicroPython (target architecture): backed by `machine.USBDevice`.

---

## 3. Standard Library Namespaces

A preceding top-level `\use{ns}` activates a namespace. At compile time, the compiler registers its callbacks for subsequent execution; activation is idempotent and persists across executions. This applies to every namespace, including `net`: protocol transport availability and DSL-visible activation are independent.

### 3.0 Universal Return and Error Contract

Every standard library invocation (`0x2A CALL_EXT`) reports through two channels:
- **Return (`$ret`, register `0xFF`)**: Functional return value (scalar integer result, client count, or positive integer Handle ID). Void functions and execution errors produce `0`.
- **Error (`$err`, register `0xFD`)**: Execution status: `ERR_OK (0)` on success or a standard non-zero error code on failure.

#### Standard Error Codes

| Error Code | Constant Identifier | Operational Meaning |
|---|---|---|
| `0` | `ERR_OK` | Success. |
| `1` | `ERR_GENERIC` | Generic runtime or extension failure. |
| `2` | `ERR_INVALID_ARG` | Invalid argument value or format, or an out-of-range parameter. |
| `3` | `ERR_NO_PROVIDER` | Capture provider disconnected or unavailable. |
| `4` | `ERR_OUT_OF_BOUNDS` | Sampling coordinates exceed buffer dimensions. |
| `5` | `ERR_HANDLE_INVALID` | Unknown, unallocated, or expired Handle ID, including a non-handle scalar supplied to a handle parameter. |
| `6` | `ERR_RESOURCE_EXHAUSTED` | Snapshot slot pool, const arena, or Handle ID space exhausted. |
| `7` | `ERR_TIMEOUT` | Extension operation or capture request timed out. |
| `8` | `ERR_HANDLE_MISUSE` | Handle-typed operand supplied to a scalar operation (arithmetic, comparison, `\typenum`); see [§4](#4-handle-lifecycle-and-ownership-management). |

#### Extension Arguments and Validation

Ordinary scalar `CALL_EXT` parameters use `typed(i32)`. The VM delivers each original `i32` value unchanged, and the extension validates it against the registered signature. Scalar clamping is an operation-specific rule for a built-in opcode with a declared physical domain or an explicit extension-signature rule; `img.diff` applies the signature clamp `[0, 255]` to `tol`.

#### Extension Completion and Exceptions

- **Bounded completion**: Every extension has bounded VM-task completion. Provider round-trips declare timeout behavior and report expiry as `ERR_TIMEOUT`.
- **Exceptions**: Declared failures use `$err`. An unexpected extension exception aborts the macro through abnormal termination, including peripheral release and handle reclamation ([vm.md §1.3](vm.md#13-peripheral-safety-invariants)).

### 3.1 `sys` Namespace (System Control)

- **`sys.free`**: Takes 0 arguments. Returns current free heap bytes (`i32`) in `$ret` and sets `$err = ERR_OK (0)`.
- **`sys.const`**: Internal runtime intrinsic taking one immediate constant ID (`cid`). The compiler activates this intrinsic for `\pixelrect` materialization independently of source-level `\use`. It materializes a read-only Handle for a compile-time constant, such as a `\pixelrect` pixel literal, and returns its Handle ID in `$ret` with `$err = ERR_OK (0)` on success. An invalid `cid` or resource exhaustion returns `$ret = 0` with `$err = ERR_INVALID_ARG` or `ERR_RESOURCE_EXHAUSTED`, respectively.

### 3.2 `net` Namespace (Network Communication)

- **`net.send{tag}`**: Broadcasts a `net.msg` frame labeled `tag` to all authenticated Protocol v1 clients. The Name-category `tag` follows `Identifier ("." Identifier)*`, with case preserved by the compiler. Empty and non-identifier labels are compile errors. The lowering uses a string-table index (`TAG_NAME = 2`). `$ret` is the number of client queues that accepted the frame and `$err = ERR_OK (0)`. With zero authenticated clients, the result is `$ret = 0`, `$err = ERR_OK (0)`.
- **`net.msg` signal**: Built-in static signal triggered by a host client's `event.push` frame. It projects into `$slot.net.msg.*` and latches `pending = 1`. Macros synchronize through `\wait{ $slot.net.msg.val }`.

### 3.3 `img` Namespace (Vision and Telemetry)

Provider round-trips in `img.*` use `EXT_TIMEOUT_MS` ([vm.md §1.0](vm.md#10-profile-constants)); expiry returns `$err = ERR_TIMEOUT`. Provider error envelopes map to `$err` as `out_of_bounds` → `ERR_OUT_OF_BOUNDS`, `field` → `ERR_INVALID_ARG`, and `capture_failed` or any remaining code → `ERR_GENERIC`.

#### 1. Exact Pixel Matching Family

- **`\call{img.snap}{x, y, w, h}`**: Captures the region at (`x`, `y`) with size `w` × `h`. Success returns a positive snap Handle in `$ret` and `$err = ERR_OK (0)`. Valid dimensions satisfy $1 \le w, h \le 128$ (`RECT_MAX_EDGE`) and $x, y \ge 0$; violations return `$ret = 0`, `$err = ERR_INVALID_ARG`. Constant arguments receive the same runtime validation and error result. The runtime contract for a region exceeding capture-source bounds is `$err = ERR_OUT_OF_BOUNDS`. Capture failure, timeout, missing provider, or resource exhaustion returns `$ret = 0` with the corresponding error, such as `ERR_NO_PROVIDER`, `ERR_TIMEOUT`, or `ERR_RESOURCE_EXHAUSTED`.
- **`\call{img.at}{snap_handle}{x, y}`**: Samples packed 24-bit RGB at snapshot-local (`x`, `y`). Success returns `0xRRGGBB` in `$ret` and `$err = ERR_OK (0)`. An invalid handle or out-of-bounds coordinates return `$ret = 0` with `ERR_HANDLE_INVALID` or `ERR_OUT_OF_BOUNDS`, respectively.
- **`\call{img.diff}{snap_a}{snap_b}{tol}`**: Compares snapshots pixel-wise. A pixel differs iff `max(|ΔR|, |ΔG|, |ΔB|) > tol`, using per-channel absolute differences. Clamp `tol` to `[0, 255]`. Success returns the differing-pixel count in `$ret` and `$err = ERR_OK (0)`. Invalid handles or mismatched dimensions return `$ret = 0` with `ERR_HANDLE_INVALID` or `ERR_INVALID_ARG`, respectively.

#### 2. Push-Model Telemetry Subscriptions (`img.watch`)

```dsl
\call{img.watch}{name}{x, y}{interval_ms, duration_ms}
```

This initiates continuous provider telemetry:

- `name`: Stream identifier. The device registers external signal `img.<name>` (for example, `img.hp`) to admit the provider's `event.push` frames. Providers originate all projections; the device-side name selects the subscription and receiving slot (`$slot.img.<name>.val`). Re-subscribing an existing `name` replaces its subscription and registers the signal anew.
- `x, y`: Sampling point with `x, y >= 0`; pushes packed `0xRRGGBB`. Extended form `x, y, w, h` selects a bounding rectangle with `w, h` in `[1, 128]` and pushes the per-channel mean as packed `0xRRGGBB`. Each channel mean uses floor integer division.
- `interval_ms`: Requested host sampling period in milliseconds, with valid values `> 0`. The provider clamps to its capture-chain floor and reports the effective period in `img.watch.ok`.
- `duration_ms`: Mandatory lease duration in milliseconds, with valid values `> 0`. The device tracks the lease deadline, and the provider terminates the stream at lease expiration.
- **Result**: Success returns `$ret = 0` and `$err = ERR_OK` (void). The rectangle form is `\call{img.watch}{name}{x, y, w, h}{interval_ms, duration_ms}` (`argc = 7`).
- **Wire path**: Subscription and cancellation use reverse requests `img.watch` / `img.unwatch`; telemetry uses `event.push` ([docs/protocol.md](../docs/protocol.md)).
- **Errors**: Missing capture provider returns `$ret = 0`, `$err = ERR_NO_PROVIDER`; subscription timeout returns `ERR_TIMEOUT`; a sampling region exceeding source bounds returns `ERR_OUT_OF_BOUNDS`; other invalid parameters return `ERR_INVALID_ARG`.
- **Cancellation**: Macro completion or abort sends `img.unwatch` for active subscriptions. The device unregisters the corresponding `img.<name>` signal on unwatch, lease expiry, or provider disconnect. A late `event.push` for an unregistered name receives `unknown_signal`. Unregistration retains slot `val`/`pending` state under the cross-macro lifecycle rules in §1.3.

#### 3. Slot Addressing and Coordination

- **Storage**: Telemetry frames project into `img.<name>`, writing the sampled scalar to `$slot.img.<name>.val` and setting `pending = 1`.
- **Reading**: Macro code reads the latest sample directly from `$slot.img.<name>.val` (initialized to 0).
- **Synchronization**: `\wait{ $slot.img.<name>.val }` awaits a frame.

#### 4. Signal Validation

- Every signal or slot reference requires activation of its namespace by a preceding top-level `\use{ns}`, including `net.msg`.
- Within an activated namespace, the compiler admits any identifier as a signal; per-name validity is a runtime concern. An unwatched name remains idle.
- An unknown or undeclared signal namespace produces static compile error `unknown signal namespace`.

---

## 4. Handle Lifecycle and Ownership Management

1. **Resource isolation**: Heavy objects such as snapshot framebuffers reside in the runtime object store. Macros use opaque positive `i32` Handle IDs ($1, 2, \dots$) from a monotonically increasing counter; each ID is allocated once. After allocation of `INT32_MAX` (`2147483647`), every subsequent Handle-producing call returns `$ret = 0`, `$err = ERR_RESOURCE_EXHAUSTED`, and the counter remains at `INT32_MAX`.
2. **Typing metadata**: Every register carries runtime metadata that distinguishes handle references from plain scalars. Metadata determines reference status; a scalar numerically equal to a Handle ID remains a scalar.
3. **Shared references**:
   - Variable assignment (`\set{b}{$a}`) copies the handle ID and typing metadata from `reg_a` to `reg_b`. Both registers reference the same resource.
   - An extension-created handle is written to `$ret` (`0xFF`) as an active reference.
   - `\call` arguments are borrowed: the callee reads the ID, and releasing a borrowed handle is invalid.
4. **Register-overwrite liveness**:
   - Every register write passes through `runtime.reg_write(dst: u8, value: i32, src: u8 | None, handle: bool)`. `value` and `handle` install the new scalar and type metadata; `src` identifies a copied source register when applicable.
   - The write atomically detaches `dst`'s previous reference, installs the new value and metadata, and scans the register file, including `$ret`. If the displaced handle ID then has zero references, the runtime frees its handle entry and buffer slot. Live handles have register references, so the register file plus in-flight `$ret` provides the structural handle-count bound; this bound replaces a separate handle-table capacity constant.
5. **Handle type faults**:
   - A Handle supplied to any scalar-only typed operand suppresses the host action, leaves the destination and target state unchanged, and sets `$err = ERR_HANDLE_MISUSE`. This rule covers arithmetic (`\add`, `\sub`), keystroke rendering (`\typenum`), and scalar-only extension parameters.
   - A predicate with a Handle source operand always evaluates to FALSE and sets `$err = ERR_HANDLE_MISUSE`. Call status is available through `\if{ {$err, !=, 0} }`.
6. **Cleanup**: Macro completion, task abort, or unhandled exception runs `runtime.release_handles()` in a `finally` block, reclaiming all active Handles and allocated buffer slots.

---

## 5. Compile-Time Const Store

The compiler preprocesses constants that exceed operand encoding or have structured payloads, such as `\pixelrect` pixel literals, into a middleware **const store** (`add_const`/`const_entry`). Each compilation resets the store.

- **Bulk materialization (`\pixelrect`)**: Matrix edges are bounded by `RECT_MAX_EDGE = 128` pixels ($128^2 \times 3 = 48\text{ KiB}$, matching `SNAP_SLOT_BYTES`). The compiler parses each `PixelRectStmt`, serializes row-major 24-bit RGB bytes, and populates the const store. Bytecode references entries by integer ID through `sys.const <cid>`, materializing standard snapshot Handles whose pixels reside in the const arena. Constant IDs start at `0` and increase in compiler encounter order after macro expansion. The compiler activates `sys.const` internally and assigns `$ret` directly to the statement destination; source-level `\use` is independent of this activation. A failed materialization skips the destination assignment and preserves the `$err` value from `sys.const`.
- **Scalar folding (`\rgb`)**: Packed RGB scalar literals (`\rgb{r, g, b}`) fold at compile time into `i32` immediates (`TAG_IMM (0)`) containing packed 24-bit RGB values, bypassing the const store and runtime construction bytecode.
- **Ownership**: Materialized const handles follow the shared-reference, overwrite-release, and exit-release rules. Releasing one frees its handle record; its pixel bytes remain in the const arena, whose batch lifetime ends at the wholesale reset on each compilation. Handle reclamation leaves arena bytes intact.
- **Concurrency**: The single-active-task rule in §1.3 keeps per-compilation const-store resets separate from active macro execution.
