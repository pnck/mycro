"""Unit guards for tests/device/reference_provider.py — the G7-G9 device
cases depend on make_png and the frame builders; a refactor must not be
able to drop them silently (happened once)."""

import json
import os
import socket
import struct
import sys
import threading
import zlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device"))

import reference_provider as rp  # noqa: E402


def _decode_png(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    i, idat = 8, b""
    while i < len(png):
        ln = struct.unpack(">I", png[i:i + 4])[0]
        if png[i + 4:i + 8] == b"IDAT":
            idat += png[i + 8:i + 8 + ln]
        i += 12 + ln
    return w, h, zlib.decompress(idat)


def test_make_png_default_frame():
    w, h, raw = _decode_png(rp.make_png(rp.W, rp.H, rp.frame_default()))
    assert (w, h) == (80, 60)
    off = 8 * (w * 3 + 1) + 10 * 3 + 1  # pixel(10,8), filter-0 rows
    assert tuple(raw[off:off + 3]) == (70, 110, 150)


def test_make_png_fullhd_frame():
    w, h, raw = _decode_png(rp.make_png(rp.BIG_W, rp.BIG_H, rp.frame_fullhd()))
    assert (w, h) == (1920, 1080)
    assert len(raw) == (w * 3 + 1) * h


def test_frames_are_deterministic_and_distinct():
    assert rp.frame_default() == rp.frame_default()
    assert rp.frame_default() != rp.frame_changed()


def test_provider_default_frame_is_gradient():
    p = rp.ReferenceProvider("127.0.0.1", 7373, "")
    w, h, rgb = p._frame
    assert (w, h) == (80, 60) and rgb == rp.frame_default()


def _recv_exact(sock, n):
    data = bytearray()
    while len(data) < n:
        part = sock.recv(n - len(data))
        assert part
        data.extend(part)
    return bytes(data)


def _recv_frame(sock):
    header = struct.unpack("<I", _recv_exact(sock, 4))[0]
    return header >> 31, _recv_exact(sock, header & 0x7FFFFFFF)


def _recv_json(sock):
    while True:
        kind, payload = _recv_frame(sock)
        if kind == 0:
            return json.loads(payload.decode("utf-8"))
        # Used only for responses that do not declare a binary stream.
        raise AssertionError("unexpected binary frame")


def _recv_stream(sock):
    kind, payload = _recv_frame(sock)
    assert kind == 0
    env = json.loads(payload.decode("utf-8"))
    meta = env["payload"]
    data = bytearray()
    for _ in range(meta["chunks"]):
        kind, chunk = _recv_frame(sock)
        assert kind == 1
        sid, flags = struct.unpack("<HH", chunk[:4])
        assert sid == meta["stream_id"] and flags == 0
        data.extend(chunk[4:])
    assert len(data) == meta["bytes"]
    return env, bytes(data)


def test_reference_provider_image_contracts_over_real_protocol():
    provider_sock, device_sock = socket.socketpair()
    device_sock.settimeout(2)
    original_create_connection = rp.socket.create_connection
    rp.socket.create_connection = lambda *args, **kwargs: provider_sock
    p = rp.ReferenceProvider("127.0.0.1", 7373, "")
    thread = threading.Thread(target=p._run, daemon=True)
    try:
        thread.start()
        assert _recv_exact(device_sock, len(rp.AUTH_MAGIC) + 33)[:4] == rp.AUTH_MAGIC
        hello = _recv_json(device_sock)
        assert hello["type"] == "img.hello"

        rects = [
            {"x": -1, "y": 0, "w": 1, "h": 1},
            {"x": 0, "y": -1, "w": 1, "h": 1},
            {"x": rp.W - 1, "y": 0, "w": 2, "h": 1},
            {"x": 0, "y": rp.H - 1, "w": 1, "h": 2},
            {"x": rp.W, "y": 0, "w": 1, "h": 1},
        ]
        for rid, rect in enumerate(rects, 1):
            p._send_env(device_sock, "img.frame", rid, {"rect": rect})
            reply = _recv_json(device_sock)
            assert reply["type"] == "error"
            assert reply["payload"]["code"] == "out_of_bounds"

        p._send_env(device_sock, "img.frame", 10,
                    {"rect": {"x": 1, "y": 1, "w": 2, "h": 1}})
        response, stream = _recv_stream(device_sock)
        payload = response["payload"]
        assert (payload["w"], payload["h"]) == (2, 1)
        assert "crop_w" not in payload and "crop_h" not in payload
        assert payload["chunks"] == 1
        assert stream == bytes((8, 48, 88, 11, 51, 91))

        p._send_env(device_sock, "img.frame", 11,
                    {"points": [[0, 0], [1, 0]]})
        payload = _recv_json(device_sock)["payload"]
        assert payload["colors"] == [0x002850, 0x032B53]

        p._send_env(device_sock, "img.frame", 12,
                    {"points": [[0, 0]] * 256})
        assert len(_recv_json(device_sock)["payload"]["colors"]) == 256
        p._send_env(device_sock, "img.frame", 13,
                    {"points": [[0, 0]] * 257})
        reply = _recv_json(device_sock)
        assert reply["type"] == "error"
        assert reply["payload"]["code"] == "field"

        for rid, request in [
            (14, {"points": [[0]]}),
            (15, {"rect": {"x": 0, "y": 0, "w": 1}}),
            (16, {"rect": {"x": 0, "y": 0, "w": 129, "h": 1}}),
            (19, {"w": 160, "h": 120, "format": "rgb8"}),
        ]:
            p._send_env(device_sock, "img.frame", rid, request)
            reply = _recv_json(device_sock)
            assert reply["type"] == "error"
            assert reply["payload"]["code"] == "field"

        p._send_env(device_sock, "img.frame", 17,
                    {"points": [[-1, 0]]})
        reply = _recv_json(device_sock)
        assert reply["payload"]["code"] == "out_of_bounds"

        p._send_env(device_sock, "img.frame", 18,
                    {"w": 2, "h": 1, "format": "rgb8"})
        response, stream = _recv_stream(device_sock)
        payload = response["payload"]
        assert (payload["width"], payload["height"]) == (rp.W, rp.H)
        assert (payload["w"], payload["h"]) == (2, 1)
        assert stream == bytes((0, 40, 80, 120, 160, 200))
    finally:
        device_sock.close()
        p._stop.set()
        thread.join(timeout=3)
        rp.socket.create_connection = original_create_connection
    assert not thread.is_alive()


@pytest.mark.parametrize("token,reject_sig,expected_status", [
    ("test_tok", False, 200),
    ("", False, 200),
    ("", True, 403),
])
def test_reference_provider_preview_worker(token, reject_sig, expected_status):
    """Preview verifies HMAC in both configured-token and empty-key dev modes."""
    import hashlib
    import hmac
    import socket

    client_sock, srv_conn = socket.socketpair()

    class MockListener:
        def __init__(self, conn):
            self.conn = conn
            self.closed = False

        def settimeout(self, t):
            pass

        def accept(self):
            if self.conn:
                c = self.conn
                self.conn = None
                return c, ("127.0.0.1", 12345)
            raise socket.timeout

        def close(self):
            self.closed = True
            if self.conn:
                self.conn.close()

    mock_listen = MockListener(srv_conn)
    p = rp.ReferenceProvider("127.0.0.1", 7373, token)
    nonce = "unit_nonce_123"
    exp_sig = hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()
    req_sig = "0" * 64 if reject_sig else exp_sig

    worker_thread = threading.Thread(
        target=p._preview_worker,
        args=(mock_listen, rp.W, rp.H, rp.frame_default(), exp_sig, nonce),
        daemon=True
    )
    worker_thread.start()

    req = f"GET /capture?type=png&nonce={nonce}&sig={req_sig} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    client_sock.sendall(req.encode("utf-8"))

    buf = bytearray()
    while True:
        chunk = client_sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    client_sock.close()
    worker_thread.join(timeout=2.0)

    assert f"HTTP/1.1 {expected_status} " in buf.decode("latin-1")
    if expected_status != 200:
        assert b"Forbidden" in buf
        return
    assert b"Content-Type: image/png" in buf
    idx = buf.find(b"\r\n\r\n")
    assert idx != -1
    png_data = buf[idx + 4:]
    assert png_data[:8] == b"\x89PNG\r\n\x1a\n"

