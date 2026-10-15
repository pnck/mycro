"""Reference capture provider for devicetest (protocol conformance +
img.* e2e without a running OBS).

A formal reference implementation of the provider side of protocol v1
(docs/protocol.md): auth prologue, img.hello, the img.frame verb with
all extraction selectors (rect / points / w+h+format / format:png).

The provider's screen content is explicit frame DATA, not named modes —
a real screen changes over time, and tests set it directly:
`provider.set_frame(w, h, rgb888_bytes)`. Frame builders:
`frame_gradient()` (default; G4/G7/G8 read exact pixels from it),
`frame_nonlinear()` (the change G5 must detect), `frame_big()`
(1920x1080, G9's full-screen throughput case).

gray8 uses the integer BT.601 luma shared with src/lib/imgmatch.py and
obs/obs_mycro.py: (r*77 + g*150 + b*29) >> 8.

Threaded, blocking sockets. Not collected by pytest (not a test_* file).
"""

import hashlib
import json
import socket
import struct
import threading
import zlib

PROTO_VERSION = 1
AUTH_MAGIC = b"MYCR"
CHUNK_RAW = 8192 - 4  # binary frame payload minus u16 stream_id + u16 flags
FLAG_STREAM_END = 1

W, H = 80, 60
BIG_W, BIG_H = 1920, 1080


def frame_gradient(w=W, h=H):
    """Deterministic base frame: v(x,y) = (x*3 + y*5) % 256 as (v, v+40,
    v+80). G4/G7/G8 read exact pixels from this."""
    out = bytearray(w * h * 3)
    i = 0
    for y in range(h):
        for x in range(w):
            v = (x * 3 + y * 5) % 256
            out[i] = v
            out[i + 1] = (v + 40) % 256
            out[i + 2] = (v + 80) % 256
            i += 3
    return bytes(out)


def frame_nonlinear(w=W, h=H):
    """A different field (v = x*x + y*y*3 + 7) — the changed content G5
    must detect via img.diff."""
    out = bytearray(w * h * 3)
    i = 0
    for y in range(h):
        for x in range(w):
            v = (x * x + y * y * 3 + 7) % 256
            out[i] = v
            out[i + 1] = (v * 2) % 256
            out[i + 2] = 255 - v
            i += 3
    return bytes(out)


_big_cache = None


def frame_big():
    """1920x1080 with per-pixel variation (PNG deflate stays honest) —
    the full-screen throughput case for G9. Computed once."""
    global _big_cache
    if _big_cache is None:
        out = bytearray(BIG_W * BIG_H * 3)
        i = 0
        for y in range(BIG_H):
            for x in range(BIG_W):
                v = (x * 3 + y * 5 + (x * y) % 7) % 256
                out[i] = v
                out[i + 1] = (v + 40) % 256
                out[i + 2] = (v + 80) % 256
                i += 3
        _big_cache = bytes(out)
    return _big_cache


def _gray(rgb):
    out = bytearray(len(rgb) // 3)
    j = 0
    for i in range(0, len(rgb), 3):
        out[j] = (rgb[i] * 77 + rgb[i + 1] * 150 + rgb[i + 2] * 29) >> 8
        j += 1
    return bytes(out)


class ReferenceProvider:
    def __init__(self, host, port, token):
        self.host, self.port, self.token = host, port, token
        self._frame = (W, H, frame_gradient())
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def set_frame(self, w, h, rgb):
        """The screen now shows this: (width, height, RGB888 bytes)."""
        assert len(rgb) == w * h * 3
        self._frame = (w, h, rgb)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("reference provider could not connect (auth?)")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    # --- protocol (mirrors obs/obs_mycro.py) ---

    def _run(self):
        try:
            self._serve()
        except Exception as e:
            if not self._stop.is_set():
                print(f"reference_provider: link ended: {type(e).__name__}: {e}")

    def _serve(self):
        s = socket.create_connection((self.host, self.port), timeout=5)
        try:
            s.settimeout(1.0)
            digest = hashlib.sha256(self.token.encode()).digest()
            s.sendall(AUTH_MAGIC + bytes((PROTO_VERSION,)) + digest)
            self._send_env(s, "img.hello", 0, {"caps": ["frame"]})
            self.ready.set()
            sid = 0
            while not self._stop.is_set():
                (h,) = struct.unpack("<I", self._recv(s, 4))
                kind, length = h >> 31, h & 0x7FFFFFFF
                payload = self._recv(s, length)
                if kind != 0:
                    continue  # binary never flows device->provider
                env = json.loads(payload.decode("utf-8"))
                t, rid = env.get("type"), env.get("id", 0)
                p = env.get("payload", {})
                w0, h0, rgb = self._frame
                if t != "img.frame":
                    continue  # pushes (macro.result et al.) are ignored
                if "rect" in p:
                    rc = p["rect"]
                    x, y = int(rc["x"]), int(rc["y"])
                    w, h = int(rc["w"]), int(rc["h"])
                    cw, ch = min(w, w0 - max(x, 0)), min(h, h0 - max(y, 0))
                    out = bytearray(cw * ch * 3)
                    for yy in range(ch):
                        src = ((y + yy) * w0 + x) * 3
                        out[yy * cw * 3:(yy + 1) * cw * 3] = \
                            rgb[src:src + cw * 3]
                    sid = (sid + 1) & 0xFFFF
                    self._send_stream(s, rid, sid,
                                      {"width": w0, "height": h0,
                                       "crop_w": cw, "crop_h": ch,
                                       "format": "rgb8"}, bytes(out))
                elif "points" in p:
                    colors = []
                    for x, y in p["points"]:
                        i = (int(y) * W + int(x)) * 3
                        colors.append([rgb[i], rgb[i + 1], rgb[i + 2]])
                    self._send_env(s, "img.frame.ok", rid,
                                   {"width": w0, "height": h0,
                                    "format": "colors", "colors": colors})
                elif str(p.get("format", "")) == "png":
                    sid = (sid + 1) & 0xFFFF
                    png = make_png(w0, h0, rgb)
                    self._send_stream(s, rid, sid,
                                      {"width": w0, "height": h0,
                                       "format": "png"}, png)
                elif "w" in p and "h" in p:                    # native is already 80x60: only 1:1 is served
                    w, h = int(p["w"]), int(p["h"])
                    fmt = str(p.get("format", ""))
                    if (w, h) != (W, H) or fmt not in ("rgb8", "gray8"):
                        self._send_env(s, "error", rid,
                                       {"code": "field",
                                        "message": "reference is 80x60 native"})
                        continue
                    data = rgb if fmt == "rgb8" else _gray(rgb)
                    sid = (sid + 1) & 0xFFFF
                    self._send_stream(s, rid, sid,
                                      {"width": W, "height": H,
                                       "frame_w": w, "frame_h": h,
                                       "format": fmt}, data)
        finally:
            s.close()

    def _recv(self, s, n):
        buf = bytearray()
        while len(buf) < n:
            if self._stop.is_set():
                raise ConnectionError("stopping")
            try:
                chunk = s.recv(n - len(buf))
            except (socket.timeout, TimeoutError):
                continue
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return bytes(buf)

    @staticmethod
    def _send_env(s, type_, rid, payload):
        body = json.dumps({"v": PROTO_VERSION, "type": type_, "id": rid,
                           "ts": 0, "payload": payload}).encode("utf-8")
        s.sendall(struct.pack("<I", len(body)) + body)

    @staticmethod
    def _send_stream(s, rid, sid, meta, data):
        chunks = (len(data) + CHUNK_RAW - 1) // CHUNK_RAW
        meta = dict(meta, stream_id=sid, chunks=chunks)
        ReferenceProvider._send_env(s, "img.frame.ok", rid, meta)
        for i in range(chunks):
            part = data[i * CHUNK_RAW:(i + 1) * CHUNK_RAW]
            flags = FLAG_STREAM_END if i == chunks - 1 else 0
            pl = struct.pack("<HH", sid, flags) + part
            s.sendall(struct.pack("<I", 0x80000000 | len(pl)) + pl)
