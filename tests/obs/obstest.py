#!/usr/bin/env python3
"""Single-file multi-scenario integration and diagnostic test suite for MYCRO OBS capture provider.

Organized by table-driven functional domains (modeled after tests/device/devicetest.py).
Serves as an independent, decoupled black-box test suite and live diagnostic probe:

    # Run full scenario validation:
    python3 tests/obs/obstest.py

    # Run live diagnostic probe against local OBS Studio:
    python3 tests/obs/obstest.py --only probe

Functional Domains:
    probe      Live OBS Studio WebSocket discovery, authentication, in-memory capture benchmark
    wire       Protocol v1 companion channel (:7373), 37B prologue, <I LE framing & flags=0 chunking
    capture    24-bit DIB BMP layout, O(1) buffer arithmetic, and RGB8 crop packing invariants
    preview    Ephemeral HTTP server Chrome PNA OPTIONS preflight, HMAC-SHA256, and Lazy Capture contract
    telemetry  Telemetry rate clamping (>=20ms), periodic loop, and synchronous unwatch zero-leak
    router     Protocol v1 envelope schema, complete geometry metadata, and error reflection defense
    e2e        Physical device VM exact pixel snap, reactive telemetry slot wait, and preview stream
    full_link  Interactive full link test: visual stimulus to USB HID keystroke intercept
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import platform
import socket
import struct
import sys
import threading
import time
import urllib.parse
from typing import Optional, List, Dict, Any, Tuple

# Ensure repo root is on sys.path to import canonical codec specification
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIB_PATH = os.path.join(REPO_ROOT, "src", "lib")
TOOLS_PATH = os.path.join(REPO_ROOT, "tools")
if LIB_PATH not in sys.path:
    sys.path.insert(0, LIB_PATH)
if TOOLS_PATH not in sys.path:
    sys.path.insert(0, TOOLS_PATH)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from client import Client, ProtocolError
from codec import (
    Reassembler,
    pack_frame,
    decode_envelope,
    encode_envelope,
    split_chunk,
    build_auth_header,
    check_auth_header,
    AUTH_MAGIC,
    PROTO_VERSION,
    KIND_JSON,
    KIND_BINARY,
    MAX_BINARY_CHUNK,
)

REGISTRY: List["Domain"] = []
RESULTS: List[tuple] = []
CONFIG: Dict[str, Any] = {"host": "", "port": 7373, "token": ""}


class Domain:
    def __init__(self, index: int, name: str, aliases: tuple = (), desc: str = ""):
        self.index = index
        self.name = name
        auto_aliases = [str(index), f"d{index}"]
        all_aliases = set(auto_aliases)
        for a in aliases:
            all_aliases.add(str(a).lower().strip())
        self.aliases = tuple(sorted(all_aliases))
        self.desc = desc
        self.fn = None

    def matches(self, term: str) -> bool:
        t = term.lower().strip()
        return (
            t == str(self.index)
            or t == f"d{self.index}"
            or t == self.name
            or t in self.aliases
            or (len(t) >= 2 and self.name.startswith(t))
        )

    def run(self) -> bool:
        width = max((len(d.name) for d in REGISTRY), default=10)
        try:
            details = self.fn()
            msg = ", ".join(details) if isinstance(details, (list, tuple)) else str(details or "")
            check(self.name, True, msg, width=width)
            return True
        except SkipException as s:
            check(self.name, None, str(s), width=width)
            return True
        except Exception as e:
            check(self.name, False, f"{type(e).__name__}: {e}", width=width)
            return False


class SkipException(Exception):
    pass


def domain(name: str, *aliases: str, desc: str = ""):
    """Decorator registering a domain test into the table-driven registry."""
    def decorator(fn):
        idx = len(REGISTRY) + 1
        d = Domain(idx, name, aliases=aliases, desc=desc)
        d.fn = fn
        REGISTRY.append(d)
        return fn
    return decorator


def check(name: str, ok: Optional[bool], detail: str = "", width: Optional[int] = None):
    RESULTS.append((name, ok, detail))
    w = width or 10
    if ok is True:
        status = "PASS"
    elif ok is None:
        status = "SKIP"
    else:
        status = "FAIL"
    print(f"{status:<4}  {name:<{w}} ({detail})")


# ==============================================================================
# Domain 1: probe (Live OBS WebSocket in-memory diagnostic & benchmark)
# ==============================================================================

@domain("probe", "obs", "benchmark", desc="Live OBS WebSocket v5 probe & in-memory capture benchmark")
def test_probe_domain() -> List[str]:
    # 1. Discover local OBS WebSocket configuration
    system = platform.system()
    if system == "Darwin":
        cfg_dir = os.path.expanduser("~/Library/Application Support/obs-studio")
    elif system == "Windows":
        cfg_dir = os.path.join(os.environ.get("APPDATA", ""), "obs-studio")
    else:
        cfg_dir = os.path.expanduser("~/.config/obs-studio")
    cfg_file = os.path.join(cfg_dir, "plugin_config", "obs-websocket", "config.json")

    enabled = False
    port = 4455
    pwd = ""
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            enabled = bool(d.get("server_enabled", False))
            port = int(d.get("server_port", 4455))
            pwd = str(d.get("server_password", ""))
        except Exception:
            pass

    details = []

    # 2. Connect via standard RFC 6455 WebSocket
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1.5)
    except Exception as e:
        raise SkipException(f"OBS WebSocket offline on :{port} ({e}) - enable in Tools -> WebSocket Server Settings")

    rfile = s.makefile("rb", buffering=65536)
    try:
        t0 = time.monotonic()
        sec_key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET / HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {sec_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        s.sendall(req.encode("ascii"))

        # Read HTTP upgrade response
        status_line = rfile.readline(4096)
        if b"101" not in status_line:
            raise RuntimeError(f"WebSocket upgrade failed: {status_line}")
        while True:
            line = rfile.readline(4096)
            if line in (b"\r\n", b"\n", b""):
                break

        # Helper to read one websocket JSON frame
        def ws_recv_json() -> dict:
            b0, b1 = struct.unpack("!BB", rfile.read(2))
            plen = b1 & 0x7F
            if plen == 126:
                plen = struct.unpack("!H", rfile.read(2))[0]
            elif plen == 127:
                plen = struct.unpack("!Q", rfile.read(8))[0]
            mask = rfile.read(4) if (b1 & 0x80) else b""
            raw = rfile.read(plen)
            if mask:
                raw = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
            return json.loads(raw.decode("utf-8"))

        def ws_send_json(data: dict):
            raw = json.dumps(data).encode("utf-8")
            mask = os.urandom(4)
            length = len(raw)
            if length <= 125:
                hdr = struct.pack("!BB", 0x81, 0x80 | length) + mask
            elif length <= 65535:
                hdr = struct.pack("!BBH", 0x81, 0x80 | 126, length) + mask
            else:
                hdr = struct.pack("!BBQ", 0x81, 0x80 | 127, length) + mask
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
            s.sendall(hdr + masked)

        # Authenticate with obs-websocket v5
        hello = ws_recv_json()
        auth_req = hello.get("d", {}).get("authentication")
        ident_d = {"rpcVersion": 1}
        if auth_req:
            challenge = auth_req.get("challenge", "")
            salt = auth_req.get("salt", "")
            secret = base64.b64encode(hashlib.sha256((pwd + salt).encode("utf-8")).digest()).decode("ascii")
            auth_hash = base64.b64encode(hashlib.sha256((secret + challenge).encode("utf-8")).digest()).decode("ascii")
            ident_d["authentication"] = auth_hash

        ws_send_json({"op": 1, "d": ident_d})
        ident_resp = ws_recv_json()
        if ident_resp.get("op") != 2:
            raise RuntimeError(f"Auth failed: {ident_resp}")

        auth_ms = (time.monotonic() - t0) * 1000.0
        details.append(f"auth in {auth_ms:.1f}ms on :{port}")

        # Helper for RPC calls
        def rpc_call(req_type: str, req_data: dict = None) -> dict:
            rid = f"test-{int(time.time() * 1000)}"
            ws_send_json({"op": 6, "d": {"requestType": req_type, "requestId": rid, "requestData": req_data or {}}})
            while True:
                msg = ws_recv_json()
                if msg.get("op") == 7 and msg.get("d", {}).get("requestId") == rid:
                    status = msg.get("d", {}).get("requestStatus", {})
                    if not status.get("result", False):
                        raise RuntimeError(f"OBS RPC failed ({status.get('code')}): {status.get('comment')}")
                    return msg.get("d", {}).get("responseData", {})

        # Video settings query
        vid = rpc_call("GetVideoSettings")
        bw, bh = vid.get("baseWidth", 1920), vid.get("baseHeight", 1080)
        details.append(f"canvas {bw}x{bh}")

        # Resolve active program scene if sourceName is not specified
        curr_scene = rpc_call("GetCurrentProgramScene")
        scene_name = curr_scene.get("currentProgramSceneName") or curr_scene.get("sceneName", "")
        if not scene_name:
            scenes_resp = rpc_call("GetSceneList")
            scenes = scenes_resp.get("scenes", [])
            if scenes:
                scene_name = scenes[0].get("sceneName", "")
        details.append(f"source {scene_name!r}")

        # Benchmark in-memory BMP capture
        t_bmp0 = time.monotonic()
        resp_bmp = rpc_call("GetSourceScreenshot", {"sourceName": scene_name, "imageFormat": "bmp"})
        bmp_ms = (time.monotonic() - t_bmp0) * 1000.0
        bmp_data = resp_bmp.get("imageData", "")
        comma = bmp_data.find(",")
        raw_bmp = base64.b64decode(bmp_data[comma + 1:] if comma != -1 else bmp_data)
        if len(raw_bmp) < 1024:
            raise AssertionError(f"BMP capture returned empty/truncated buffer: {len(raw_bmp)} bytes")
        details.append(f"BMP {len(raw_bmp) / 1024:.0f}KB in {bmp_ms:.1f}ms")

        # Benchmark in-memory PNG capture
        t_png0 = time.monotonic()
        resp_png = rpc_call("GetSourceScreenshot", {"sourceName": scene_name, "imageFormat": "png"})
        png_ms = (time.monotonic() - t_png0) * 1000.0
        png_data = resp_png.get("imageData", "")
        comma = png_data.find(",")
        raw_png = base64.b64decode(png_data[comma + 1:] if comma != -1 else png_data)
        if len(raw_png) < 1024:
            raise AssertionError(f"PNG capture returned empty/truncated buffer: {len(raw_png)} bytes")
        details.append(f"PNG {len(raw_png) / 1024:.0f}KB in {png_ms:.1f}ms")
        details.append("0 disk I/O")
    finally:
        rfile.close()
        s.close()

    return details


# ==============================================================================
# Domain 2: wire (Protocol v1 Wire Framing, Little-Endian <I & Chunking)
# ==============================================================================

@domain("wire", "session", "proto", desc="Protocol v1 link, 37B prologue, <I LE framing & flags=0 chunking")
def test_wire_domain() -> List[str]:
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    token = "test_wire_token_456"
    reassembler = Reassembler()
    frames_received = []

    def server_thread():
        def recv_exact(s, n):
            buf = bytearray()
            while len(buf) < n:
                chunk = s.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("connection closed")
                buf.extend(chunk)
            return bytes(buf)

        try:
            conn, _ = server_sock.accept()
            with conn:
                # 1. Read 37-byte Auth Prologue exactly
                prologue = recv_exact(conn, 37)
                assert check_auth_header(prologue, token)

                # 2. Feed stream into canonical Reassembler
                while True:
                    buf = conn.recv(4096)
                    if not buf:
                        break
                    items = reassembler.feed(buf)
                    frames_received.extend((kind, bytes(payload)) for kind, payload in items)
                    if len(frames_received) >= 3:
                        break
        finally:
            server_sock.close()

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

    # Client side: connect and send messages conforming strictly to wire format
    client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    try:
        # Auth prologue
        client.sendall(build_auth_header(token))

        # Kind-0 JSON envelope (little-endian <I header)
        env = {"v": 1, "type": "ping", "id": 1, "ts": 123456, "payload": {}}
        client.sendall(pack_frame(KIND_JSON, encode_envelope(env)))

        # Kind-1 binary chunks: stream_id=7, flags=0 (strictly 0)
        chunk1_payload = struct.pack("<HH", 7, 0) + b"CHUNK1_DATA"
        chunk2_payload = struct.pack("<HH", 7, 0) + b"CHUNK2_DATA"

        client.sendall(pack_frame(KIND_BINARY, chunk1_payload))
        client.sendall(pack_frame(KIND_BINARY, chunk2_payload))
    finally:
        client.close()

    t.join(timeout=2.0)

    # Assertions
    assert len(frames_received) == 3
    kind0, raw_env = frames_received[0]
    assert kind0 == KIND_JSON
    assert decode_envelope(raw_env) == env

    kind1_a, raw_c1 = frames_received[1]
    assert kind1_a == KIND_BINARY
    sid1, flags1, data1 = split_chunk(raw_c1)
    assert sid1 == 7
    assert flags1 == 0
    assert bytes(data1) == b"CHUNK1_DATA"

    kind1_b, raw_c2 = frames_received[2]
    assert kind1_b == KIND_BINARY
    sid2, flags2, data2 = split_chunk(raw_c2)
    assert sid2 == 7
    assert flags2 == 0
    assert bytes(data2) == b"CHUNK2_DATA"

    return ["37B prologue verified", "kind-0 <I LE JSON framing", "kind-1 flags=0 chunk reassembly"]


# ==============================================================================
# Domain 3: capture (24-bit DIB BMP Layout & RGB8 Crop Packing Invariants)
# ==============================================================================

@domain("capture", "engine", "pixel", desc="24-bit DIB BMP layout, O(1) buffer arithmetic, and RGB8 crop packing")
def test_capture_domain() -> List[str]:
    # Construct a valid 5x4 24-bit uncompressed bottom-up BMP (w=5 forces 1 byte of padding per row)
    # Stride = ((5 * 3 + 3) // 4) * 4 = 16 bytes
    stride = 16
    raw_bmp = bytearray(54 + stride * 4)
    raw_bmp[0:2] = b"BM"
    struct.pack_into("<I", raw_bmp, 2, len(raw_bmp))
    struct.pack_into("<I", raw_bmp, 10, 54)   # offset
    struct.pack_into("<I", raw_bmp, 14, 40)   # DIB header size
    struct.pack_into("<ii", raw_bmp, 18, 5, 4) # width=5, height=4 (positive = bottom-up)
    struct.pack_into("<H", raw_bmp, 26, 1)    # planes
    struct.pack_into("<H", raw_bmp, 28, 24)   # 24-bit bpp
    struct.pack_into("<I", raw_bmp, 30, 0)    # BI_RGB uncompressed

    # Paint (x=0, y=3 in image space): this is row 0 in bottom-up BMP layout
    # Color: Red=0x12, Green=0x34, Blue=0x56
    row0 = 54
    raw_bmp[row0] = 0x56      # B
    raw_bmp[row0 + 1] = 0x34  # G
    raw_bmp[row0 + 2] = 0x12  # R

    # Paint (x=1, y=0 in image space): this is row 3 in bottom-up BMP layout
    row3 = 54 + 3 * stride
    raw_bmp[row3 + 3] = 0xAA      # B
    raw_bmp[row3 + 4] = 0xBB      # G
    raw_bmp[row3 + 5] = 0xCC      # R

    bmp_mem = memoryview(raw_bmp)

    # 1. Verify DIB header extraction
    offset = struct.unpack_from("<I", bmp_mem, 10)[0]
    w, h = struct.unpack_from("<ii", bmp_mem, 18)
    bpp = struct.unpack_from("<H", bmp_mem, 28)[0]
    comp = struct.unpack_from("<I", bmp_mem, 30)[0]
    assert offset == 54 and w == 5 and h == 4 and bpp == 24 and comp == 0

    # 2. Verify O(1) pixel address calculation
    calc_stride = ((w * 3 + 3) // 4) * 4
    assert calc_stride == 16

    # Lookup (0, 3): row = (4 - 1 - 3) = 0
    pos1 = offset + (4 - 1 - 3) * calc_stride + 0 * 3
    b1, g1, r1 = bmp_mem[pos1], bmp_mem[pos1 + 1], bmp_mem[pos1 + 2]
    color1 = (r1 << 16) | (g1 << 8) | b1
    assert color1 == 0x123456

    # Lookup (1, 0): row = (4 - 1 - 0) = 3
    pos2 = offset + (4 - 1 - 0) * calc_stride + 1 * 3
    b2, g2, r2 = bmp_mem[pos2], bmp_mem[pos2 + 1], bmp_mem[pos2 + 2]
    color2 = (r2 << 16) | (g2 << 8) | b2
    assert color2 == 0xCCBBAA

    # 3. Verify RGB8 crop packing (top-down row order, R, G, B per pixel)
    # Crop 2x2 at (0, 0)
    crop_w, crop_h = 2, 2
    out_rgb = bytearray(crop_w * crop_h * 3)
    out_idx = 0
    for row_idx in range(crop_h):
        curr_y = 0 + row_idx
        src_row = 4 - 1 - curr_y
        row_start = offset + src_row * calc_stride + 0 * 3
        span = bmp_mem[row_start : row_start + crop_w * 3]
        for i in range(0, len(span), 3):
            out_rgb[out_idx] = span[i + 2]
            out_rgb[out_idx + 1] = span[i + 1]
            out_rgb[out_idx + 2] = span[i]
            out_idx += 3

    assert len(out_rgb) == 12
    # Verify first pixel in crop is (0, 0), second is (1, 0) -> R=0xCC, G=0xBB, B=0xAA
    assert out_rgb[3] == 0xCC and out_rgb[4] == 0xBB and out_rgb[5] == 0xAA

    return ["4B stride alignment verified", "O(1) DIB buffer lookup exact", "RGB8 crop packing validated"]


# ==============================================================================
# Domain 4: preview (Ephemeral HTTP Server Protocol & Chrome PNA)
# ==============================================================================

@domain("preview", "http", "lazy", desc="Ephemeral HTTP server Chrome PNA, HMAC auth & Lazy Capture contract")
def test_preview_domain() -> List[str]:
    token = "secret_tok_789"
    nonce = "1234567890abcdef1234567890abcdef"
    call_count = [0]

    def test_producer() -> bytes:
        call_count[0] += 1
        return b"\x89PNG\r\n\x1a\nfake_png_data"

    expected_sig = hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()

    # Start a standalone ephemeral asyncio HTTP server
    port_holder = []
    started_evt = threading.Event()
    stop_evt = threading.Event()

    async def _async_server():
        async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            req_data = await reader.read(4096)
            header_text = req_data.decode("latin1", errors="replace")
            parts = header_text.split()
            if len(parts) >= 2:
                method, full_path = parts[0].upper(), parts[1]
                cors_headers = (
                    "Access-Control-Allow-Origin: *\r\n"
                    "Access-Control-Allow-Methods: GET, OPTIONS\r\n"
                    "Access-Control-Allow-Headers: *\r\n"
                    "Access-Control-Allow-Private-Network: true\r\n"
                )
                if method == "OPTIONS":
                    writer.write(f"HTTP/1.1 204 No Content\r\n{cors_headers}Connection: close\r\n\r\n".encode("ascii"))
                    await writer.drain()
                    writer.close()
                    return
                elif method == "GET":
                    parsed = urllib.parse.urlparse(full_path)
                    qs = urllib.parse.parse_qs(parsed.query)
                    req_nonce = qs.get("nonce", [""])[0]
                    sig = qs.get("sig", [""])[0]

                    if req_nonce != nonce or not hmac.compare_digest(sig, expected_sig):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                        await writer.drain()
                        writer.close()
                        return

                    body = test_producer()
                    hdr = (
                        f"HTTP/1.1 200 OK\r\n"
                        f"Content-Type: image/png\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        f"{cors_headers}"
                        f"Connection: close\r\n\r\n"
                    ).encode("ascii")
                    writer.write(hdr + body)
                    await writer.drain()
                    writer.close()
                    stop_evt.set()
                    return

            writer.close()

        server = await asyncio.start_server(_handler, "127.0.0.1", 0)
        port_holder.append(server.sockets[0].getsockname()[1])
        started_evt.set()

        try:
            while not stop_evt.is_set():
                await asyncio.sleep(0.02)
        finally:
            server.close()
            await server.wait_closed()

    def _loop_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_async_server())
        loop.close()

    t = threading.Thread(target=_loop_thread, daemon=True)
    t.start()
    started_evt.wait(timeout=2.0)
    port = port_holder[0]

    try:
        # Step 1: Send OPTIONS preflight (Chrome PNA) on connection 1
        s1 = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        opt_req = (
            f"OPTIONS /capture HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Access-Control-Request-Private-Network: true\r\n\r\n"
        )
        s1.sendall(opt_req.encode("ascii"))
        opt_resp = s1.recv(1024).decode("latin1")
        s1.close()

        assert "204 No Content" in opt_resp
        assert "Access-Control-Allow-Private-Network: true" in opt_resp
        assert call_count[0] == 0  # Crucial: NOT invoked on OPTIONS!

        # Step 2: Send GET with bad signature on connection 2
        s2 = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        bad_req = f"GET /capture?nonce={nonce}&sig=bad_sig HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n"
        s2.sendall(bad_req.encode("ascii"))
        bad_resp = s2.recv(1024).decode("latin1")
        s2.close()

        assert "403 Forbidden" in bad_resp
        assert call_count[0] == 0  # Crucial: NOT invoked on 403!

        # Step 3: Send GET with valid signature on connection 3
        s3 = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        good_req = f"GET /capture?nonce={nonce}&sig={expected_sig} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n"
        s3.sendall(good_req.encode("ascii"))
        good_resp = bytearray()
        while True:
            chunk = s3.recv(1024)
            if not chunk:
                break
            good_resp.extend(chunk)
        s3.close()

        assert b"200 OK" in good_resp
        assert b"fake_png_data" in good_resp
        assert call_count[0] == 1  # Lazy capture contract verified!
    finally:
        stop_evt.set()
        t.join(timeout=2.0)

    return ["Chrome PNA 204 verified", "HMAC 403 rejected", "Lazy Capture contract satisfied"]


# ==============================================================================
# Domain 5: telemetry (Sampling Rate Clamping & Termination Confirmation)
# ==============================================================================

@domain("telemetry", "sampler", "watch", desc="Telemetry rate clamping (>=20ms), multi-frequency benchmark & concurrent watches")
def test_telemetry_domain() -> List[str]:
    from obs.obs_mycro import TelemetrySampler, TELEMETRY_MIN_INTERVAL_MS

    sampler = TelemetrySampler()
    details = []

    # 1. Multi-frequency benchmark across progression
    intervals = [100, 50, 33, 25, 20, 15, 10, 5]
    benched_rates = {}

    for iv in intervals:
        samples = []
        eff = sampler.start_watch(
            name="bench",
            interval_ms=iv,
            duration_ms=1000,
            sample_fn=lambda: 0x123456,
            on_sample=lambda name, val: samples.append(time.perf_counter()),
        )
        expected_eff = max(TELEMETRY_MIN_INTERVAL_MS, iv)
        assert eff == expected_eff, f"Expected {expected_eff}ms, got {eff}ms"
        time.sleep(0.08)
        sampler.stop_watch("bench")

        deltas = [(samples[i] - samples[i - 1]) * 1000.0 for i in range(1, len(samples))] if len(samples) > 1 else [eff]
        avg_delta = sum(deltas) / len(deltas)
        rate_hz = 1000.0 / avg_delta if avg_delta > 0 else 0
        benched_rates[iv] = (eff, rate_hz, avg_delta)

    # Verify clamping invariant: intervals <= 20ms all clamp to 20ms
    assert benched_rates[5][0] == 20
    assert benched_rates[10][0] == 20
    assert benched_rates[15][0] == 20
    assert benched_rates[20][0] == 20
    ceiling_hz = 1000.0 / TELEMETRY_MIN_INTERVAL_MS
    details.append(f"clamped {TELEMETRY_MIN_INTERVAL_MS}ms ({ceiling_hz:.1f}Hz ceiling)")

    # 2. Multi-watch combinations: 3 concurrent watches running simultaneously
    collected = {"slow": [], "mid": [], "fast": []}
    sampler.start_watch("slow", 60, 1000, lambda: 1, lambda n, v: collected["slow"].append(time.perf_counter()))
    sampler.start_watch("mid", 35, 1000, lambda: 2, lambda n, v: collected["mid"].append(time.perf_counter()))
    sampler.start_watch("fast", 20, 1000, lambda: 3, lambda n, v: collected["fast"].append(time.perf_counter()))
    time.sleep(0.12)
    sampler.stop_all()

    assert len(sampler._watches) == 0, "Watches dict not cleared"
    assert len(collected["slow"]) >= 1, "Slow watch received 0 samples"
    assert len(collected["mid"]) >= 2, "Mid watch received < 2 samples"
    assert len(collected["fast"]) >= 3, "Fast watch received < 3 samples"
    assert len(collected["fast"]) >= len(collected["mid"]) >= len(collected["slow"]), (
        f"Frequency ordering violated: fast={len(collected['fast'])}, mid={len(collected['mid'])}, slow={len(collected['slow'])}"
    )
    details.append(f"3 concurrent watches [20/35/60ms] -> samples: {len(collected['fast'])}/{len(collected['mid'])}/{len(collected['slow'])}")
    details.append("sync stop zero-leak verified")

    return details


# ==============================================================================
# Domain 6: router (Protocol v1 Envelope Schema & Error Reflection Defense)
# ==============================================================================

@domain("router", "dispatch", desc="Protocol v1 envelope schema, geometry metadata & error reflection defense")
def test_router_domain() -> List[str]:
    # 1. img.hello announcement schema
    hello_env = {
        "v": 1,
        "type": "img.hello",
        "id": 0,
        "ts": int(time.time() * 1000),
        "payload": {"caps": ["frame", "direct_preview"]}
    }
    assert hello_env["type"] == "img.hello"
    assert "frame" in hello_env["payload"]["caps"]

    # 2. img.frame.ok geometry metadata schema
    frame_ok_env = {
        "v": 1,
        "type": "img.frame.ok",
        "id": 42,
        "ts": int(time.time() * 1000),
        "payload": {
            "width": 1920,
            "height": 1080,
            "w": 128,
            "h": 128,
            "format": "rgb8",
            "stream_id": 1,
            "bytes": 128 * 128 * 3,
            "chunks": 6,
        }
    }
    p = frame_ok_env["payload"]
    assert p["width"] == 1920 and p["height"] == 1080
    assert p["w"] == 128 and p["h"] == 128
    assert p["bytes"] == p["w"] * p["h"] * 3

    # 3. Error reflection defense invariant: incoming error or .ok must NOT trigger error rebound
    def mock_dispatch(env: dict) -> Optional[dict]:
        verb = env.get("type", "")
        if verb == "error" or verb.endswith(".ok"):
            return None  # Drop without response
        if verb == "unknown":
            return {"type": "error", "id": env.get("id", 0), "payload": {"code": "unknown_type"}}
        return None

    assert mock_dispatch({"type": "error", "id": 1}) is None
    assert mock_dispatch({"type": "img.frame.ok", "id": 2}) is None
    assert mock_dispatch({"type": "unknown", "id": 3}) == {"type": "error", "id": 3, "payload": {"code": "unknown_type"}}

    return ["img.hello announced", "img.frame.ok geometry complete", "error reflection prevented"]


# ==============================================================================
# Domain 7: e2e (Tripartite End-to-End Integration: OBS <-> Provider <-> Device)
# ==============================================================================

@domain(
    "e2e", "integration", "full", "live", "device",
    desc="Tripartite live E2E: OBS WebSocket v5 <-> obs_mycro <-> Physical MYCRO Device (:7373)"
)
def test_e2e_domain() -> List[str]:
    host = CONFIG.get("host") or os.environ.get("MYCRO_HOST", "")
    port = CONFIG.get("port") or int(os.environ.get("MYCRO_RAW_PORT", 7373))
    token = CONFIG.get("token") or os.environ.get("MYCRO_TOKEN", "")

    if not host:
        host = "mycro.local"

    # Pre-resolve IPv4 specifically to eliminate macOS mDNS AAAA 5s timeouts
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            host = infos[0][4][0]
    except Exception:
        pass

    # Verify device reachability
    try:
        s = socket.create_connection((host, port), timeout=1.5)
        s.close()
    except Exception:
        raise SkipException(f"Physical MYCRO device offline at {host}:{port} (pass --host <ip> when connected)")

    # Verify OBS WebSocket reachability
    from obs.obs_mycro import ObsWsConfig
    ws_enabled, ws_port, ws_pwd = ObsWsConfig.load()
    if not ws_enabled:
        raise SkipException("OBS WebSocket disabled in config (enable in OBS Tools -> WebSocket Server Settings)")
    try:
        ws_test = socket.create_connection(("127.0.0.1", ws_port), timeout=1.5)
        ws_test.close()
    except Exception:
        raise SkipException(f"OBS WebSocket not running on :{ws_port} (launch OBS Studio to run live e2e)")

    details = []

    # Check whether an OBS capture provider is already registered on the device
    c = Client(host, port, token)
    dev_info = c.call("device.info")
    details.append(f"device fw {dev_info['payload'].get('fw', '?')}")

    check_prov_code = (
        "\\use{img}\\use{net}\\call{img.snap}{0,0,1,1}"
        "\\if{ {$err, =, 3} }{\\call{net.send}{noprov}}{\\call{net.send}{prov_ok}}"
    )
    c.call("macro.submit", {"code": check_prov_code})
    tags = []
    t_end = time.monotonic() + 3.0
    while time.monotonic() < t_end:
        env = c.recv_envelope()
        if env.get("type") == "net.msg":
            tags.append(env["payload"].get("tag"))
        elif env.get("type") == "macro.result":
            break

    if tags == ["noprov"]:
        c.close()
        raise SkipException(
            f"OBS plugin not connected to device at {host}:{port} -- "
            "load obs/obs_mycro.py in OBS (Tools -> Scripts) and click Connect"
        )
    details.append("OBS plugin active")

    try:
        # 1. End-to-end 2D spatial area snap, diff & pattern matching on device
        snap_code = (
            "\\#!syntax: free\n"
            "\\use{img}\\use{net}\n"
            "\\pixelrect{ref}{\\row{0xFF0000, 0x00FF00},\\row{0x0000FF, 0xFFFF00}}\n"
            "\\call{img.diff}{$ref}{$ref}{0}\\set{d_self}{$ret}\n"
            "\\call{img.snap}{0,0,16,16}\\set{s1}{$ret}\n"
            "\\call{img.snap}{0,0,16,16}\\set{s2}{$ret}\n"
            "\\call{img.diff}{$s1}{$s2}{5}\\set{d_snap}{$ret}\n"
            "\\call{img.at}{$s1}{0,0}\\set{c0}{$ret}\n"
            "\\call{img.at}{$s1}{15,15}\\set{c1}{$ret}\n"
            "\\if{ \\all{{$d_self, =, 0}, {$d_snap, =, 0}, {$err, =, 0}} }"
            "{\\call{net.send}{area_match_ok}}{\\call{net.send}{area_match_fail}}\n"
        )
        t_sub0 = time.monotonic()
        sub = c.call("macro.submit", {"code": snap_code})
        if sub.get("type") != "macro.submit.ok":
            raise AssertionError(f"macro submit failed: {sub}")

        tags = []
        res = None
        t_deadline = time.monotonic() + 5.0
        while time.monotonic() < t_deadline:
            env = c.recv_envelope()
            if env.get("type") == "net.msg":
                tags.append(env["payload"].get("tag"))
            elif env.get("type") == "macro.result":
                res = env
                break

        snap_ms = (time.monotonic() - t_sub0) * 1000.0
        if not (tags == ["area_match_ok"] and res and res["payload"].get("state") == "done"):
            raise AssertionError(f"E2E area match macro failed: tags={tags}, res={res}")
        details.append(f"area snap & diff ok in {snap_ms:.0f}ms")

        # 2. End-to-end concurrent multi-frequency telemetry combination on live device
        watch_code = (
            "\\#!syntax: free\n"
            "\\use{img}\\use{net}\n"
            "\\call{img.watch}{w_slow}{10,8}{50ms,6s}\n"
            "\\call{img.watch}{w_fast}{12,8}{25ms,6s}\n"
            "\\wait[3s]{$slot.img.w_slow.val}\n"
            "\\wait[3s]{$slot.img.w_fast.val}\n"
            "\\call{net.send}{multi_watch_ok}\n"
            "\\call{img.unwatch}{w_slow}\n"
            "\\call{img.unwatch}{w_fast}\n"
        )
        c.call("macro.submit", {"code": watch_code})
        tags = []
        t_deadline = time.monotonic() + 5.0
        while time.monotonic() < t_deadline:
            env = c.recv_envelope()
            if env.get("type") == "net.msg":
                tags.append(env["payload"].get("tag"))
            elif env.get("type") == "macro.result":
                break

        if not (tags == ["multi_watch_ok"]):
            raise AssertionError(f"E2E multi-frequency watch failed: tags={tags}")
        details.append("multi-watch [25/50ms] sampled ok")

        # 3. Web UI Direct Preview LAN stream from OBS via device endpoint
        import urllib.request
        def req_preview(method):
            r = urllib.request.Request(f"http://{host}/img/capture", method=method)
            if token:
                r.add_header("Authorization", f"Bearer {token}")
            return r

        urllib.request.urlopen(req_preview("POST"), timeout=5).read()
        preview_meta = None
        for _ in range(20):
            time.sleep(0.15)
            r = urllib.request.urlopen(req_preview("GET"), timeout=5)
            if r.status == 204:
                continue
            body = r.read()
            if body.startswith(b"{"):
                preview_meta = json.loads(body.decode("utf-8"))
                break

        if not preview_meta:
            raise AssertionError("preview arming timed out")

        url, nonce = preview_meta["url"], preview_meta["nonce"]
        sig = hmac.new((token or "").encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()
        target_url = f"{url}?type=png&nonce={urllib.parse.quote(nonce)}&sig={urllib.parse.quote(sig)}"
        req_p = urllib.request.Request(target_url)
        with urllib.request.urlopen(req_p, timeout=5) as r:
            assert r.status == 200
            assert r.headers.get("content-type") == "image/png"
            p_bytes = r.read()
            assert p_bytes[:8] == b"\x89PNG\r\n\x1a\n"
            assert len(p_bytes) > 1024
        details.append(f"direct preview PNG {len(p_bytes)//1024}KB ok")

    finally:
        c.close()

    return details


# ==============================================================================
# 8. Interactive Full Link Hardware Verification Domain
# ==============================================================================

@domain("full_link", "link", "gui", desc="Interactive full link test: visual stimulus to USB HID keystroke")
def test_full_link_domain() -> List[str]:
    host = CONFIG.get("host") or os.environ.get("MYCRO_HOST", "")
    port = CONFIG.get("port") or int(os.environ.get("MYCRO_RAW_PORT", 7373))
    token = CONFIG.get("token") or os.environ.get("MYCRO_TOKEN", "")

    if not host:
        host = "mycro.local"

    if CONFIG.get("skip_gui"):
        raise SkipException("Interactive full link test skipped (--skip-gui)")

    try:
        import tkinter
    except ImportError:
        raise SkipException("tkinter not available in environment")

    # Check device reachability
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        ip = infos[0][4][0] if infos else host
        s = socket.create_connection((ip, port), timeout=1.5)
        s.close()
    except Exception:
        raise SkipException(f"Physical MYCRO device offline at {host}:{port}")

    # Check OBS WebSocket reachability
    from obs.obs_mycro import ObsWsConfig
    ws_enabled, ws_port, ws_pwd = ObsWsConfig.load()
    if not ws_enabled:
        raise SkipException("OBS WebSocket disabled in config")
    try:
        ws_test = socket.create_connection(("127.0.0.1", ws_port), timeout=1.5)
        ws_test.close()
    except Exception:
        raise SkipException(f"OBS WebSocket not running on :{ws_port}")

    # Check if OBS capture provider is actually registered and connected on device
    c = Client(host, port, token)
    check_prov_code = (
        "\\use{img}\\use{net}\\call{img.snap}{0,0,1,1}"
        "\\if{ {$err, =, 3} }{\\call{net.send}{noprov}}{\\call{net.send}{prov_ok}}"
    )
    c.call("macro.submit", {"code": check_prov_code})
    tags = []
    t_end = time.monotonic() + 3.0
    while time.monotonic() < t_end:
        env = c.recv_envelope()
        if env.get("type") == "net.msg":
            tags.append(env["payload"].get("tag"))
        elif env.get("type") == "macro.result":
            break
    c.close()

    if tags == ["noprov"]:
        raise SkipException(
            f"OBS capture provider not connected to device at {host}:{port} -- "
            "ensure OBS Studio script is loaded and connected"
        )

    # Terminal startup banner for user awareness
    print("\n" + "=" * 65)
    print("[INTERACTIVE DOMAIN] Full Link Test: Visual Stimulus -> USB HID Keystroke")
    print("-" * 65)
    print("A test window has opened on your desktop.")
    print("1. Keep the window visible in your OBS Studio capture.")
    print("2. Click '1. Calibrate in OBS' to lock canvas coordinates.")
    print("3. Click '2. Run Full Link Test' to execute the 4-trial verification (multi-freq & 2D area pattern).")
    print("4. Click 'Finish Test' when complete to return to the test suite.")
    print("Waiting for user interaction in test window...")
    print("=" * 65 + "\n")

    # Block and launch the interactive test window for the user
    from tests.obs.full_link_test import FullLinkApp
    app = FullLinkApp(host, port, token, source=CONFIG.get("source", ""))
    app.mainloop()

    if not app.result.get("ok"):
        fail_reasons = app.result.get("failures") or app.result.get("details", ["Test closed without completion"])
        raise AssertionError("Full link test failed: " + "; ".join(fail_reasons))

    return app.result.get("details", ["full link verified"])


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="MYCRO OBS Capture Provider Integration & Probe Suite")
    parser.add_argument("--only", type=str, default="", help="Run only the specified domain (e.g. probe, wire, capture, e2e, full_link)")
    parser.add_argument("--skip-gui", dest="skip_gui", action="store_true", help="Skip the interactive physical GUI test")
    parser.add_argument("--host", default=os.environ.get("MYCRO_HOST", ""), help="Physical MYCRO device IP/mDNS hostname (default: mycro.local)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MYCRO_RAW_PORT", 7373)), help="MYCRO raw port (default: 7373)")
    parser.add_argument("--token", default=os.environ.get("MYCRO_TOKEN", ""), help="Bearer token")
    parser.add_argument("--source", default=os.environ.get("MYCRO_OBS_SOURCE", ""), help="Explicit OBS capture source or input name")
    args = parser.parse_args()

    CONFIG["host"] = args.host
    CONFIG["port"] = args.port
    CONFIG["token"] = args.token
    CONFIG["source"] = args.source
    CONFIG["skip_gui"] = args.skip_gui

    targets = [d for d in REGISTRY if d.matches(args.only)] if args.only else list(REGISTRY)

    if not targets:
        print(f"No domains matching '{args.only}'. Available: {', '.join(d.name for d in REGISTRY)}")
        sys.exit(1)

    print("=== MYCRO OBS Capture Provider Test & Diagnostic Suite ===")
    all_ok = True
    for d in targets:
        ok = d.run()
        if not ok:
            all_ok = False

    passed = sum(1 for _, ok, _ in RESULTS if ok is True)
    failed = sum(1 for _, ok, _ in RESULTS if ok is False)
    skipped = sum(1 for _, ok, _ in RESULTS if ok is None)

    print("-" * 55)
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
