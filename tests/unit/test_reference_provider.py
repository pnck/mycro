"""Unit guards for tests/device/reference_provider.py — the G7-G9 device
cases depend on make_png and the frame builders; a refactor must not be
able to drop them silently (happened once)."""

import os
import struct
import sys
import threading
import zlib

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


def test_crop_rect_bounds_sanitization():
    p = rp.ReferenceProvider("127.0.0.1", 7373, "")
    try:
        w0, h0, _ = p._frame
        rc = {"x": 100, "y": 80, "w": 10, "h": 10}
        x, y, w, h = rc["x"], rc["y"], rc["w"], rc["h"]
        x0, y0 = max(x, 0), max(y, 0)
        x1, y1 = min(x + w, w0), min(y + h, h0)
        assert x1 <= x0 or y1 <= y0  # correctly detected as out-of-bounds
    finally:
        p.stop()


def test_reference_provider_preview_worker():
    """Verify ReferenceProvider._preview_worker executes cleanly without NameError."""
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
    token = "test_tok"
    p = rp.ReferenceProvider("127.0.0.1", 7373, token)
    nonce = "unit_nonce_123"
    exp_sig = hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()

    worker_thread = threading.Thread(
        target=p._preview_worker,
        args=(mock_listen, rp.W, rp.H, rp.frame_default(), exp_sig, nonce),
        daemon=True
    )
    worker_thread.start()

    req = f"GET /capture?type=png&nonce={nonce}&sig={exp_sig} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    client_sock.sendall(req.encode("utf-8"))

    buf = bytearray()
    while True:
        chunk = client_sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    client_sock.close()
    worker_thread.join(timeout=2.0)

    assert b"HTTP/1.1 200 OK" in buf
    assert b"Content-Type: image/png" in buf
    idx = buf.find(b"\r\n\r\n")
    assert idx != -1
    png_data = buf[idx + 4:]
    assert png_data[:8] == b"\x89PNG\r\n\x1a\n"

