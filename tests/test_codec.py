"""Stage 1 codec tests: frame packing, stream reassembly, envelope
encode/decode, fixed-length auth header. Pure wire-level; no dispatcher."""

import pytest

from codec import (
    AUTH_LEN,
    AUTH_MAGIC,
    KIND_BINARY,
    KIND_JSON,
    MAX_BINARY_CHUNK,
    MAX_PAYLOAD,
    PROTO_VERSION,
    CodecError,
    Reassembler,
    build_auth_header,
    check_auth_header,
    decode_envelope,
    encode_envelope,
    pack_frame,
)

ENV = {"v": 1, "type": "macro.status", "id": 42, "ts": 123456, "payload": {}}


# --- envelope ---

def test_envelope_roundtrip():
    assert decode_envelope(encode_envelope(ENV)) == ENV


def test_envelope_roundtrip_unicode_and_nested():
    env = {"v": 1, "type": "t", "id": 1, "ts": 0,
           "payload": {"text": "héllo", "list": [1, 2.5, True, None]}}
    assert decode_envelope(encode_envelope(env)) == env


def test_decode_rejects_malformed_json():
    with pytest.raises(CodecError):
        decode_envelope(b"{not json")


def test_decode_rejects_non_object():
    with pytest.raises(CodecError):
        decode_envelope(b"[1,2,3]")


def test_decode_rejects_bad_utf8():
    with pytest.raises(CodecError):
        decode_envelope(b'{"a": "\xff\xfe"}')


# --- frame packing ---

def test_pack_frame_header_layout():
    payload = b"AB"
    frame = pack_frame(KIND_JSON, payload)
    assert frame[:4] == bytes([2, 0, 0, 0])
    assert frame[4:] == payload


def test_pack_frame_binary_sets_kind_bit():
    frame = pack_frame(KIND_BINARY, b"\x00" * 3)
    assert frame[3] == 0x80  # u32 LE: kind bit lands in the top byte
    assert frame[:3] == bytes([3, 0, 0])


def test_pack_frame_enforces_caps():
    with pytest.raises(CodecError):
        pack_frame(KIND_JSON, b"x" * (MAX_PAYLOAD + 1))
    with pytest.raises(CodecError):
        pack_frame(KIND_BINARY, b"x" * (MAX_BINARY_CHUNK + 1))


# --- reassembly ---

def test_reassemble_single_frame():
    r = Reassembler()
    frame = pack_frame(KIND_JSON, encode_envelope(ENV))
    assert r.feed(frame) == [(KIND_JSON, encode_envelope(ENV))]


def test_reassemble_byte_by_byte():
    r = Reassembler()
    frame = pack_frame(KIND_JSON, b"hello")
    out = []
    for i in range(len(frame)):
        out += r.feed(frame[i : i + 1])
    assert out == [(KIND_JSON, b"hello")]


def test_reassemble_coalesced_frames():
    r = Reassembler()
    f1 = pack_frame(KIND_JSON, b"one")
    f2 = pack_frame(KIND_BINARY, b"\x01\x02two")
    assert r.feed(f1 + f2) == [(KIND_JSON, b"one"), (KIND_BINARY, b"\x01\x02two")]


def test_reassemble_partial_then_rest():
    r = Reassembler()
    frame = pack_frame(KIND_JSON, b"payload")
    assert r.feed(frame[:5]) == []
    assert r.feed(frame[5:]) == [(KIND_JSON, b"payload")]


def test_reassemble_zero_length_frame():
    r = Reassembler()
    assert r.feed(pack_frame(KIND_JSON, b"")) == [(KIND_JSON, b"")]


def test_reassemble_oversize_header_raises():
    r = Reassembler()
    with pytest.raises(CodecError):
        r.feed(bytes([0xFF, 0xFF, 0xFF, 0x7F]))  # kind=0, length near 2GB


def test_reassemble_oversize_binary_raises():
    r = Reassembler()
    with pytest.raises(CodecError):
        r.feed(bytes([0x01, 0x00, 0x60, 0x80]))  # kind=1, length 0x6001 > cap


# --- auth header ---

def test_auth_header_fixed_length():
    assert len(build_auth_header("secret")) == AUTH_LEN


def test_auth_roundtrip():
    token = "correct horse"
    assert check_auth_header(build_auth_header(token), token)


def test_auth_rejects_wrong_token():
    assert not check_auth_header(build_auth_header("nope"), "correct horse")


def test_auth_rejects_wrong_magic():
    h = bytearray(build_auth_header("t"))
    h[0] ^= 0xFF
    assert not check_auth_header(bytes(h), "t")


def test_auth_rejects_wrong_version():
    h = bytearray(build_auth_header("t"))
    h[len(AUTH_MAGIC)] = PROTO_VERSION + 1
    assert not check_auth_header(bytes(h), "t")


def test_auth_rejects_short_header():
    assert not check_auth_header(b"MY", "t")


def test_auth_dev_mode_skips_hash_but_checks_magic():
    assert check_auth_header(AUTH_MAGIC + bytes([PROTO_VERSION]) + b"\x00" * 32, None)
    assert check_auth_header(AUTH_MAGIC + bytes([PROTO_VERSION]) + b"\x00" * 32, "")
    assert not check_auth_header(b"XXXX" + bytes([PROTO_VERSION]) + b"\x00" * 32, None)
