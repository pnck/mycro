"""Reference capture provider for devicetest (protocol conformance +
img.* e2e without a running OBS).

A formal reference implementation of the provider side of protocol v1
(docs/protocol.md): auth prologue, img.hello, the img.frame verb with
all extraction selectors (rect / points / w+h+format / format:png).

The provider's screen content is explicit frame DATA, not named modes —
a real screen changes over time, and tests set it directly:
`provider.set_frame(w, h, rgb888_bytes)`. Frame builders:
`frame_default()` (default; G4/G7/G8 read exact pixels from it),
`frame_changed()` (the change G5 must detect), `frame_fullhd()`
(1920x1080, G9's full-screen throughput case).

gray8 uses the integer BT.601 luma shared with src/lib/imgmatch.py and
obs/obs_mycro.py: (r*77 + g*150 + b*29) >> 8.

Threaded, blocking sockets. Not collected by pytest (not a test_* file).
"""

import hashlib
import hmac
import json
import socket
import struct
import threading
import time
import urllib.parse
import zlib

PROTO_VERSION = 1
AUTH_MAGIC = b"MYCR"
CHUNK_RAW = 8192 - 4  # binary frame payload minus u16 stream_id + u16 flags
MAX_POINTS = 256
MAX_RECT_EDGE = 128
IMG_STREAM_MAX = 48 * 1024

W, H = 80, 60
BIG_W, BIG_H = 1920, 1080


def frame_default(w=W, h=H):
    """The predictable default screen. The formula is arbitrary — what
    matters is position-dependent + deterministic (a solid color would
    hide coordinate-mapping bugs). G4/G7/G8 read exact pixels here."""
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


def frame_changed(w=W, h=H):
    """The screen after content changed (v = x*x + y*y*3 + 7) — what G5
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


def frame_fullhd():
    """A 1920x1080 screen with per-pixel variation (PNG deflate stays
    honest) — G9's full-screen throughput case. Computed once."""
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


def make_png(w, h, rgb):
    """Minimal valid PNG (8-bit RGB, filter-0 rows) — stdlib only."""

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _gray(rgb):
    out = bytearray(len(rgb) // 3)
    j = 0
    for i in range(0, len(rgb), 3):
        out[j] = (rgb[i] * 77 + rgb[i + 1] * 150 + rgb[i + 2] * 29) >> 8
        j += 1
    return bytes(out)


def _resample(rgb, source_w, source_h, w, h):
    """Deterministic nearest-neighbor whole-source resampling."""
    if (source_w, source_h) == (w, h):
        return rgb
    out = bytearray(w * h * 3)
    dst = 0
    for y in range(h):
        source_y = y * source_h // h
        for x in range(w):
            source_x = x * source_w // w
            src = (source_y * source_w + source_x) * 3
            out[dst:dst + 3] = rgb[src:src + 3]
            dst += 3
    return bytes(out)


class ReferenceProvider:
    def __init__(self, host, port, token):
        self.host, self.port, self.token = host, port, token
        self._frame = (W, H, frame_default())
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._watches = {}  # name -> threading.Event

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
        for w_stop in list(self._watches.values()):
            w_stop.set()
        self._watches.clear()
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
            self._send_env(s, "img.hello", 0, {"caps": ["frame", "direct_preview"]})
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

                if t == "img.arm_preview":
                    nonce = str(p.get("nonce", ""))
                    expected_sig = hmac.new(
                        (self.token or "").encode("utf-8"),
                        nonce.encode("utf-8"),
                        hashlib.sha256
                    ).hexdigest()
                    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    lsock.bind(("0.0.0.0", 0))
                    port = lsock.getsockname()[1]
                    lsock.listen(1)
                    lsock.settimeout(5.0)

                    threading.Thread(
                        target=self._preview_worker,
                        args=(lsock, w0, h0, rgb, expected_sig, nonce),
                        daemon=True
                    ).start()
                    self._send_env(s, "img.arm_preview.ok", rid, {
                        "port": port,
                        "width": w0,
                        "height": h0,
                    })
                    continue

                if t == "img.watch":
                    name = p.get("name")
                    try:
                        x, y = int(p.get("x")), int(p.get("y"))
                        interval_ms = int(p.get("interval_ms"))
                        duration_ms = int(p.get("duration_ms"))
                    except (TypeError, ValueError):
                        self._send_env(s, "error", rid,
                                       {"code": "field", "message": "invalid numeric arguments"})
                        continue
                    if not isinstance(name, str) or not name or interval_ms <= 0 or duration_ms <= 0:
                        self._send_env(s, "error", rid,
                                       {"code": "field", "message": "invalid watch parameters"})
                        continue
                    if x < 0 or y < 0 or x >= w0 or y >= h0:
                        self._send_env(s, "error", rid,
                                       {"code": "out_of_bounds", "message": "point outside frame bounds"})
                        continue
                    prev_stop = self._watches.pop(name, None)
                    if prev_stop:
                        prev_stop.set()
                    effective_interval = max(interval_ms, 20)
                    watch_stop = threading.Event()
                    self._watches[name] = watch_stop

                    def _watch_loop(w_name, wx, wy, w_interval, w_duration, w_stop):
                        deadline = time.monotonic() + w_duration / 1000.0
                        period_s = w_interval / 1000.0
                        while not self._stop.is_set() and not w_stop.is_set():
                            if time.monotonic() >= deadline:
                                break
                            w_w0, w_h0, w_rgb = self._frame
                            if 0 <= wx < w_w0 and 0 <= wy < w_h0:
                                idx = (wy * w_w0 + wx) * 3
                                val = (w_rgb[idx] << 16) | (w_rgb[idx + 1] << 8) | w_rgb[idx + 2]
                                try:
                                    self._send_env(s, "event.push", 0, {
                                        "name": "img." + w_name,
                                        "fields": {"val": val},
                                    })
                                except Exception:
                                    break
                            if w_stop.wait(period_s):
                                break
                        self._watches.pop(w_name, None)

                    self._send_env(s, "img.watch.ok", rid, {"interval_ms": effective_interval})
                    threading.Thread(
                        target=_watch_loop,
                        args=(name, x, y, effective_interval, duration_ms, watch_stop),
                        daemon=True
                    ).start()
                    continue

                if t == "img.unwatch":
                    name = p.get("name")
                    watch_stop = self._watches.pop(name, None)
                    if watch_stop:
                        watch_stop.set()
                    self._send_env(s, "img.unwatch.ok", rid, {})
                    continue

                if t != "img.frame":
                    continue  # pushes (macro.result et al.) are ignored
                try:
                    if "rect" in p:
                        rc = p["rect"]
                        x, y = int(rc["x"]), int(rc["y"])
                        w, h = int(rc["w"]), int(rc["h"])
                        if not (1 <= w <= MAX_RECT_EDGE
                                and 1 <= h <= MAX_RECT_EDGE
                                and w * h * 3 <= IMG_STREAM_MAX):
                            self._send_env(s, "error", rid,
                                           {"code": "field",
                                            "message": "rect exceeds the snapshot budget"})
                            continue
                        if x < 0 or y < 0 or x + w > w0 or y + h > h0:
                            self._send_env(s, "error", rid,
                                           {"code": "out_of_bounds",
                                            "message": "crop rect outside frame bounds"})
                            continue
                        out = bytearray(w * h * 3)
                        for yy in range(h):
                            src = ((y + yy) * w0 + x) * 3
                            out[yy * w * 3:(yy + 1) * w * 3] = \
                                rgb[src:src + w * 3]
                        sid = (sid + 1) & 0xFFFF
                        self._send_stream(s, rid, sid,
                                          {"width": w0, "height": h0,
                                           "w": w, "h": h,
                                           "format": "rgb8"}, bytes(out))
                    elif "points" in p:
                        points = p["points"]
                        if not (1 <= len(points) <= MAX_POINTS):
                            self._send_env(s, "error", rid,
                                           {"code": "field",
                                            "message": "points capped at %d" % MAX_POINTS})
                            continue
                        colors = []
                        for x, y in points:
                            nx, ny = int(x), int(y)
                            if not (0 <= nx < w0 and 0 <= ny < h0):
                                self._send_env(s, "error", rid,
                                               {"code": "out_of_bounds",
                                                "message": "point outside frame bounds"})
                                break
                            i = (ny * w0 + nx) * 3
                            colors.append((rgb[i] << 16) | (rgb[i + 1] << 8)
                                          | rgb[i + 2])
                        if len(colors) != len(points):
                            continue
                        self._send_env(s, "img.frame.ok", rid,
                                       {"width": w0, "height": h0,
                                        "format": "colors", "colors": colors})
                    elif "w" in p and "h" in p:
                        w, h = int(p["w"]), int(p["h"])
                        fmt = str(p.get("format", ""))
                        if (fmt not in ("rgb8", "gray8") or w < 1 or h < 1
                                or w * h * (3 if fmt == "rgb8" else 1) > IMG_STREAM_MAX):
                            self._send_env(s, "error", rid,
                                           {"code": "field",
                                            "message": "invalid resample dimensions or format"})
                            continue
                        data = _resample(rgb, w0, h0, w, h)
                        if fmt == "gray8":
                            data = _gray(data)
                        sid = (sid + 1) & 0xFFFF
                        self._send_stream(s, rid, sid,
                                          {"width": w0, "height": h0,
                                           "w": w, "h": h,
                                           "format": fmt}, data)
                    else:
                        raise ValueError("missing frame selector")
                except (KeyError, TypeError, ValueError) as e:
                    self._send_env(s, "error", rid,
                                   {"code": "field", "message": str(e)})
        finally:
            s.close()

    def _preview_worker(self, s_listen, w_frame, h_frame, rgb_frame, exp_sig, exp_nonce):
        deadline = time.monotonic() + 5.0
        try:
            while time.monotonic() < deadline:
                s_listen.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    conn, _ = s_listen.accept()
                except (socket.timeout, OSError):
                    break
                try:
                    conn.settimeout(5.0)
                    req_line = conn.recv(1024).decode("utf-8", "replace").split("\r\n")[0]
                    parts = req_line.split(" ")
                    method = parts[0].upper() if len(parts) > 0 else ""
                    path = parts[1] if len(parts) > 1 else ""
                    parsed = urllib.parse.urlparse(path)
                    qs = urllib.parse.parse_qs(parsed.query)

                    if method == "OPTIONS":
                        conn.sendall(
                            b"HTTP/1.1 204 No Content\r\n"
                            b"Access-Control-Allow-Origin: *\r\n"
                            b"Access-Control-Allow-Private-Network: true\r\n"
                            b"Access-Control-Allow-Methods: GET, OPTIONS\r\n"
                            b"Access-Control-Allow-Headers: *\r\n"
                            b"Connection: close\r\n\r\n"
                        )
                        continue

                    req_nonce = qs.get("nonce", [""])[0]
                    req_sig = qs.get("sig", [""])[0]
                    req_type = qs.get("type", [""])[0]
                    auth_ok = hmac.compare_digest(req_sig, exp_sig)
                    if not (parsed.path == "/capture" and req_type == "png" and req_nonce == exp_nonce and auth_ok):
                        conn.sendall(
                            b"HTTP/1.1 403 Forbidden\r\n"
                            b"Content-Length: 9\r\n"
                            b"Access-Control-Allow-Origin: *\r\n"
                            b"Access-Control-Allow-Private-Network: true\r\n"
                            b"Connection: close\r\n\r\n"
                            b"Forbidden"
                        )
                        break
                    png = make_png(w_frame, h_frame, rgb_frame)
                    header = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: image/png\r\n"
                        b"Content-Length: " + str(len(png)).encode("ascii") + b"\r\n"
                        b"Access-Control-Allow-Origin: *\r\n"
                        b"Access-Control-Allow-Private-Network: true\r\n"
                        b"Access-Control-Allow-Methods: GET, OPTIONS\r\n"
                        b"Cache-Control: no-store\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    conn.sendall(header + png)
                    break
                finally:
                    conn.close()
        except Exception:
            pass
        finally:
            s_listen.close()

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

    def _send_env(self, s, type_, rid, payload):
        body = json.dumps({"v": PROTO_VERSION, "type": type_, "id": rid,
                           "ts": 0, "payload": payload}).encode("utf-8")
        wire = struct.pack("<I", len(body)) + body
        lock = getattr(self, "_lock", None)
        if lock is not None:
            with lock:
                s.sendall(wire)
        else:
            s.sendall(wire)

    def _send_stream(self, s, rid, sid, meta, data):
        chunks = (len(data) + CHUNK_RAW - 1) // CHUNK_RAW
        meta = dict(meta, stream_id=sid, chunks=chunks,
                    bytes=len(data))
        self._send_env(s, "img.frame.ok", rid, meta)
        lock = getattr(self, "_lock", None)
        for i in range(chunks):
            part = data[i * CHUNK_RAW:(i + 1) * CHUNK_RAW]
            pl = struct.pack("<HH", sid, 0) + part
            wire = struct.pack("<I", 0x80000000 | len(pl)) + pl
            if lock is not None:
                with lock:
                    s.sendall(wire)
            else:
                s.sendall(wire)
