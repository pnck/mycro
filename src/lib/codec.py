"""Phase 2 wire codec: framed JSON envelopes over raw TCP.

Frame layout (both directions, one ordered stream per connection):

    u32 LE header:  bit31   = kind (0 = JSON envelope, 1 = binary chunk)
                    bit0-30 = payload length in bytes
    kind 0 payload: UTF-8 JSON envelope {"v", "type", "id", "ts", "payload"}
    kind 1 payload: u16 stream_id + u16 flags + raw bytes (Phase 3 bulk
                    transfers; Phase 2 endpoints reject this kind)

Connection prologue (raw TCP): the client leads with a fixed-length auth
header, MAGIC + u8 protocol version + 32B SHA-256 of the token. When no
token is configured (dev mode) the hash is not checked but magic and
version still are.

Envelope semantics (field validation, message types) live in proto.py;
this module only moves bytes <-> dicts and bytes <-> frames.
"""

import hashlib
import json

PROTO_VERSION = 1

AUTH_MAGIC = b"MYCR"
AUTH_LEN = len(AUTH_MAGIC) + 1 + 32  # magic + version + SHA-256 digest

KIND_JSON = 0
KIND_BINARY = 1

FLAG_STREAM_END = 1  # chunk flags bit0: last chunk of the stream

MAX_PAYLOAD = 8192  # JSON envelope cap (macro source maxes at 4096)
MAX_BINARY_CHUNK = 8192  # per-chunk cap for future bulk streams

_KIND_SHIFT = 0x80000000
_LEN_MASK = 0x7FFFFFFF


class CodecError(Exception):
    """Malformed frame, oversized payload, or undecodable envelope."""


def token_digest(token):
    # CircuitPython's hashlib exposes algorithms only through new();
    # hashlib.new("sha256", data) works on both CP and CPython
    return hashlib.new("sha256", token.encode("utf-8")).digest()


def build_auth_header(token):
    return AUTH_MAGIC + bytes([PROTO_VERSION]) + token_digest(token)


def check_auth_header(header, token):
    """Validate a fixed-length auth header. token None/empty = dev mode
    (magic + version still enforced). Compares the full digest to avoid
    early-exit timing tells."""
    if len(header) != AUTH_LEN:
        return False
    if header[: len(AUTH_MAGIC)] != AUTH_MAGIC:
        return False
    if header[len(AUTH_MAGIC)] != PROTO_VERSION:
        return False
    if not token:
        return True
    diff = 0
    for a, b in zip(header[len(AUTH_MAGIC) + 1 :], token_digest(token)):
        diff |= a ^ b
    return diff == 0


def encode_envelope(env):
    return json.dumps(env).encode("utf-8")


def decode_envelope(data):
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)  # memoryview from the reassembler; JSON is small
    try:
        env = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as e:
        raise CodecError("invalid envelope JSON: {}".format(e))
    if not isinstance(env, dict):
        raise CodecError("envelope is not a JSON object")
    return env


def pack_frame(kind, payload):
    cap = MAX_PAYLOAD if kind == KIND_JSON else MAX_BINARY_CHUNK
    if len(payload) > cap:
        raise CodecError("payload {}B over {}B cap".format(len(payload), cap))
    n = len(payload) | (kind << 31)
    return bytes((n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF, (n >> 24) & 0xFF)) + payload


def split_chunk(payload):
    """Parse a kind-1 payload: u16 LE stream_id + u16 LE flags + raw.
    The raw part is a VIEW of the payload, not a copy — valid until the
    caller's next reassembler feed."""
    if len(payload) < 4:
        raise CodecError("binary chunk shorter than its 4-byte header")
    sid = payload[0] | (payload[1] << 8)
    flags = payload[2] | (payload[3] << 8)
    return sid, flags, payload[4:]


class Reassembler:
    """Incremental frame reassembly over ONE self-managed buffer.

    The buffer is allocated once per connection and never grows: the
    transport recv_into()s directly into write_view(), then feed() parses
    complete frames in place. Frame payloads come back as memoryviews
    into the buffer — valid until the next write_view/feed cycle — so
    the hot path copies each byte exactly twice: socket->buffer (the
    recv itself) and buffer->its final destination (the consumer's
    slice-assign). No per-chunk heap allocation.

    Compaction of the unconsumed tail is DEFERRED to the start of the
    next cycle: it must never run in the same feed that minted the
    views, or the views would point at memmove'd-over memory.

    Capacity fits one full binary frame plus one full read, so after
    compaction there is always room for a max-size recv."""

    CAPACITY = 2 * (MAX_BINARY_CHUNK + 8)

    def __init__(self):
        self._buf = bytearray(self.CAPACITY)
        # tail bounce for compaction: two memcpys, zero allocation
        # (a bytearray slice copy would allocate per compaction)
        self._bounce = bytearray(MAX_BINARY_CHUNK + 8)
        self._have = 0
        self._pos = 0  # consumed prefix, compacted lazily next cycle

    def _compact(self):
        if self._pos:
            tail = self._have - self._pos
            if tail:
                memoryview(self._bounce)[:tail] = \
                    memoryview(self._buf)[self._pos:self._have]
                memoryview(self._buf)[:tail] = memoryview(self._bounce)[:tail]
            self._have = tail
            self._pos = 0

    def write_view(self, nbytes=MAX_BINARY_CHUNK):
        """Writable tail for recv_into: room for up to nbytes. Compacts
        first — any views from the previous cycle die here."""
        self._compact()
        return memoryview(self._buf)[self._have:self._have + nbytes]

    def feed(self, data):
        """Commit received bytes and extract complete frames.

        `data` is either an int (that many bytes already landed in
        write_view) or a bytes-like to copy in. Returns (kind, payload)
        pairs; payloads are views valid until the next feed/write_view."""
        if isinstance(data, int):
            self._have += data  # write_view already compacted
        else:
            self._compact()
            n = len(data)
            if self._have + n > len(self._buf):
                raise CodecError("reassembler overflow")
            self._buf[self._have:self._have + n] = data
            self._have += n
        frames = []
        pos = 0
        while self._have - pos >= 4:
            i = pos
            hdr = (
                self._buf[i]
                | (self._buf[i + 1] << 8)
                | (self._buf[i + 2] << 16)
                | (self._buf[i + 3] << 24)
            )
            kind = KIND_BINARY if hdr & _KIND_SHIFT else KIND_JSON
            length = hdr & _LEN_MASK
            cap = MAX_PAYLOAD if kind == KIND_JSON else MAX_BINARY_CHUNK
            if length > cap:
                raise CodecError(
                    "frame {}B over {}B cap (kind {})".format(length, cap, kind)
                )
            if self._have - pos < 4 + length:
                break
            payload = memoryview(self._buf)[pos + 4: pos + 4 + length]
            frames.append((kind, payload))
            pos += 4 + length
        self._pos = pos  # compaction deferred: the views above must
        return frames    # keep pointing at intact memory
