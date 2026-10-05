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

(The type catalog lands with the dispatcher stage; see ROADMAP Phase 2.)

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
