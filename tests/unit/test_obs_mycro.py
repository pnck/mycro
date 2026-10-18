"""Unit tests for obs/obs_mycro.py (the actual OBS capture provider script).

Tests the script directly by mocking the OBS environment (`obspython`),
ensuring the provider logic is rigorously verified in automated CI
rather than existing only in a detached mock.
"""

import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import threading
import time
import types

# 1. Mock obspython BEFORE importing obs_mycro
mock_obs = types.ModuleType("obspython")
mock_obs.LOG_INFO = 1
mock_obs.LOG_WARNING = 2
mock_obs.LOG_ERROR = 3

logs = []

def _mock_script_log(level, msg):
    logs.append((level, msg))

mock_obs.script_log = _mock_script_log
mock_obs.obs_get_source_by_name = lambda name: "mock_source" if name else None
mock_obs.obs_source_release = lambda src: None
mock_obs.obs_source_get_width = lambda src: 1920
mock_obs.obs_source_get_height = lambda src: 1080
mock_obs.obs_data_set_string = lambda s, k, v: None
mock_obs.obs_data_set_default_int = lambda s, k, v: None
mock_obs.obs_data_set_default_bool = lambda s, k, v: None
mock_obs.obs_data_get_string = lambda s, k: ""
mock_obs.obs_data_get_int = lambda s, k: 0
mock_obs.obs_data_get_bool = lambda s, k: False
mock_obs.obs_frontend_add_event_callback = lambda cb: None
mock_obs.obs_frontend_remove_event_callback = lambda cb: None
mock_obs.obs_frontend_get_last_screenshot = lambda: ""
mock_obs.obs_frontend_take_source_screenshot = lambda src: False
mock_obs.timer_add = lambda cb, ms: None
mock_obs.timer_remove = lambda cb: None

sys.modules["obspython"] = mock_obs

# Mock PIL if not installed in current environment
try:
    import PIL
except ImportError:
    mock_pil = types.ModuleType("PIL")
    mock_image = types.ModuleType("PIL.Image")
    mock_image.Resampling = types.SimpleNamespace(BILINEAR=2)
    mock_image.BILINEAR = 2
    mock_pil.Image = mock_image
    sys.modules["PIL"] = mock_pil
    sys.modules["PIL.Image"] = mock_image

# 2. Add repo root to sys.path and import obs_mycro
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)
import obs.obs_mycro as obs_mycro


class MockChannel:
    """Socket-like mock to inspect outgoing frames."""
    def __init__(self):
        self.sent = bytearray()

    def sendall(self, data):
        self.sent.extend(data)

    def pop_envelopes(self):
        envs = []
        buf = self.sent
        while len(buf) >= 4:
            (h,) = struct.unpack("<I", buf[:4])
            kind, length = h >> 31, h & 0x7FFFFFFF
            if len(buf) < 4 + length:
                break
            payload = bytes(buf[4:4 + length])
            del buf[:4 + length]
            if kind == 0:
                envs.append(json.loads(payload.decode("utf-8")))
        return envs


def _mock_socket_factory(srv_conns):
    orig_socket = socket.socket
    conns = list(srv_conns) if isinstance(srv_conns, (list, tuple)) else [srv_conns]
    class MockSock:
        def __init__(self, *a, **k): pass
        def setsockopt(self, *a): pass
        def bind(self, *a): pass
        def getsockname(self): return ("127.0.0.1", 54321)
        def listen(self, *a): pass
        def settimeout(self, *a): pass
        def close(self): pass
        def accept(self):
            if conns:
                return (conns.pop(0), ("127.0.0.1", 12345))
            time.sleep(0.5)
            raise socket.timeout()
    return orig_socket, MockSock


def test_obs_unknown_type_logs_warning_and_replies_error():
    logs.clear()
    ch = MockChannel()
    env = {
        "v": 1,
        "type": "nonexistent.verb",
        "id": 42,
        "payload": {}
    }
    obs_mycro.handle(ch, env)

    # 1. Must log warning to OBS script log (no silent errors!)
    assert any(level == mock_obs.LOG_WARNING and "unknown request type from device: 'nonexistent.verb'" in msg
               for level, msg in logs), "Unknown type must be logged to OBS console"

    # 2. Must send error envelope
    replies = ch.pop_envelopes()
    assert len(replies) == 1
    rep = replies[0]
    assert rep["type"] == "error"
    assert rep["id"] == 42
    assert rep["payload"]["code"] == "unknown_type"


def test_obs_arm_preview_happy_path_and_rejection():
    logs.clear()
    token = "secret_token_123"
    obs_mycro.device_token = token
    obs_mycro.source_name = "test_src"
    ch = MockChannel()
    nonce = os.urandom(16).hex()
    exp_sig = hmac.new(token.encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()

    # Stub capture_png
    dummy_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    obs_mycro.capture_png = lambda: (dummy_png, 1920, 1080)

    # 1. Arm preview with 2 sequential connections: connection 1 for OPTIONS, connection 2 for GET
    client_opt, srv_opt = socket.socketpair()
    client_get, srv_get = socket.socketpair()
    orig_socket, MockSock1 = _mock_socket_factory([srv_opt, srv_get])
    socket.socket = MockSock1
    try:
        env = {
            "v": 1,
            "type": "img.arm_preview",
            "id": 101,
            "payload": {"nonce": nonce}
        }
        obs_mycro.handle(ch, env)
    finally:
        socket.socket = orig_socket

    replies = ch.pop_envelopes()
    assert len(replies) == 1
    rep = replies[0]
    assert rep["type"] == "img.arm_preview.ok"
    assert rep["id"] == 101
    assert rep["payload"]["port"] == 54321
    assert rep["payload"]["width"] == 1920
    assert rep["payload"]["height"] == 1080

    # 2. Test Chrome Private Network Access (PNA) OPTIONS preflight request
    client_opt.sendall(f"OPTIONS /capture?type=png&nonce={nonce} HTTP/1.1\r\nAccess-Control-Request-Private-Network: true\r\n\r\n".encode("utf-8"))
    opt_res = client_opt.recv(4096)
    client_opt.close()
    assert b"HTTP/1.1 204 No Content" in opt_res
    assert b"Access-Control-Allow-Private-Network: true" in opt_res
    assert b"Access-Control-Allow-Origin: *" in opt_res

    # 3. Valid GET request on subsequent connection -> 200 OK with PNG
    client_get.sendall(f"GET /capture?type=png&nonce={nonce}&sig={exp_sig} HTTP/1.1\r\n\r\n".encode("utf-8"))
    res = bytearray()
    while True:
        chunk = client_get.recv(4096)
        if not chunk:
            break
        res.extend(chunk)
    client_get.close()
    assert b"HTTP/1.1 200 OK" in res
    assert b"Content-Type: image/png" in res
    assert b"Access-Control-Allow-Private-Network: true" in res
    assert dummy_png in res

    # 4. Arm again and test tampered signature -> 403 Forbidden
    client_sock2, srv_conn2 = socket.socketpair()
    _, MockSock2 = _mock_socket_factory(srv_conn2)
    socket.socket = MockSock2
    try:
        env = {
            "v": 1,
            "type": "img.arm_preview",
            "id": 102,
            "payload": {"nonce": nonce}
        }
        obs_mycro.handle(ch, env)
    finally:
        socket.socket = orig_socket

    client_sock2.sendall(f"GET /capture?type=png&nonce={nonce}&sig=bad_sig HTTP/1.1\r\n\r\n".encode("utf-8"))
    res2 = bytearray()
    while True:
        chunk = client_sock2.recv(4096)
        if not chunk:
            break
        res2.extend(chunk)
    client_sock2.close()
    assert b"HTTP/1.1 403 Forbidden" in res2
    assert b"Forbidden" in res2


def test_obs_arm_preview_dev_mode():
    obs_mycro.device_token = ""  # dev mode (empty token)
    ch = MockChannel()
    nonce = os.urandom(16).hex()
    dummy_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    obs_mycro.capture_png = lambda: (dummy_png, 1920, 1080)

    # 1. Dev mode with empty sig succeeds
    client_sock1, srv_conn1 = socket.socketpair()
    orig_socket, MockSock1 = _mock_socket_factory(srv_conn1)
    socket.socket = MockSock1
    try:
        env = {
            "v": 1,
            "type": "img.arm_preview",
            "id": 103,
            "payload": {"nonce": nonce}
        }
        obs_mycro.handle(ch, env)
    finally:
        socket.socket = orig_socket

    client_sock1.sendall(f"GET /capture?type=png&nonce={nonce} HTTP/1.1\r\n\r\n".encode("utf-8"))
    res1 = bytearray()
    while True:
        chunk = client_sock1.recv(4096)
        if not chunk:
            break
        res1.extend(chunk)
    client_sock1.close()
    assert b"HTTP/1.1 200 OK" in res1

    # 2. Wrong nonce is rejected with 403 even in dev mode
    client_sock2, srv_conn2 = socket.socketpair()
    _, MockSock2 = _mock_socket_factory(srv_conn2)
    socket.socket = MockSock2
    try:
        env = {
            "v": 1,
            "type": "img.arm_preview",
            "id": 104,
            "payload": {"nonce": nonce}
        }
        obs_mycro.handle(ch, env)
    finally:
        socket.socket = orig_socket

    client_sock2.sendall(b"GET /capture?type=png&nonce=wrong_nonce HTTP/1.1\r\n\r\n")
    res2 = bytearray()
    while True:
        chunk = client_sock2.recv(4096)
        if not chunk:
            break
        res2.extend(chunk)
    client_sock2.close()
    assert b"HTTP/1.1 403 Forbidden" in res2


def test_obs_arm_preview_path_and_type_enforcement():
    obs_mycro.device_token = ""  # dev mode
    ch = MockChannel()
    nonce = os.urandom(16).hex()
    dummy_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    obs_mycro.capture_png = lambda: (dummy_png, 1920, 1080)

    # 1. Wrong path is rejected with 403
    client_sock1, srv_conn1 = socket.socketpair()
    orig_socket, MockSock1 = _mock_socket_factory(srv_conn1)
    socket.socket = MockSock1
    try:
        obs_mycro.handle(ch, {"v": 1, "type": "img.arm_preview", "id": 105, "payload": {"nonce": nonce}})
    finally:
        socket.socket = orig_socket

    client_sock1.sendall(f"GET /wrong?type=png&nonce={nonce} HTTP/1.1\r\n\r\n".encode("utf-8"))
    res1 = bytearray()
    while True:
        chunk = client_sock1.recv(4096)
        if not chunk:
            break
        res1.extend(chunk)
    client_sock1.close()
    assert b"HTTP/1.1 403 Forbidden" in res1

    # 2. Wrong type is rejected with 403
    client_sock2, srv_conn2 = socket.socketpair()
    _, MockSock2 = _mock_socket_factory(srv_conn2)
    socket.socket = MockSock2
    try:
        obs_mycro.handle(ch, {"v": 1, "type": "img.arm_preview", "id": 106, "payload": {"nonce": nonce}})
    finally:
        socket.socket = orig_socket

    client_sock2.sendall(f"GET /capture?type=jpeg&nonce={nonce} HTTP/1.1\r\n\r\n".encode("utf-8"))
    res2 = bytearray()
    while True:
        chunk = client_sock2.recv(4096)
        if not chunk:
            break
        res2.extend(chunk)
    client_sock2.close()
    assert b"HTTP/1.1 403 Forbidden" in res2


if __name__ == "__main__":
    test_obs_unknown_type_logs_warning_and_replies_error()
    test_obs_arm_preview_happy_path_and_rejection()
    test_obs_arm_preview_dev_mode()
    test_obs_arm_preview_path_and_type_enforcement()
    print("ALL OBS PROVIDER UNIT TESTS PASSED SUCCESSFULLY!")
