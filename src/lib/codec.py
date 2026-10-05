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


class Reassembler:
    """Incremental frame reassembly for stream transports.

    Feed received bytes; get back all complete frames as (kind, payload)
    pairs. Raises CodecError on an oversized frame header — the transport
    should close the connection on CodecError."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data):
        self._buf += data
        frames = []
        while len(self._buf) >= 4:
            hdr = (
                self._buf[0]
                | (self._buf[1] << 8)
                | (self._buf[2] << 16)
                | (self._buf[3] << 24)
            )
            kind = KIND_BINARY if hdr & _KIND_SHIFT else KIND_JSON
            length = hdr & _LEN_MASK
            cap = MAX_PAYLOAD if kind == KIND_JSON else MAX_BINARY_CHUNK
            if length > cap:
                raise CodecError(
                    "frame {}B over {}B cap (kind {})".format(length, cap, kind)
                )
            if len(self._buf) < 4 + length:
                break
            frames.append((kind, bytes(self._buf[4 : 4 + length])))
            # CircuitPython bytearray has no slice deletion; reslice instead
            self._buf = self._buf[4 + length :]
        return frames
