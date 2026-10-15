# MYCRO Protocol v1

Machine-facing protocol for host companion programs, spoken over raw TCP
(port 7373). The HTTP REST endpoints remain the human/debug channel; this
document is the normative reference for the socket protocol and is written
to be sufficient for implementing an independent client.

## Frame layer

Every connection carries an ordered stream of frames, each:

```
u32 LE header:  bit31   = kind (0 = JSON envelope, 1 = binary chunk)
                bit0-30 = payload length in bytes
payload:        kind 0 → UTF-8 JSON envelope
                kind 1 → u16 LE stream_id + u16 LE flags + raw bytes
```

Caps: JSON envelopes ≤ 8192 bytes; binary chunks ≤ 8192 bytes each.
A frame whose header exceeds the cap for its kind is a protocol error —
the server closes the connection.

Binary frames are the bulk-transfer path (screenshot pushes for vision);
a begin/done pair of JSON envelopes carries the metadata and correlates by
`stream_id`, so binary payloads never touch JSON or base64. Servers that
have no binary consumer reject kind-1 frames.

## Connection prologue (auth)

The client leads with a fixed-length 37-byte header before any frame:

```
offset 0:  4 bytes  magic "MYCR"
offset 4:  1 byte   wire protocol version (currently 1)
offset 5:  32 bytes SHA-256 digest of the token (UTF-8 bytes of MYCRO_TOKEN)
```

The server compares the digest full-length. Mismatch of magic, version,
or digest closes the connection without a reply. When no token is
configured on the device (dev mode), the digest is not checked; magic and
version still are. The wire version gates the transport framing and is
distinct from the envelope `v` field, which versions message semantics.

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
| `ts` | int | sender timestamp in milliseconds (device: `time.monotonic()` since boot; hosts may use epoch ms — receivers must not mix the two) |
| `payload` | obj | per-type body |

Errors are reported as `{"type": "error", "id": <request id>, "payload":
{"code": <machine code>, "message": <human text>}}`.

## Message types

Request → response pairs echo the request `id`; a successful response's
type is the request type plus `.ok`. Failures use `type: "error"` with a
machine-readable `payload.code` (`bad_version` / `bad_type` / `bad_id` /
`bad_ts` / `bad_payload` / `unknown_type` / `field` / `compile_error` /
`unknown_signal` / `state` / `internal`).

| type | direction | payload | response payload |
|---|---|---|---|
| `macro.submit` | client → server | `code`: DSL source string | `bytes`: compiled bytecode size |
| `macro.status` | client → server | — | `state`: `idle`/`running`/`done`/`aborted`/`error`, optional `detail` |
| `macro.abort` | client → server | — | — (error `state` when nothing is running) |
| `device.info` | client → server | — | `board`, `fw`, `free_mem`, `proto` (protocol version) |
| `event.push` | client → server | `name`: signal name, `fields`: object of i32 slot values | — (error `unknown_signal` for unregistered signals) |
| `macro.result` | server → clients | `state`, `ms` (execution wall time) | push, `id` = 0 |
| `net.msg` | server → clients | emitted by `\call{net.send}` | push, `id` = 0 |
| `img.hello` | capture client → server | `caps`: list | push, `id` = 0; registers the client as the capture provider |
| `img.frame` | server → capture client | one selector: `rect` {x,y,w,h} → lossless RGB8 crop / `points` [[x,y],…] → exact colors / `w`,`h` + `format` (`rgb8`/`gray8`) → whole-source resample / `format`:`png` → native whole-source PNG bytes, untouched | `img.frame.ok` with `width`/`height` (native dims), selector-specific fields, and for pixel streams `stream_id`/`chunks` followed by kind-1 chunks |

`img.frame` is the **only** provider verb — stateless "current frame":
every request takes a fresh screenshot and extracts per the selector.
The provider is a client like any other; after auth it sends
`img.hello`, and from then on the device may send it reverse requests
(`id`-correlated). A second `img.hello` replaces the registration;
disconnecting clears it. Errors use the usual `error` type
(`capture_failed`, `field`, `unknown_type`). All coordinates are native
source pixels; resampling happens only under the explicit `w,h` selector
and never changes color depth unless `format` says so.

`event.push` is the inbound half of the DSL `net` namespace: firing
`net.msg` wakes device macros suspended in `\wait{net.msg}` and publishes
the fields as last-published state, read via `$net.msg.<field>` slot
references. `\wait` observes the next fire after entry only — a fire with
no waiter is discarded (slots still record it).

## ADR-1: encoding and channel choices

**Encoding: JSON, no msgpack.** Neither the official nor the community
CircuitPython bundle ships a msgpack library (verified against both bundle
indexes, 2026-10), so msgpack would mean vendoring a private codec with no
circup pinning. Measured on the target board (ESP32-S3, CP 10.3.0), the
built-in `json` codec round-trips a typical 89-byte status envelope in
0.42 ms with 416 B allocation churn, and a 1.2 KB macro-submit envelope in
3.27 ms with 2.5 KB churn — far below macro-execution timescales. Bulk
binary payloads (Phase 3 screenshots) bypass JSON entirely via kind-1
frames, which removes msgpack's remaining advantage.

**Channel: raw TCP with a custom framed protocol.** The device's USB is
plugged into the controlled host (PC1) and its USB endpoint budget is
exhausted (HID keyboard + mouse + CDC serial), while the web UI may be
driven from a second machine (PC2). PC1's companion program therefore
needs a persistent, bidirectional, non-HTTP channel to report host state
and orchestrate macros; HTTP cannot carry server-initiated messages, and
its long-polling workarounds would exhaust the device's tiny HTTP
connection pool. One ordered TCP stream gives request/response/push
multiplexing with id correlation and lets binary frames interleave with
envelopes without inventing ordering on top of HTTP.

**Auth: fixed-length prologue header.** A 37-byte magic+version+SHA-256
prologue keeps the hot loop free of auth parsing, rejects stray traffic
(port scans, wrong services) before any frame handling, and stores
nothing but a digest comparison on the wire path. The threat model is the
same LAN plaintext model as the existing HTTP Bearer token.

## ADR-2: image pipeline (Phase 3, decided 2026-10)

Decisions below are backed by on-device/OBS measurements, quoted at the
end. The `img.frame` verb lives in the message-type table above; the
DSL surface built on it (the snapshot-centric exact pixel family) is
specified in spec/vm.md Part II.

**Capture: an OBS plugin (obspython), host-side decode.** OBS's source
screenshot API is exposed to Python scripts
(`obs_frontend_take_source_screenshot` + the `SCREENSHOT_TAKEN` event +
`obs_frontend_get_last_screenshot`), giving GPU-captured pixels of any
OBS source — including DirectX-rendered windows on Windows — with no
per-platform capture code in our hands; the same script runs unchanged
on macOS (dev host) and Windows (target). Screenshots save as PNG only
(JXR for HDR on Windows), at native source resolution, with no
server-side downscale — so the plugin decodes with Pillow (installed
into the same Python OBS loads) and the device needs no image codec at
all. OBS refuses concurrent screenshots; the plugin serializes capture
requests internally.

**Semantics: stateless "current frame".** The plugin holds no frame
cache and runs no refresh timer: every `img.*` request triggers one
fresh capture, one extraction, one response. Freshness is therefore
never in doubt and there is no cache-coherence question; the cost is
the full capture chain (~450 ms) per request, which bounds polling
cadence at ~2 Hz — well inside macro semantics.

**Matching on the device.** The device hosts the matching service; the
host is a thin capture+extract pipe running inside OBS's process (a
separate host-side recognizer would be an anti-cheat liability on the
controlled machine, and OBS itself is a mainstream whitelisted tool).
The algorithm is row-convolution NCC on ulab — verified on-device with
exact argmax hits on planted templates. CNN/YOLO-class recognition is a
Phase 3.1 platform-spike question (ESP-DL/microlite custom firmware) and
is deliberately out of scope here.

**Connection model: one client, reverse requests.** The OBS plugin is a
persistent *client* of the device's :7373 listener (same auth prologue).
`img.*` requests flow device→plugin over that established connection
and responses flow back with the request's `id` — the envelope/frame
rules are symmetric, so no new listener, port, or protocol exists.
Binary responses use kind-1 frames correlated by `stream_id` to their
metadata envelope, exactly as the frame layer already describes.

**Coordinate space.** All coordinates are native source pixels and
pixel-exact paths never resample. Every `img.frame` response carries
the source's native `width`/`height`; Retina-class hosts capture at 2x
logical size, which simply means more addressable pixels. (Verified: a
~1512x982 logical window captures at 3024x1964.) A normalized frame
space for template matching was considered and deliberately shelved with
that feature — when it lands in P3 it will be explicit, never implicit
in a size parameter.

**Selector budgets (measured)** (capture ~400 ms + decode ~38 ms +
steady-state RTT ~26 ms + transfer at ~127 KB/s): `points` ~0.5 s
(~2 Hz color checks), `rect` 256² ~2 s (low-frequency only), `w,h` gray
80x60 ~0.6 s. The DSL's exact pixel family (`img.snap`/`img.at`/
`img.diff`, spec/vm.md handle model) rides `rect`; template matching
(`w,h`) is deferred to the P3 discussion together with the CNN question
— the bench numbers that will drive it: row-conv NCC with exact argmax,
T=16 on 80x60 = 573 ms, T=8 on 160x120 = 1245 ms, T=16 = 2691 ms,
T=32 = 6872 ms (quadratic in template edge; ROI windows are the planned
lever). Ingest (raw bytes → ulab float array) is 6 ms; PSRAM is no
constraint (peak ~160 KB used of ~7.5 MB free).

**WiFi power management.** CircuitPython defaults to modem sleep
(`wifi.PowerManagement.MIN`): the AP buffers inbound packets until the
next DTIM wake, which cost ~350 ms per connection setup and produced
sawtooth ping RTTs (30–120 ms cycling; router RTT 3–7 ms; warm-
connection RTT 26 ms). The device is USB-powered, so code.py now sets
`PowerManagement.NONE` after connecting — flat 3–15 ms ping afterwards.
