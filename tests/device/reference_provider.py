"""Reference capture provider for devicetest (protocol conformance +
img.* e2e without a running OBS).

A formal reference implementation of the provider side of protocol v1
(docs/protocol.md): auth prologue, img.hello, the img.frame verb with
all three extraction selectors (rect / points / w+h+format). Pixels come
from a deterministic synthetic 80x60 frame (native resolution), shared
by construction with the G-group cases in devicetest.py:

    frame "a": v(x,y) = (x*3 + y*5) % 256 as (v, v+40, v+80)
    frame "b": nonlinear field (v = x*x + y*y*3 + 7), for diff tests

gray8 uses the integer BT.601 luma shared with src/lib/imgmatch.py and
obs/obs_mycro.py: (r*77 + g*150 + b*29) >> 8.

Threaded, blocking sockets. Not collected by pytest (not a test_* file).
"""

import hashlib
import json
import socket
import struct
import threading

PROTO_VERSION = 1
AUTH_MAGIC = b"MYCR"
CHUNK_RAW = 8192 - 4  # binary frame payload minus u16 stream_id + u16 flags
FLAG_STREAM_END = 1

W, H = 80, 60


def _pixels(which):
    out = bytearray(W * H * 3)
    for y in range(H):
        for x in range(W):
            if which == "b":
                v = (x * x + y * y * 3 + 7) % 256
                r, g, b = v, (v * 2) % 256, 255 - v
            else:
                v = (x * 3 + y * 5) % 256
                r, g, b = v, (v + 40) % 256, (v + 80) % 256
            i = (y * W + x) * 3
            out[i], out[i + 1], out[i + 2] = r, g, b
    return bytes(out)


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
        self.which = "a"
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None

    def set_frame(self, which):
        self.which = which

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
                rgb = _pixels(self.which)
                if t != "img.frame":
                    continue  # pushes (macro.result et al.) are ignored
                if "rect" in p:
                    rc = p["rect"]
                    x, y = int(rc["x"]), int(rc["y"])
                    w, h = int(rc["w"]), int(rc["h"])
                    cw, ch = min(w, W - max(x, 0)), min(h, H - max(y, 0))
                    out = bytearray(cw * ch * 3)
                    for yy in range(ch):
                        src = ((y + yy) * W + x) * 3
                        out[yy * cw * 3:(yy + 1) * cw * 3] = \
                            rgb[src:src + cw * 3]
                    sid = (sid + 1) & 0xFFFF
                    self._send_stream(s, rid, sid,
                                      {"width": W, "height": H,
                                       "crop_w": cw, "crop_h": ch,
                                       "format": "rgb8"}, bytes(out))
                elif "points" in p:
                    colors = []
                    for x, y in p["points"]:
                        i = (int(y) * W + int(x)) * 3
                        colors.append([rgb[i], rgb[i + 1], rgb[i + 2]])
                    self._send_env(s, "img.frame.ok", rid,
                                   {"width": W, "height": H,
                                    "format": "colors", "colors": colors})
                elif "w" in p and "h" in p:
                    # native is already 80x60: only 1:1 is served
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
