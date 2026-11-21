"""Unit tests for obs/obs_mycro.py (the high-performance in-memory OBS capture provider).

Tests every orthogonal capability in isolation without requiring a live OBS Studio process:
    1. CaptureEngine BMP header parsing, O(1) buffer lookup, crop slicing, and source/retry handling
    2. EphemeralHttpServer Chrome PNA 204 preflight, HMAC-SHA256, dev mode, and 410 lease expiration
    3. TelemetrySampler rate clamping (>=20ms), periodic dispatch, and post-sample stop latch
    4. WireSession protocol v1 prologue, LE framing, and kind-1 binary chunking
    5. ProtoActionRouter route dispatch, geometry schema, unwatch ACK/timeout, and error reflection defense
    6. ObsWsConfig auto-discovery from local JSON files
    7. ObsWsClient RPC exception hierarchy (business vs transport) and subscription negotiation
"""

import base64
import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from typing import Dict, Tuple

import pytest

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import obs.obs_mycro as obs_mycro
from obs.obs_mycro import (
    CaptureEngine,
    EphemeralHttpServer,
    TelemetrySampler,
    WireSession,
    ProtoActionRouter,
    ObsWsConfig,
    ObsWsClient,
    ObsWsError,
    ObsWsTransportError,
    ObsWsBusinessError,
    TELEMETRY_MIN_INTERVAL_MS,
    MAX_CHUNK_PAYLOAD,
)


def make_test_bmp(width: int, height: int, pixels: Dict[Tuple[int, int], Tuple[int, int, int]]) -> bytes:
    """Constructs a minimal 24-bit uncompressed DIB BMP in memory."""
    stride = ((width * 3 + 3) // 4) * 4
    image_size = stride * height
    file_size = 54 + image_size
    header = bytearray(54)
    header[0:2] = b"BM"
    struct.pack_into("<I", header, 2, file_size)
    struct.pack_into("<I", header, 10, 54)   # offset
    struct.pack_into("<I", header, 14, 40)   # dib_size
    struct.pack_into("<ii", header, 18, width, height)
    struct.pack_into("<H", header, 26, 1)    # planes
    struct.pack_into("<H", header, 28, 24)   # bpp
    struct.pack_into("<I", header, 30, 0)    # comp
    struct.pack_into("<I", header, 34, image_size)

    data = bytearray(image_size)
    for (x, y), (r, g, b) in pixels.items():
        if 0 <= x < width and 0 <= y < height:
            row = (height - 1 - y) if height > 0 else y
            pos = row * stride + x * 3
            data[pos] = b
            data[pos + 1] = g
            data[pos + 2] = r
    return bytes(header) + bytes(data)


# ==============================================================================
# 1. CaptureEngine Tests
# ==============================================================================

def test_capture_engine_bmp_header_and_points():
    pixels = {
        (0, 0): (0x12, 0x34, 0x56),
        (3, 2): (0xAB, 0xCD, 0xEF),
        (1, 1): (0x00, 0xFF, 0x00),
    }
    bmp_bytes = make_test_bmp(4, 3, pixels)
    mv = memoryview(bmp_bytes)

    ce = CaptureEngine(ws_client=None, default_source="dummy")
    offset, w, h, stride = ce.parse_bmp_header(mv)
    assert offset == 54
    assert w == 4
    assert h == 3
    assert stride == 12

    # Extract points
    colors = ce.extract_points(mv, [(0, 0), (3, 2), (1, 1)])
    assert colors == [0x123456, 0xABCDEF, 0x00FF00]

    # Out of bounds points must raise ValueError
    with pytest.raises(ValueError):
        ce.extract_points(mv, [(-1, 0)])
    with pytest.raises(ValueError):
        ce.extract_points(mv, [(4, 0)])
    with pytest.raises(ValueError):
        ce.extract_points(mv, [(0, 3)])


def test_capture_engine_extract_rect():
    pixels = {
        (1, 1): (0x11, 0x22, 0x33),
        (2, 1): (0x44, 0x55, 0x66),
    }
    bmp_bytes = make_test_bmp(4, 3, pixels)
    mv = memoryview(bmp_bytes)

    ce = CaptureEngine(ws_client=None, default_source="dummy")
    # Crop 2x1 region starting at (1, 1)
    rect = {"x": 1, "y": 1, "w": 2, "h": 1}
    crop = ce.extract_rect(mv, rect)
    assert len(crop) == 2 * 1 * 3
    assert crop == bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])

    # Out of bounds rect must raise ValueError
    with pytest.raises(ValueError):
        ce.extract_rect(mv, {"x": 3, "y": 0, "w": 2, "h": 1})
    with pytest.raises(ValueError):
        ce.extract_rect(mv, {"x": -1, "y": 0, "w": 1, "h": 1})


def test_capture_engine_source_resolution_and_retry(monkeypatch):
    monkeypatch.setattr(ObsWsConfig, "get_configured_script_source", classmethod(lambda cls: ""))
    screenshot_attempts = [0]
    connect_count = [0]

    class MockWs:
        def connect(self, timeout=3.0):
            connect_count[0] += 1

        def call(self, req_type, data=None):
            if req_type == "GetCurrentProgramScene":
                return {"currentProgramSceneName": "GameplayScene"}
            if req_type == "GetSourceScreenshot":
                if data and data.get("sourceName") == "InvalidScene":
                    raise ObsWsBusinessError(600, "Source not found")
                screenshot_attempts[0] += 1
                if screenshot_attempts[0] == 1:  # First attempt fails with transport error
                    raise ObsWsTransportError("Connection dropped")
                dummy_bmp = base64.b64encode(b"BM" + b"\x00" * 60).decode("ascii")
                return {"imageData": f"data:image/bmp;base64,{dummy_bmp}"}
            return {}

    ws = MockWs()
    ce = CaptureEngine(ws_client=ws, default_source="")
    assert ce.resolve_source() == "GameplayScene"

    # Screenshot retry: transport error triggers exactly 1 reconnect attempt
    res = ce._fetch_screenshot_bytes("bmp")
    assert res.startswith(b"BM")
    assert connect_count[0] == 1

    # Screenshot business error: MUST NOT attempt reconnect
    ce_biz = CaptureEngine(ws_client=ws, default_source="InvalidScene")
    with pytest.raises(ObsWsBusinessError):
        ce_biz._fetch_screenshot_bytes("bmp")
    assert connect_count[0] == 1


# ==============================================================================
# 2. EphemeralHttpServer Tests (Asyncio-based Chrome PNA & HMAC)
# ==============================================================================

def test_ephemeral_http_server_pna_and_hmac():
    dummy_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    producer_calls = [0]

    def test_producer():
        producer_calls[0] += 1
        return dummy_png

    token = "test_secret_token"
    server = EphemeralHttpServer()
    nonce = os.urandom(16).hex()
    port = server.serve(token=token, nonce=nonce, data_producer=test_producer)
    assert port > 0

    # 1. Test Chrome Private Network Access (PNA) OPTIONS preflight request
    s_opt = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    s_opt.sendall(
        f"OPTIONS /capture?type=png&nonce={nonce} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Access-Control-Request-Private-Network: true\r\n\r\n".encode("utf-8")
    )
    res_opt = s_opt.recv(4096)
    s_opt.close()
    assert b"HTTP/1.1 204 No Content" in res_opt
    assert b"Access-Control-Allow-Private-Network: true" in res_opt
    assert b"Access-Control-Allow-Origin: *" in res_opt

    # Pre-connect concurrent socket to test duplicate GET lease expiration
    s_dup = socket.create_connection(("127.0.0.1", port), timeout=2.0)

    # 2. Test valid HMAC signature -> 200 OK
    sig = hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()
    s_get = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    s_get.sendall(
        f"GET /capture?type=png&nonce={nonce}&sig={sig} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n\r\n".encode("utf-8")
    )
    res_get = bytearray()
    while True:
        chunk = s_get.recv(4096)
        if not chunk:
            break
        res_get.extend(chunk)
    s_get.close()
    assert b"HTTP/1.1 200 OK" in res_get
    assert b"Content-Type: image/png" in res_get
    assert dummy_png in res_get
    assert producer_calls[0] == 1

    # 3. Duplicate GET on concurrent connection -> 410 Gone (pre-producer atomic latch prevents second invocation)
    s_dup.sendall(
        f"GET /capture?type=png&nonce={nonce}&sig={sig} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n\r\n".encode("utf-8")
    )
    res_dup = bytearray()
    while True:
        chunk = s_dup.recv(4096)
        if not chunk:
            break
        res_dup.extend(chunk)
    s_dup.close()
    assert b"HTTP/1.1 410 Gone" in res_dup
    assert producer_calls[0] == 1  # INVARIANT: Producer was NOT called a second time

    # 4. Test tampered signature -> 403 Forbidden
    nonce2 = os.urandom(16).hex()
    port2 = server.serve(token=token, nonce=nonce2, data_producer=test_producer)
    s_bad = socket.create_connection(("127.0.0.1", port2), timeout=2.0)
    s_bad.sendall(
        f"GET /capture?type=png&nonce={nonce2}&sig=tampered_sig HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port2}\r\n\r\n".encode("utf-8")
    )
    res_bad = s_bad.recv(4096)
    s_bad.close()
    assert b"HTTP/1.1 403 Forbidden" in res_bad

    server.stop()


def test_ephemeral_http_server_dev_mode():
    dummy_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    server = EphemeralHttpServer()
    nonce = os.urandom(16).hex()
    port = server.serve(token="", nonce=nonce, data_producer=lambda: dummy_png)

    # Empty token dev mode uses empty-key HMAC signature
    sig = hmac.new(b"", nonce.encode("utf-8"), hashlib.sha256).hexdigest()
    s = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    s.sendall(
        f"GET /capture?type=png&nonce={nonce}&sig={sig} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n\r\n".encode("utf-8")
    )
    res = bytearray()
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        res.extend(chunk)
    s.close()
    assert b"HTTP/1.1 200 OK" in res
    server.stop()


# ==============================================================================
# 3. TelemetrySampler Tests
# ==============================================================================

def test_telemetry_sampler_lifecycle():
    sampler = TelemetrySampler()
    samples = []

    # 1. Clamping & normal termination
    eff_interval = sampler.start_watch(
        name="test_watch",
        interval_ms=5,
        duration_ms=1000,
        sample_fn=lambda: 0x00FF00,
        on_sample=lambda name, val: samples.append((name, val)),
    )
    assert eff_interval == TELEMETRY_MIN_INTERVAL_MS
    time.sleep(0.08)
    assert sampler.stop_watch("test_watch") is True
    assert len(samples) >= 2
    assert all(val == 0x00FF00 for _, val in samples)
    assert len(sampler._watches) == 0
    assert sampler.stop_watch("nonexistent") is True

    # 2. Post-sample stop latch: discard in-flight sample on stop
    sample_started = threading.Event()
    sample_proceed = threading.Event()
    dispatched = []

    def slow_sample():
        sample_started.set()
        sample_proceed.wait(timeout=2.0)
        return 0x00FF00

    sampler.start_watch("latch_watch", 20, 1000, slow_sample, lambda name, val: dispatched.append((name, val)))
    sample_started.wait(timeout=2.0)

    stop_done = threading.Event()

    def do_stop():
        assert sampler.stop_watch("latch_watch", timeout_sec=2.0) is True
        stop_done.set()

    t_stop = threading.Thread(target=do_stop, daemon=True)
    t_stop.start()
    time.sleep(0.05)

    sample_proceed.set()
    stop_done.wait(timeout=2.0)
    t_stop.join(timeout=2.0)
    assert len(dispatched) == 0


def test_telemetry_refresh_reuses_worker_and_discards_stale_capture():
    sampler = TelemetrySampler()
    old_started = threading.Event()
    release_old = threading.Event()
    new_sampled = threading.Event()
    samples = []

    def old_sample():
        old_started.set()
        release_old.wait(timeout=2.0)
        return 1

    def new_sample():
        return 2

    def new_emit(name, val):
        samples.append(val)
        new_sampled.set()

    try:
        sampler.start_watch("s1", 20, 1000, old_sample, lambda n, v: samples.append(v))
        assert old_started.wait(timeout=1.0)
        entry = sampler._watches["s1"]
        thread = entry["thread"]

        # Refresh must not wait for the old capture or replace the thread.
        sampler.start_watch("s1", 20, 1000, new_sample, new_emit)
        assert sampler._watches["s1"] is entry
        assert entry["thread"] is thread
        assert thread.is_alive()
        assert not new_sampled.is_set()

        release_old.set()
        assert new_sampled.wait(timeout=1.0)
        assert samples == [2]
    finally:
        release_old.set()
        sampler.stop_all()


def test_telemetry_refresh_drains_old_emission_before_returning():
    sampler = TelemetrySampler()
    emission_started = threading.Event()
    release_emission = threading.Event()
    refreshed = threading.Event()
    order = []

    def old_emit(name, val):
        order.append("old-start")
        emission_started.set()
        release_emission.wait(timeout=2.0)
        order.append("old-end")

    try:
        sampler.start_watch("s1", 20, 1000, lambda: 1, old_emit)
        assert emission_started.wait(timeout=1.0)

        def refresh():
            sampler.start_watch("s1", 20, 1000, lambda: 2, lambda n, v: None)
            order.append("refresh")
            refreshed.set()

        t = threading.Thread(target=refresh, daemon=True)
        t.start()
        time.sleep(0.03)
        assert not refreshed.is_set()
        release_emission.set()
        assert refreshed.wait(timeout=1.0)
        t.join(timeout=1.0)
        assert order[:3] == ["old-start", "old-end", "refresh"]
    finally:
        release_emission.set()
        sampler.stop_all()


def test_telemetry_lease_expires_promptly_and_suppresses_late_capture():
    sampler = TelemetrySampler()
    capture_started = threading.Event()
    release_capture = threading.Event()
    samples = []

    def slow_sample():
        capture_started.set()
        release_capture.wait(timeout=2.0)
        return 1

    try:
        sampler.start_watch("lease", 1000, 30, slow_sample, lambda n, v: samples.append(v))
        assert capture_started.wait(timeout=1.0)
        entry = sampler._watches["lease"]
        time.sleep(0.06)
        release_capture.set()
        entry["thread"].join(timeout=1.0)
        assert not entry["thread"].is_alive()
        assert "lease" not in sampler._watches
        assert samples == []
        assert sampler.stop_watch("lease") is True
    finally:
        release_capture.set()
        sampler.stop_all()


def test_telemetry_stop_timeout_keeps_terminal_worker_tracked():
    sampler = TelemetrySampler()
    capture_started = threading.Event()
    release_capture = threading.Event()

    def blocked_sample():
        capture_started.set()
        release_capture.wait(timeout=2.0)
        return 1

    try:
        sampler.start_watch("blocked", 20, 1000, blocked_sample, lambda n, v: None)
        assert capture_started.wait(timeout=1.0)
        entry = sampler._watches["blocked"]
        assert sampler.stop_watch("blocked", timeout_sec=0.01) is False
        assert sampler._watches["blocked"] is entry
        assert entry["stop"].is_set()
        with pytest.raises(RuntimeError, match="still stopping"):
            sampler.start_watch("blocked", 20, 1000, lambda: 2, lambda n, v: None)
        release_capture.set()
        assert sampler.stop_watch("blocked", timeout_sec=1.0) is True
        assert "blocked" not in sampler._watches
    finally:
        release_capture.set()
        sampler.stop_all()


def test_telemetry_stop_timeout_covers_blocked_emission():
    sampler = TelemetrySampler()
    emission_started = threading.Event()
    release_emission = threading.Event()

    def blocked_emit(name, val):
        emission_started.set()
        release_emission.wait(timeout=2.0)

    try:
        sampler.start_watch("emit", 20, 1000, lambda: 1, blocked_emit)
        assert emission_started.wait(timeout=1.0)
        entry = sampler._watches["emit"]
        started = time.monotonic()
        assert sampler.stop_watch("emit", timeout_sec=0.01) is False
        assert time.monotonic() - started < 0.5
        assert entry["stop"].is_set()
        release_emission.set()
        assert sampler.stop_watch("emit", timeout_sec=1.0) is True
    finally:
        release_emission.set()
        sampler.stop_all()


def test_telemetry_refresh_replaces_retiring_worker_instead_of_reviving_it():
    sampler = TelemetrySampler()

    class ExitingThread:
        def is_alive(self):
            return True

    old_thread = ExitingThread()
    old_entry = {
        "thread": old_thread,
        "stop": threading.Event(),
        "lock": threading.Lock(),
        "retiring": True,
    }
    sampler._watches["s1"] = old_entry
    try:
        sampler.start_watch("s1", 20, 1000, lambda: 2, lambda n, v: None)
        new_entry = sampler._watches["s1"]
        assert new_entry is not old_entry
        assert new_entry["thread"] is not old_thread
        assert not new_entry["retiring"]
    finally:
        sampler.stop_all()


# ==============================================================================
# 4. WireSession Tests
# ==============================================================================

def test_wire_session_framing_and_chunking():
    s_client, s_server = socket.socketpair()
    wire = WireSession(host="127.0.0.1", port=7373, token="secret_123")
    wire.sock = s_client

    # Send kind-0 JSON envelope
    test_env = {"v": 1, "type": "macro.result", "id": 1, "payload": {"state": "done"}}
    wire.send_envelope(test_env)

    # Read from server side
    hdr = s_server.recv(4)
    (h,) = struct.unpack("<I", hdr)
    kind, length = h >> 31, h & 0x7FFFFFFF
    assert kind == 0
    body = s_server.recv(length)
    rec_env = json.loads(body.decode("utf-8"))
    assert rec_env["type"] == "macro.result"
    assert rec_env["payload"]["state"] == "done"

    # Send kind-1 binary chunks
    data = b"\x42" * 1000
    wire.send_chunks(stream_id=5, data=data)

    c_hdr = s_server.recv(4)
    (ch,) = struct.unpack("<I", c_hdr)
    c_kind, c_length = ch >> 31, ch & 0x7FFFFFFF
    assert c_kind == 1
    c_payload = s_server.recv(c_length)
    sid, flags = struct.unpack("<HH", c_payload[:4])
    assert sid == 5
    assert flags == 0
    assert c_payload[4:] == data

    s_client.close()
    s_server.close()


def test_wire_send_timeout_disconnects_desynchronized_stream():
    class FailingSocket:
        def __init__(self):
            self.closed = False

        def sendall(self, data):
            raise socket.timeout("blocked write")

        def shutdown(self, how):
            pass

        def close(self):
            self.closed = True

    failing = FailingSocket()
    wire = WireSession(host="127.0.0.1", port=7373)
    wire.sock = failing
    with pytest.raises(socket.timeout):
        wire.send_envelope({"type": "event.push", "id": 0, "payload": {}})
    assert wire.sock is None
    assert wire._closed
    assert failing.closed


def test_wire_recv_exact_honors_stop_event_during_timeout():
    recv_started = threading.Event()
    stopped = threading.Event()

    class IdleSocket:
        def recv(self, n):
            recv_started.set()
            raise socket.timeout("idle")

    stop_evt = threading.Event()

    def receive():
        with pytest.raises(ConnectionError, match="Receive stopped"):
            WireSession._recv_exact(IdleSocket(), 4, stop_evt)
        stopped.set()

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    assert recv_started.wait(timeout=1.0)
    stop_evt.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert stopped.is_set()


# ==============================================================================
# 5. ProtoActionRouter Tests
# ==============================================================================

class MockWire:
    def __init__(self):
        self.sent_envelopes = []
        self.sent_chunks = []
        self._action_handler = None
        self._connect_handler = None
        self._stream_id = 0
        self.disconnected = False

    def set_action_handler(self, handler):
        self._action_handler = handler

    def set_connect_handler(self, handler):
        self._connect_handler = handler

    def next_stream_id(self):
        self._stream_id += 1
        return self._stream_id

    def send_envelope(self, env):
        self.sent_envelopes.append(env)

    def disconnect(self):
        self.disconnected = True

    def send_chunks(self, sid, data):
        self.sent_chunks.append((sid, data))


def test_proto_action_router_dispatch_and_error_defense():
    pixels = {(0, 0): (0x11, 0x22, 0x33)}
    bmp_bytes = make_test_bmp(4, 3, pixels)

    class StubCapture:
        def get_raw_bmp(self):
            return memoryview(bmp_bytes)
        def parse_bmp_header(self, mv):
            return (54, 4, 3, 12)
        def extract_points(self, mv, pts):
            return [0x112233]
        def extract_rect(self, mv, rect):
            return bytes([0x11, 0x22, 0x33])

    wire = MockWire()
    router = ProtoActionRouter(
        capture=StubCapture(),
        preview=None,
        sampler=TelemetrySampler(),
        wire=wire,
    )

    # 1. Test connect announcement
    router.on_connected()
    assert len(wire.sent_envelopes) == 1
    assert wire.sent_envelopes[0]["type"] == "img.hello"

    # 2. Test img.frame with points
    router.dispatch_action({"type": "img.frame", "id": 10, "payload": {"points": [[0, 0]]}})
    rep = wire.sent_envelopes[-1]
    assert rep["type"] == "img.frame.ok"
    assert rep["payload"]["colors"] == [0x112233]

    # 3. Test unknown verb -> error envelope
    router.dispatch_action({"type": "bad.verb", "id": 11, "payload": {}})
    err = wire.sent_envelopes[-1]
    assert err["type"] == "error"
    assert err["payload"]["code"] == "unknown_type"

    # 4. Error reflection defense: incoming error or .ok must NOT trigger replies
    initial_len = len(wire.sent_envelopes)
    router.dispatch_action({"type": "error", "id": 12, "payload": {}})
    router.dispatch_action({"type": "macro.result", "id": 0, "payload": {}})  # push
    router.dispatch_action({"type": "img.frame.ok", "id": 13, "payload": {}})
    assert len(wire.sent_envelopes) == initial_len

    # 5. Test img.unwatch ACK
    router.dispatch_action({"type": "img.unwatch", "id": 50, "payload": {"name": "test_unwatch"}})
    rep_unwatch = wire.sent_envelopes[-1]
    assert rep_unwatch["type"] == "img.unwatch.ok"
    assert rep_unwatch["id"] == 50

    # 6. Unwatch timeout is localized to the named watch
    class StubTimingOutSampler(TelemetrySampler):
        def stop_watch(self, name: str, timeout_sec: float = 5.5) -> bool:
            return False

    router_timeout = ProtoActionRouter(
        capture=None,
        preview=None,
        sampler=StubTimingOutSampler(),
        wire=wire,
    )
    router_timeout.dispatch_action({"type": "img.unwatch", "id": 51, "payload": {"name": "hung_watch"}})
    err_unwatch = wire.sent_envelopes[-1]
    assert err_unwatch["type"] == "error"
    assert err_unwatch["id"] == 51
    assert err_unwatch["payload"]["code"] == "timeout"
    assert not wire.disconnected


# ==============================================================================
# 6. ObsWsConfig Auto-Discovery Tests
# ==============================================================================

def test_obs_ws_config_discovery():
    with tempfile.TemporaryDirectory() as td:
        cfg_path = os.path.join(td, "config.json")
        with open(cfg_path, "w") as f:
            json.dump({
                "server_enabled": True,
                "server_port": 4455,
                "server_password": "test_ws_password",
            }, f)

        orig_fn = ObsWsConfig.get_config_path
        ObsWsConfig.get_config_path = classmethod(lambda cls: cfg_path)
        try:
            enabled, port, pwd = ObsWsConfig.load()
            assert enabled is True
            assert port == 4455
            assert pwd == "test_ws_password"
        finally:
            ObsWsConfig.get_config_path = orig_fn


# ==============================================================================
# 7. ObsWsClient RPC Exception & Subscription Tests
# ==============================================================================

def _send_ws_json(sock: socket.socket, data: dict) -> None:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    length = len(raw)
    if length <= 125:
        hdr = struct.pack("!BB", 0x81, length)
    elif length <= 65535:
        hdr = struct.pack("!BBH", 0x81, 126, length)
    else:
        hdr = struct.pack("!BBQ", 0x81, 127, length)
    sock.sendall(hdr + raw)


def _recv_ws_json(f) -> dict:
    read_fn = getattr(f, "read", None) or getattr(f, "recv")
    b0, b1 = struct.unpack("!BB", read_fn(2))
    plen = b1 & 0x7F
    if plen == 126:
        plen = struct.unpack("!H", read_fn(2))[0]
    elif plen == 127:
        plen = struct.unpack("!Q", read_fn(8))[0]
    mask = read_fn(4) if (b1 & 0x80) else None
    raw = bytearray()
    while len(raw) < plen:
        chunk = read_fn(plen - len(raw))
        if not chunk:
            break
        raw.extend(chunk)
    if mask:
        raw = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
    return json.loads(raw.decode("utf-8"))


def test_obs_ws_client_error_handling():
    s_client, s_server = socket.socketpair()
    client = ObsWsClient()
    with client._sock_lock:
        client.sock = s_client
        client.rfile = s_client.makefile("rb", buffering=65536)

    # 1. Trigger an OBS RPC business error (e.g. source not found)
    def handle_call1():
        req = _recv_ws_json(s_server)
        assert req["op"] == 6
        _send_ws_json(s_server, {
            "op": 7,
            "d": {
                "requestId": req["d"]["requestId"],
                "requestStatus": {"result": False, "code": 600, "comment": "Source not found"},
            }
        })

    t1 = threading.Thread(target=handle_call1, daemon=True)
    t1.start()

    with pytest.raises(ObsWsBusinessError) as exc_info:
        client.call("GetSourceScreenshot", {"sourceName": "NonExistent"})
    t1.join(timeout=1.0)

    assert exc_info.value.code == 600
    assert "Source not found" in str(exc_info.value)
    # INVARIANT: Socket MUST NOT be closed on business errors!
    assert client.sock is not None
    assert client.rfile is not None

    # 2. Subsequent call succeeds on the SAME open socket without reconnect
    def handle_call2():
        req = _recv_ws_json(s_server)
        assert req["op"] == 6
        _send_ws_json(s_server, {
            "op": 7,
            "d": {
                "requestId": req["d"]["requestId"],
                "requestStatus": {"result": True, "code": 100},
                "responseData": {"sceneName": "CurrentScene"},
            }
        })

    t2 = threading.Thread(target=handle_call2, daemon=True)
    t2.start()

    resp = client.call("GetCurrentProgramScene")
    t2.join(timeout=1.0)
    assert resp == {"sceneName": "CurrentScene"}

    # 3. Transport error abruptly closing connection MUST close and clear socket
    s_server.close()
    with pytest.raises(ObsWsTransportError):
        client.call("GetVideoSettings")

    assert client.sock is None
    assert client.rfile is None


def test_obs_ws_identify_event_subscriptions_zero(monkeypatch):
    s_client, s_server = socket.socketpair()
    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: s_client)

    received_identify = {}

    def server_thread():
        rf = s_server.makefile("rb")
        key = ""
        while True:
            line = rf.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            if line.lower().startswith(b"sec-websocket-key:"):
                key = line.split(b":", 1)[1].strip().decode("ascii")
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        s_server.sendall(
            f"HTTP/1.1 101 Switching Protocols\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode("ascii")
        )

        _send_ws_json(s_server, {"op": 0, "d": {"rpcVersion": 1}})
        received_identify.update(_recv_ws_json(rf))
        _send_ws_json(s_server, {"op": 2, "d": {"negotiatedRpcVersion": 1}})

        time.sleep(0.02)
        rf.close()
        s_server.close()

    t = threading.Thread(target=server_thread, daemon=True)
    t.start()

    client = ObsWsClient()
    client.connect(timeout=2.0)
    client.disconnect()
    t.join(timeout=2.0)

    assert received_identify.get("op") == 1
    d = received_identify.get("d", {})
    assert d.get("rpcVersion") == 1
    assert d.get("eventSubscriptions") == 0
