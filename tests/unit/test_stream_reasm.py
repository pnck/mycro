"""Multi-chunk stream reassembly through the static Reassembler —
the G9 scenario: an announced ~1.2 MB stream arrives as 152 binary
chunks (max-size frames), interleaved with partial TCP reads, and every
chunk must come out in order with its exact payload.

This pins the multi-chunk path that the small G4-G8 streams never
exercise (they all fit in a single chunk).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "lib"))

import codec  # noqa: E402

CHUNK_RAW = 8192 - 4  # payload minus u16 sid + u16 flags (provider side)
TOTAL = 152 * CHUNK_RAW + 123  # ~1.2 MB with a short tail chunk


def _make_stream(sid=7):
    """The bytes a provider puts on the wire for one stream."""
    data = bytes((i * 31 + 7) % 256 for i in range(TOTAL))
    out = bytearray()
    chunks = (len(data) + CHUNK_RAW - 1) // CHUNK_RAW
    for i in range(chunks):
        part = data[i * CHUNK_RAW:(i + 1) * CHUNK_RAW]
        flags = codec.FLAG_STREAM_END if i == chunks - 1 else 0
        payload = sid.to_bytes(2, "little") + flags.to_bytes(2, "little") + part
        out += codec.pack_frame(codec.KIND_BINARY, payload)
    return bytes(out), data


def test_multi_chunk_stream_survives_arbitrary_read_sizes():
    wire, data = _make_stream()
    # bytes-feed is bounded by the buffer capacity (hot path uses
    # write_view+feed(int)); 8192 is the transport's read size
    for read_size in (8192, 4096, 8196, 1024, 1):
        r = codec.Reassembler()
        got = bytearray()
        ends = 0
        off = 0
        while off < len(wire):
            piece = wire[off:off + read_size]
            off += len(piece)
            for kind, payload in r.feed(piece):
                assert kind == codec.KIND_BINARY
                sid, flags, raw = codec.split_chunk(payload)
                assert sid == 7
                got += raw
                ends += bool(flags & codec.FLAG_STREAM_END)
        assert bytes(got) == data, f"read_size={read_size}"
        assert ends == 1, f"read_size={read_size}"


def test_stream_armed_by_write_view_recv_pattern():
    """The device hot path: recv_into(write_view) + feed(n) commits."""
    wire, data = _make_stream()
    r = codec.Reassembler()
    got = bytearray()
    off = 0
    while off < len(wire):
        view = r.write_view()
        n = min(len(view), len(wire) - off, 8192)  # one recv_into worth
        view[:n] = wire[off:off + n]  # stands in for recv_into
        off += n
        for kind, payload in r.feed(n):
            _sid, _flags, raw = codec.split_chunk(payload)
            got += raw
    assert bytes(got) == data


def test_meta_then_chunks_in_one_feed_order_preserved():
    """The response envelope and its first chunks can share one read;
    frames must come out in wire order (arm before chunks)."""
    wire, data = _make_stream()
    env = codec.pack_frame(codec.KIND_JSON,
                           b'{"v":1,"type":"img.frame.ok","id":3,"ts":0,'
                           b'"payload":{"stream_id":7,"chunks":153,'
                           b'"bytes":1244571}}')
    r = codec.Reassembler()
    frames = r.feed(env + wire[:9000])
    kinds = [k for k, _ in frames]
    assert kinds[0] == codec.KIND_JSON
    assert all(k == codec.KIND_BINARY for k in kinds[1:])
