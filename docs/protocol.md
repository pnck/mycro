# MYCRO Protocol v1

This is the normative reference for the machine-facing socket protocol used by host companion programs over raw TCP port 7373. It provides the information needed to implement an independent client. HTTP REST endpoints remain the human/debug channel.

## Frame layer

Every connection carries an ordered stream of frames:

```
u32 LE header:  bit31   = kind (0 = JSON envelope, 1 = binary chunk)
                bit0-30 = payload length in bytes
payload:        kind 0 → UTF-8 JSON envelope
                kind 1 → u16 LE stream_id + u16 LE flags + raw bytes
```

JSON envelopes and binary chunks each have an 8192-byte cap. For kind 1, the cap includes the 4-byte stream header, so a chunk carries at most 8188 raw bytes. A header announcing a larger payload for its kind is a protocol error, and the server closes the connection.

Binary frames carry bulk transfers such as vision screenshot pushes. One `img.frame.ok` JSON envelope supplies the begin metadata and correlates following chunks by `stream_id`; binary payloads contain raw bytes, and declared chunk and byte counts determine completion. Each `stream_id` is unique among active streams on its connection. `flags` is `0`; every other value is a protocol error. A server rejects kind-1 frames when it lacks a binary consumer.

## Connection prologue (auth)

The client leads with a fixed-length 37-byte header before any frame:

```
offset 0:  4 bytes  magic "MYCR"
offset 4:  1 byte   wire protocol version (currently 1)
offset 5:  32 bytes SHA-256 digest of the token (UTF-8 bytes of MYCRO_TOKEN)
```

The server compares the full digest. A magic, version, or digest mismatch closes the connection before any reply. In dev mode (an unconfigured device token), the server checks magic and version and skips the digest check. Dev-mode Pixel Assist signatures independently use HMAC-SHA256 with the empty key `b""`, and signature verification remains active. The wire version gates transport framing; envelope `v` separately versions message semantics.

## Envelope

kind-0 payloads are JSON objects:

```json
{"v": 1, "type": "macro.submit", "id": 1234, "ts": 987654, "payload": {}}
```

| field | type | meaning |
|---|---|---|
| `v` | int | message-semantics version (currently 1) |
| `type` | str | message type |
| `id` | int | u32 correlation id chosen by the requester; a response echoes the request's id; server-initiated pushes use `id` = 0 |
| `ts` | int | sender timestamp in milliseconds (device: `time.monotonic()` since boot; hosts may use epoch ms; receivers keep the two time domains separate) |
| `payload` | obj | per-type body |

Errors use `{"type": "error", "id": <request id>, "payload":
{"code": <machine code>, "message": <human text>}}`.

## Message types

Request → response pairs echo the request `id`. A successful response type is the request type plus `.ok`. Failures use `type: "error"` with machine-readable `payload.code`: `bad_version` / `bad_type` / `bad_id` / `bad_ts` / `bad_payload` / `unknown_type` / `field` / `compile_error` / `unknown_signal` / `state` / `internal` / `capture_failed` / `out_of_bounds` / `resource_exhausted`.

Inbound `type: "error"` and types ending in `.ok` are terminal responses or notifications. Receivers silently drop or log unhandled terminal responses, stray packets, and unhandled error notices, including unrecognized or unexpected ones. Error replies are reserved for requests, preventing error-reflection loops.

| type | direction | payload | response payload |
|---|---|---|---|
| `macro.submit` | client → server | `code`: DSL source string | `bytes`: compiled bytecode size |
| `macro.status` | client → server | — | `state`: `idle`/`running`/`done`/`aborted`/`error`, optional `detail` |
| `macro.abort` | client → server | — | — (error `state` when active macro count is zero) |
| `device.info` | client → server | optional `gc`: true → reading taken after a GC sweep (steady-state truth) | `board`, `fw`, `free_mem`, `proto` (protocol version) |
| `event.push` | client → server | `name`: signal name, `fields`: object of i32 slot values; `{"val": <i32>}` is scalar and `{}` is a notification | — (error `unknown_signal` for unregistered signals) |
| `macro.result` | server → clients | `state`, `ms` (execution wall time) | push, `id` = 0 |
| `net.msg` | server → clients | `tag`: the event label string from `\call{net.send}` (carried verbatim, case preserved) | push, `id` = 0 |
| `img.hello` | capture client → server | `caps`: list (`["frame", "direct_preview"]`) | push, `id` = 0; registers the client as the capture provider |
| `img.frame` | server → capture client | one selector: `rect` {x,y,w,h} → lossless RGB8 crop (≤ 128²) / `points` [[x,y],…] → exact colors / `w`,`h` + `format` (`rgb8`/`gray8`) → whole-source resample | `img.frame.ok` with the common and selector-specific schema below |
| `img.arm_preview` | server → capture client | `nonce`: 32-hex ephemeral random nonce | `img.arm_preview.ok` with `port`: ephemeral TCP port, `width`/`height`: native source dimensions |
| `img.watch` | server → capture client | `name`: stream identifier; `x`, `y`: sampling point, optional `w`, `h`: rect; `interval_ms`, `duration_ms` (lease) | `img.watch.ok` with `interval_ms`: effective period after provider capability clamping |
| `img.unwatch` | server → capture client | `name` | `img.unwatch.ok` |

Every `img.frame.ok` response carries `width` and `height` equal to the native source dimensions. Selector-specific response fields are:

- **`points`**: `colors` is an array of packed `0xRRGGBB` integers in request-point order. The JSON response carries the complete result and uses zero kind-1 chunks. A request contains at most `MAX_POINTS = 256` points; a longer list receives a `field` error before capture.
- **`rect`**: `w` and `h` are the crop dimensions, `format` is `rgb8`, and `stream_id`, `chunks`, and `bytes` describe the pixel stream, with `bytes = w * h * 3`. Raw bytes start at the crop's top-left pixel and are tightly packed in row-major order. Each pixel contributes three bytes in R, G, B order; row pitch is exactly `w * 3`, with adjacent rows and zero padding bytes.
- **Whole-source resample**: `w` and `h` are the output dimensions, `format` is `rgb8` or `gray8`, and `stream_id`, `chunks`, and `bytes` describe the pixel stream. Both formats are tightly packed and row-major from the top-left pixel. `rgb8` contributes three bytes per pixel in R, G, B order with `bytes = w * h * 3`; `gray8` contributes one byte per pixel with `bytes = w * h`.

For each pixel stream, `stream_id`, `chunks`, and `bytes` are required metadata. `img.frame.ok` is the begin metadata envelope, and the declared counts determine completion. A receiver completes the stream after exactly `chunks` kind-1 frames when their cumulative raw length equals `bytes`. Chunk-count mismatches, raw-byte-length mismatches, unknown stream IDs, chunks arriving after completion, and nonzero `flags` are frame-layer protocol errors; the receiver closes the connection.

In-band pixel streams satisfy `bytes <= SNAP_SLOT_BYTES` = 48 KiB (`spec/vm.md` §1.0); a selector request exceeding that budget receives a `field` error before streaming. `img.arm_preview` arms out-of-band full-frame UI previews (Pixel Assist) through a one-shot ephemeral LAN data plane that bypasses the microcontroller.

`img.watch` requests push-model telemetry for DSL `\call{img.watch}`. Coordinates satisfy `x, y >= 0`; rectangle dimensions satisfy `w, h` in `[1, 128]`. A sampling region exceeding native source bounds produces provider error `out_of_bounds`, and other invalid parameters produce `field`. The provider samples the named point (`x`,`y` → packed `0xRRGGBB`) or rectangle (`x,y,w,h` → per-channel mean using floor integer division, packed `0xRRGGBB`) every `interval_ms`. Each sample uses `event.push` with `name: "img.<name>"` and `fields: {"val": <sample>}`, so macros synchronize through the existing `\wait{ $slot.img.<name>.val }` signal path. The subscription expires `duration_ms` after acceptance, providing a fail-safe lease if the device vanishes. The device tracks the lease deadline. An `img.watch` with an existing `name` replaces the subscription and registers `img.<name>` anew. The device sends `img.unwatch` for active watches on macro completion or abort. Unwatch, lease expiry, and provider disconnect each unregister the corresponding signal; a late `event.push` then receives `unknown_signal`. Unregistration retains slot `val` and `pending` under the cross-macro lifecycle rules. Successful `img.watch` registration of `img.<name>` admits the provider's `event.push` frames through the `unknown_signal` check.

The capture provider authenticates as a client and sends `img.hello`. The device may then send it `id`-correlated reverse requests. A second `img.hello` replaces the registration; disconnect clears it. Provider errors use `capture_failed`, `field`, `out_of_bounds`, or `unknown_type` in the standard `error` envelope. Coordinates are native source pixels. Resampling requires the explicit `w,h` selector, and color-depth conversion follows `format`.

`event.push` is the inbound half of the DSL `net` namespace. Firing `net.msg` projects fields into `$slot.net.msg.<field>` receptors and latches `pending = 1` (`spec/runtime.md` §1.1). A scalar projection uses `fields: {"val": <i32>}` and updates both `val` and `pending`. Empty `fields: {}` is a notification: it sets `$slot.<name>.val.pending = 1` and retains the current `val`. A multi-field object projects each entry to its corresponding slot. The latch retains an event that arrives before a waiter; the next `\wait{ $slot.net.msg.val }` consumes it immediately on entry.

`MAX_SLOTS = 256` bounds distinct `(signal, field)` receptors. Only initial receptor creation consumes capacity; updates to existing receptors proceed at every occupancy level. Before applying an `event.push`, the device counts every new receptor required by its complete field set. Insufficient remaining capacity rejects the entire projection with `resource_exhausted`, leaving receptor capacity, `$err`, and all existing slot values and latches unchanged.

## ADR-1: Encoding and Channel Choices

**Encoding: JSON.** Neither the official nor community CircuitPython bundle ships msgpack (verified against both bundle indexes, 2026-10). Using msgpack would require a vendored codec and lack `circup` pinning. On the target board (ESP32-S3, CircuitPython 10.3.0), the built-in `json` codec round-trips a typical 89-byte status envelope in 0.42 ms with 416 B allocation churn, and a 1.2 KB macro-submit envelope in 3.27 ms with 2.5 KB churn, well below macro-execution timescales. Phase 3 bulk binary payloads bypass JSON through kind-1 frames, removing msgpack's remaining advantage.

**Channel: raw TCP with custom framing.** The device's USB connects to controlled host PC1, and its endpoint budget is exhausted by HID keyboard, mouse, and CDC serial. The web UI may run on PC2. PC1's companion therefore needs a persistent bidirectional channel for host state and macro orchestration. HTTP lacks server-initiated messages, and long polling would exhaust the device's small HTTP connection pool. One ordered TCP stream multiplexes requests, responses, and pushes through `id` correlation, with binary frames and envelopes sharing transport ordering.

**Auth: fixed-length prologue.** The 37-byte magic+version+SHA-256 prologue keeps auth parsing out of the hot loop and rejects stray traffic such as port scans and wrong services before frame handling. The wire path performs magic/version checks and a digest comparison. Its threat model matches the existing plaintext-LAN HTTP Bearer token model.

## ADR-2: Image Pipeline (Phase 3, Decided 2026-10)

On-device and OBS measurements appear below. The message table defines `img.frame`; `spec/runtime.md` §3.3 specifies the snapshot-centric exact-pixel DSL family built on it.

**Capture: OBS script (obspython), host-side decode.** OBS exposes source screenshots to Python through `obs_frontend_take_source_screenshot`, the `SCREENSHOT_TAKEN` event, and `obs_frontend_get_last_screenshot`. This supplies GPU-captured pixels from any OBS source, including DirectX-rendered Windows windows, through one script shared by macOS (dev host) and Windows (target), avoiding per-platform capture code. Screenshot output is PNG (JXR for HDR on Windows) at native source resolution before host decode. The script decodes with Pillow in OBS's Python environment, keeping image codecs off the device. OBS refuses concurrent screenshots, so the script serializes capture requests internally.

**Semantics: stateless current frame.** Every `img.frame` request performs a fresh capture, extraction, and response. Capture work on this path occurs while handling requests. Each request pays the full capture-chain cost (~450 ms), bounding polling at ~2 Hz, which fits macro semantics.

**Matching on the device.** The device hosts matching; the host is a thin capture-and-extract pipe in OBS's process. A separate host recognizer would be an anti-cheat liability on the controlled machine, while OBS is a mainstream whitelisted tool. The row-convolution NCC algorithm uses ulab and produced exact argmax hits on planted templates in on-device verification. CNN/YOLO-class recognition (ESP-DL/microlite custom firmware) is deferred to a Phase 3.1 platform spike.

**Connection model: one client, reverse requests.** The OBS script is a persistent client of the device's :7373 listener using the same auth prologue. `img.*` requests flow device→script over that connection, and responses return with the request `id`. Symmetric envelope/frame rules reuse the existing listener, port, and protocol. Binary responses use kind-1 frames correlated by `stream_id` to their metadata envelope. Required `stream_id`, `chunks`, and `bytes` metadata describes the complete stream; `bytes` lets the receiver preallocate and place each chunk at its TCP-order offset, avoiding O(n²) append-growth copies. Completion requires exactly the declared chunk count and cumulative raw length, with either mismatch treated as a protocol error.

**Coordinate space.** Coordinates are native source pixels, and pixel-exact paths use native resolution. Every `img.frame` response carries source-native `width`/`height`; Retina-class hosts capture at 2x logical size, providing more addressable pixels. A ~1512x982 logical window was verified to capture at 3024x1964. Normalized frame space was considered and shelved with template matching; a future P3 design will declare it explicitly.

**Selector budgets (measured).** Capture ~400 ms + decode ~38 ms + steady-state RTT ~26 ms + transfer at ~127 KB/s yields: `points` ~0.5 s (~2 Hz color checks), `rect` 256² ~2 s (pre-cap provider benchmark retained for performance history), and `w,h` gray 80x60 ~0.6 s. The exact-pixel family (`img.snap`/`img.at`/`img.diff`, `spec/runtime.md` §3.3 and §4) rides `rect`. Template matching (`w,h`) is deferred to the P3 discussion with CNN support. Benchmarks for that decision are row-convolution NCC with exact argmax: T=16 on 80x60 = 573 ms, T=8 on 160x120 = 1245 ms, T=16 = 2691 ms, T=32 = 6872 ms (quadratic in template edge; ROI windows are the planned lever). Raw-byte → ulab-float ingest is 6 ms; PSRAM peaked at ~160 KB used of ~7.5 MB free.

**WiFi power management.** CircuitPython defaults to modem sleep (`wifi.PowerManagement.MIN`): the AP buffers inbound packets until the next DTIM wake, which cost ~350 ms per connection setup and produced sawtooth ping RTTs (30–120 ms cycling; router RTT 3–7 ms; warm-connection RTT 26 ms). The device is USB-powered, so `code.py` sets `PowerManagement.NONE` after connecting, producing flat 3–15 ms ping RTTs.

**Pixel Assist: out-of-band ephemeral direct LAN preview.** Pixel Assist supports interactive whole-source inspection (zoomed 1000×+ for pixel-exact coordinate and color selection). The design preserves bit-exact RGB8 within the microcontroller's memory and throughput limits:

- *Separate control and data planes*: Bridging a 2–3 MiB full-frame PNG across the ESP32-S3's single-threaded raw TCP stack (~120 KB/s) would take ~40 seconds, fragment into 1600+ TCP MSS packets, and exceed the device's 48 KiB stream budget (`spec/vm.md` Appendix A). The device therefore manages control signaling, while bulk data moves directly between browser PC3 and OBS PC2 over the LAN.
- *Ephemeral server lifecycle*: In response to `img.arm_preview` on the existing :7373 connection, OBS binds an ephemeral socket to OS dynamic port `0.0.0.0:0` with a 5-second timeout and accepts exactly one inbound HTTP connection. This replaces permanent background servers and persistent listening ports.
- *Lazy capture*: Arming defers screenshot capture and frame-buffer allocation until an authenticated client connects. Disconnect or timeout ends the arm before image generation or buffering.
- *HMAC-SHA256 asymmetric ticket distribution*:
  1. The MCU generates an ephemeral 16-byte random `nonce` (32 hex characters) and delivers it to OBS in `img.arm_preview`.
  2. The browser polls `GET /img/capture` and receives `{ "url": "http://<obs_ip>:<port>/capture", "nonce": "<nonce>" }`.
  3. The browser signs the request with the shared secret: `sig = HMAC-SHA256(MYCRO_TOKEN, nonce)`, using a JavaScript implementation compatible with non-secure HTTP LAN origins. The `sig` query value is the 32-byte digest encoded as 64 lowercase hexadecimal characters.
  4. The browser issues `GET /capture?type=png&nonce=<nonce>&sig=<sig>` directly to OBS.
  5. OBS verifies the signature with constant-time `hmac.compare_digest`, takes the GPU screenshot, compresses PNG, responds with `HTTP 200 OK` and CORS `Access-Control-Allow-Origin: *`, then closes and unbinds the socket immediately (~50 ms total connection lifetime).
  - *Dev mode alignment (A2)*: With `MYCRO_TOKEN` unset or empty, HMAC-SHA256 uses the default empty key `b""`. Signature verification, nonce matching and ephemeral single-use enforcement remain active. Tests including A2 and G7b skip invalid-token rejection cases in this configuration.

Preview requests carry HMAC signatures, the device handles control signaling, and multi-megabyte lossless previews transfer directly over the LAN.
