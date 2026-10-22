# MYCRO Runtime Environment & Standard Library Specification

**Status: normative.** This document specifies the MYCRO macro runtime architecture, the Signal/Slot event model, the peripheral abstraction layer (`HIDProvider`), and standard library namespaces for **v4**.

- For surface DSL syntax and grammatical rules, see [dsl.md](dsl.md).
- For binary bytecode container format and VM execution semantics, see [vm.md](vm.md).

---

## 1. Runtime Architecture & Signal / Slot Reset

`src/lib/runtime.py` governs four dimensions of runtime resources:
- **Namespaces**: Functional and peripheral boundaries (`sys`, `net`, `img`).
- **Function Signatures**: Input parameter schemas (Value types vs Handle types), return value contracts, and error code conventions.
- **Signals**: Pure Name-category external projection channels (`net.msg`, `img.frame`).
- **Slots**: Pure Value-category state receptors, mandatory-prefixed with `$slot.` (e.g., `$slot.net.msg.val`, `$slot.img.hp.val`), holding scalar payloads and arrival latches.

### 1.1 Signal / Slot Orthogonal Paradigm Axioms

The runtime establishes two orthogonal invariants separating external projection from internal reception:

1. **Signals are Strictly Name-Category Projection Channels (Projectors)**:
   - **Syntactic Form**: Whenever Signal semantics appear, **they MUST be pure static identifiers (Name category, never prefixed with `$`)**.
   - **Semantic Role**: Represents an external projection channel triggered exclusively by the outside world (host Protocol v1, OBS capture providers, network push).
   - **Statelessness**: Signals maintain no persistent storage. They cannot be read, cannot participate in arithmetic, and cannot be awaited directly.
2. **Slots are Strictly Value-Category Stateful Receptors**:
   - **Syntactic Form**: Whenever Slot semantics appear, **they MUST be variables mandatory-prefixed with `$slot.`** (canonical form: `$slot.<sig>.<field>`).
   - **No-Shorthand Law**: Slot field names MUST be written explicitly in source code. Because multi-level identifiers split at the last dot, an omitted form like `$slot.<sig>` resolves to signal `<prefix>` + field `<last_segment>`, conflicting with the shorthand intent. A single deterministic rule outranks convenience.
   - **Default Projection Rule**: When an external signal `<sig>` is emitted without specific sub-fields (e.g., a single scalar integer or notification), the **default projection field is uniformly mapped to `val`** (`$slot.<sig>.val`). When emitted with a key-value dictionary, payloads project to corresponding `$slot.<sig>.<k>` fields.
   - **Multi-Level Splitting**: After stripping `$slot.`, the remainder is split at the LAST dot: signal channel is everything to the left, and field identifier is the last segment (e.g., `$slot.sys.net.msg.val` $\rightarrow$ signal `sys.net.msg`, field `val`).
   - **Event Latch Storage Model**: Each `(signal, field)` slot maintains:
     - `val`: Latest scalar payload (`i32`, initialized to 0).
     - `pending`: Unconsumed event arrival flag (`bool`, initialized to `False`). External signal projections atomically set `pending = True` and wake waiting coroutines. **The flag is per-(signal, field)**; a multi-field projection sets the flag on every field it writes.
3. **The Sole Waitable Receptor Law**:
   - **Receptor slots starting with `$slot.` are the ONLY valid asynchronous entities eligible for waiting (`\wait`)**. Guard predicates observe external asynchronous state exclusively through `$slot.` receptors.
   - General-purpose registers (`$var`) represent private synchronous state inside the single-threaded execution context; attempting to `\wait` on `$var` is statically rejected.
   - **Lossless Event Latch Waiting**: Executing `\wait[timeout]{ $slot.<sig>.<field> }`:
     - If `slot.pending == True` (prior projection unconsumed): Consumes the event immediately, resets `slot.pending = False`, and returns with zero delay, eliminating race-condition event loss.
     - If `slot.pending == False`: Suspends the coroutine until the next projection sets `pending = True` (or until timeout); upon wake-up, consumes the flag and resumes execution.

### 1.2 Unidirectional Asynchronous Flow

Slots serve exclusively as passive data containers and event latches with zero reverse control flow or re-entrant callbacks:
$$\text{External Signal Projection} \longrightarrow \text{Slot Event Latch} \longrightarrow \text{Internal Wait / Guard Inspection}$$

### 1.3 Cooperative Concurrency & Slot Consistency

- The device executes on a cooperative single-threaded event loop (`asyncio`).
- External network packets (Protocol v1 `event.push`) and VM execution share the main thread dispatcher.
- The VM yields execution via `await asyncio.sleep` during `\delay` micro-slices or `\wait` suspensions. Computational bursts and predicate evaluations execute uninterrupted.
- **Consistency Guarantee**: Single-threaded dispatch guarantees mutual exclusion for slot reads and writes, providing deterministic memory consistency without mutex locks.

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
| `mouse_move` | `dx: int, dy: int` | Emits relative mouse movement. |
| `char_keycodes` | `char: str` | Resolves character to physical keycode and Shift state. |
| `consumer_pulse` | `usage_id: int` | Sends a single USB HID Consumer Control press and release pulse (Usage Page 0x0C, e.g., volume, playback, power; VM opcode `0x08 CONSUMER_KEY`). |

Platform implementations:
- CircuitPython: `src/lib/hid_adafruit.py` (backed by `adafruit_hid`).
- Unit Testing: `tests/unit/mock_hid.py`.
- MicroPython (target architecture): Backed by `machine.USBDevice`.

---

## 3. Standard Library Namespaces

### 3.1 `sys` Namespace (System Control)

- **`sys.free`**: Takes 0 arguments. Returns current free heap bytes (`i32`) in `$ret`.
- **`sys.const`**: Internal runtime intrinsic. Retrieves read-only Handles for compile-time constants (e.g., `\rect` pixel literals).

### 3.2 `net` Namespace (Network Communication)

- **`net.send{str_tag}`**: Broadcasts a `net.msg` frame containing `str_tag` to all authenticated Protocol v1 clients. Returns recipient count in `$ret`.
- **`net.msg` Signal**: Triggered when a host client transmits an `event.push` frame, projecting into `$slot.net.msg.*` and latching `pending = True`. Macros synchronize via `\wait{ $slot.net.msg.val }`.

### 3.3 `img` Namespace (Vision & Telemetry)

#### 1. Exact Pixel Matching Family
- **`\call{img.snap}{rect}`**: Captures screen region (`rect` tuple `{x, y, w, h}`); returns non-negative snap Handle in `$ret` ($< 0$ on error).
- **`\call{img.at}{snap_handle}{point}`**: Samples packed 24-bit RGB color at coordinates (`point` tuple `{x, y}`); returns packed `0xRRGGBB` in `$ret`.
- **`\call{img.diff}{snap_a}{snap_b}{tol}`**: Compares two snapshots; returns count of pixels exceeding color tolerance `tol` in `$ret`.

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
- **Slot Storage**: Arriving telemetry frames project into signal `img.<name>`, writing the sampled scalar into `$slot.img.<name>.val` and setting `pending = True`.
- **Zero-Copy Reading**: Macro code reads the latest sample directly via `$slot.img.<name>.val` (initialized to 0).
- **Frame Synchronization**: Awaited via `\wait{ $slot.img.<name>.val }`.

---

## 4. Handle Lifecycle & Ownership Management

1. **Resource Type Isolation**: Heavy objects (such as snapshot framebuffers) reside in the runtime's internal object store; macros interact strictly through opaque `i32` Handles.
2. **Register-Write Mediation**: Every register write is mediated by `runtime.reg_write(dst, src)`:
   - When a register holding a Handle is overwritten or assigned, the old Handle is automatically unregistered and freed.
   - Variable assignment (`\set{b}{$a}`) transfers resource ownership (Move semantics; source register reverts to plain integer).
   - If an extension minted a handle and `$ret` is updated, ownership is claimed; unhandled orphan resources are reclaimed immediately.
3. **Deterministic Cleanup**: Upon macro completion, abort, or unhandled exception, `runtime.release_all()` executes unconditionally in a `finally` block, reclaiming all active Handles.

---

## 5. Compile-Time Const Store

Compile-time constants too large or structured for operand encoding (e.g., `\rect` pixel literals) are preprocessed by the compiler into a middleware **const store** (`add_const`/`const_entry`), reset on each compilation.
- The bytecode references entries by integer ID via `sys.const <cid>`, materializing standard Handles.
- Ownership rules (claim, move, overwrite-release, exit-release) apply unchanged.
- Any compilation request received while a macro is actively running MUST be rejected as busy (`state: busy`) to prevent clobbering active constants.
