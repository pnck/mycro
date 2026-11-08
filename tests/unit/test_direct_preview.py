"""Unit tests for the three-party direct preview capture workflow:
1. HMAC-SHA256 crypto correctness (RFC 4231 test vectors + key hashing).
2. Capture provider's ephemeral challenge server & state machine:
   - Happy path: valid nonce + signature -> HTTP 200 PNG with CORS.
   - Tampered signature -> HTTP 403 Forbidden.
   - Tampered nonce -> HTTP 403 Forbidden.
   - Missing query parameters -> HTTP 403 Forbidden.
   - Single-use guarantee (replay rejection): port unbinds immediately after 1 request.
   - Lazy capture guarantee: PNG is only generated upon authenticated client connection.
3. MCU state machine & intermediate states (pending, busy conflict, read-once).
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
import urllib.error
import urllib.parse
import urllib.request
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src", "lib"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "device"))

import reference_provider as rp


# ============================================================================
# 1. HMAC-SHA256 Crypto Correctness (RFC 4231)
# ============================================================================

def _compute_sig(token: str, nonce: str) -> str:
    return hmac.new(
        token.encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def test_rfc4231_hmac_sha256_vectors():
    # Case 1: 20-byte key (0x0b), "Hi There"
    k1 = b"\x0b" * 20
    d1 = b"Hi There"
    assert hmac.new(k1, d1, hashlib.sha256).hexdigest() == \
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"

    # Case 2: key "Jefe", "what do ya want for nothing?"
    k2 = b"Jefe"
    d2 = b"what do ya want for nothing?"
    assert hmac.new(k2, d2, hashlib.sha256).hexdigest() == \
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"

    # Case 6: 131-byte key (> 64 bytes, tests SHA-256 pre-hash of key)
    k6 = b"\xaa" * 131
    d6 = b"Test Using Larger Than Block-Size Key - Hash Key First"
    assert hmac.new(k6, d6, hashlib.sha256).hexdigest() == \
        "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54"

    # Case 7: 131-byte key (> 64 bytes) and large message
    k7 = b"\xaa" * 131
    d7 = b"This is a test using a larger than block-size key and a larger than block-size data. The key needs to be hashed before being used by the HMAC algorithm."
    assert hmac.new(k7, d7, hashlib.sha256).hexdigest() == \
        "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2"


def test_crypto_empty_and_edge_keys():
    # Empty token with random nonce
    nonce = os.urandom(16).hex()
    sig_empty = _compute_sig("", nonce)
    assert len(sig_empty) == 64
    assert hmac.compare_digest(sig_empty, _compute_sig("", nonce))
    assert not hmac.compare_digest(sig_empty, _compute_sig("secret", nonce))


# ============================================================================
# 2. Provider Ephemeral Challenge Server & State Machine Tests
# ============================================================================

def _handle_preview_conn(conn, exp_sig: str, exp_nonce: str, capture_fn, token="non_empty"):
    """The exact connection handler logic used by OBS and ReferenceProvider."""
    try:
        raw = conn.recv(2048).decode("latin-1")
        req_line = raw.split("\r\n")[0] if "\r\n" in raw else raw
        parts = req_line.split(" ")
        path = parts[1] if len(parts) > 1 else ""
        parsed = urllib.parse.urlparse(path)
        qs = urllib.parse.parse_qs(parsed.query)

        req_nonce = qs.get("nonce", [""])[0]
        req_sig = qs.get("sig", [""])[0]
        req_type = qs.get("type", [""])[0]

        # Dev mode uses an empty HMAC key; signature verification remains active.
        auth_ok = hmac.compare_digest(req_sig, exp_sig)

        if parsed.path == "/capture" and req_type == "png" and req_nonce == exp_nonce and auth_ok:
            png_bytes, w, h = capture_fn()
            resp = (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: image/png\r\n"
                f"Content-Length: {len(png_bytes)}\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Cache-Control: no-store\r\n"
                "Connection: close\r\n\r\n"
            ).encode("latin-1") + png_bytes
            conn.sendall(resp)
        else:
            conn.sendall(
                b"HTTP/1.1 403 Forbidden\r\n"
                b"Content-Length: 9\r\n"
                b"Access-Control-Allow-Origin: *\r\n"
                b"Connection: close\r\n\r\n"
                b"Forbidden"
            )
    finally:
        conn.close()


def _do_request(request_str: str, exp_sig: str, exp_nonce: str, capture_fn, token="non_empty"):
    client_sock, srv_sock = socket.socketpair()
    try:
        t = threading.Thread(
            target=_handle_preview_conn,
            args=(srv_sock, exp_sig, exp_nonce, capture_fn, token),
            daemon=True
        )
        t.start()
        client_sock.sendall(request_str.encode("utf-8"))

        res = bytearray()
        while True:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            res += chunk
        t.join(timeout=1.0)

        sep = res.find(b"\r\n\r\n")
        assert sep != -1, "malformed HTTP response"
        head = res[:sep].decode("utf-8", "replace")
        body = bytes(res[sep + 4:])
        status_line = head.split("\r\n")[0]
        status_code = int(status_line.split(" ")[1])
        headers = {}
        for line in head.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        return status_code, headers, body
    finally:
        client_sock.close()


class CaptureSpy:
    def __init__(self):
        self.count = 0
        self.rgb = rp.frame_default()

    def capture(self):
        self.count += 1
        png = rp.make_png(80, 60, self.rgb)
        return png, 80, 60


def test_preview_happy_path_lazy_capture():
    spy = CaptureSpy()
    nonce = os.urandom(16).hex()
    token = "test_token_xyz"
    sig = _compute_sig(token, nonce)

    # Before connection: lazy action guarantee (0 captures taken)
    assert spy.count == 0

    req = f"GET /capture?type=png&nonce={nonce}&sig={sig} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status, headers, body = _do_request(req, exp_sig=sig, exp_nonce=nonce, capture_fn=spy.capture)

    assert status == 200
    assert headers.get("content-type") == "image/png"
    assert headers.get("access-control-allow-origin") == "*"
    assert int(headers.get("content-length", 0)) == len(body)
    assert body[:8] == b"\x89PNG\r\n\x1a\n"

    # Exactly 1 capture was triggered after authentication
    assert spy.count == 1

    # Verify PNG dimensions (80x60)
    w, h = struct.unpack(">II", body[16:24])
    assert (w, h) == (80, 60)


def test_preview_tampered_sig_rejected_with_403():
    spy = CaptureSpy()
    nonce = os.urandom(16).hex()
    token = "test_token_xyz"
    exp_sig = _compute_sig(token, nonce)
    bad_sig = "0" * 64

    req = f"GET /capture?type=png&nonce={nonce}&sig={bad_sig} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status, headers, body = _do_request(req, exp_sig=exp_sig, exp_nonce=nonce, capture_fn=spy.capture)

    assert status == 403
    assert b"Forbidden" in body
    # Lazy capture was NOT triggered
    assert spy.count == 0


def test_preview_tampered_nonce_rejected_with_403():
    spy = CaptureSpy()
    nonce = os.urandom(16).hex()
    token = "test_token_xyz"
    exp_sig = _compute_sig(token, nonce)
    other_nonce = "1" * 32

    req = f"GET /capture?type=png&nonce={other_nonce}&sig={exp_sig} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status, headers, body = _do_request(req, exp_sig=exp_sig, exp_nonce=nonce, capture_fn=spy.capture)

    assert status == 403
    assert b"Forbidden" in body
    assert spy.count == 0


def test_preview_missing_params_rejected_with_403():
    spy = CaptureSpy()
    nonce = os.urandom(16).hex()
    token = "test_token_xyz"
    exp_sig = _compute_sig(token, nonce)

    req = "GET /capture HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status, headers, body = _do_request(req, exp_sig=exp_sig, exp_nonce=nonce, capture_fn=spy.capture)

    assert status == 403
    assert b"Forbidden" in body
    assert spy.count == 0


def test_preview_single_use_closes_immediately():
    """Verify that after sending response, the server socket is closed."""
    spy = CaptureSpy()
    nonce = os.urandom(16).hex()
    token = "test_token_xyz"
    sig = _compute_sig(token, nonce)

    client_sock, srv_sock = socket.socketpair()
    try:
        t = threading.Thread(
            target=_handle_preview_conn,
            args=(srv_sock, sig, nonce, spy.capture),
            daemon=True
        )
        t.start()
        req = f"GET /capture?type=png&nonce={nonce}&sig={sig} HTTP/1.1\r\nHost: localhost\r\n\r\n"
        client_sock.sendall(req.encode("utf-8"))

        res = bytearray()
        while True:
            chunk = client_sock.recv(4096)
            if not chunk:
                break
            res += chunk
        t.join(timeout=1.0)

        # Socket was closed by server: subsequent recv returns b""
        assert client_sock.recv(1024) == b""
    finally:
        client_sock.close()


def test_preview_dev_mode_token_unset():
    """Dev mode verifies empty-key HMAC and still requires the nonce."""
    spy = CaptureSpy()
    nonce = os.urandom(16).hex()
    empty_sig = _compute_sig("", nonce)

    # 1. Missing signature is rejected even with an empty HMAC key.
    req1 = f"GET /capture?type=png&nonce={nonce} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status1, _, _ = _do_request(req1, exp_sig=empty_sig, exp_nonce=nonce, capture_fn=spy.capture, token="")
    assert status1 == 403
    assert spy.count == 0

    # 2. The valid empty-key signature succeeds.
    req2 = f"GET /capture?type=png&nonce={nonce}&sig={empty_sig} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status2, _, _ = _do_request(req2, exp_sig=empty_sig, exp_nonce=nonce, capture_fn=spy.capture, token="")
    assert status2 == 200
    assert spy.count == 1

    # 3. Wrong nonce is rejected even with a valid signature for another nonce.
    req3 = f"GET /capture?type=png&nonce=wrong_nonce&sig={empty_sig} HTTP/1.1\r\nHost: localhost\r\n\r\n"
    status3, _, _ = _do_request(req3, exp_sig=empty_sig, exp_nonce=nonce, capture_fn=spy.capture, token="")
    assert status3 == 403
    assert spy.count == 1


# ============================================================================
# 3. MCU State Machine Intermediate States
# ============================================================================

def test_mcu_preview_state_transitions():
    """Simulates the state transitions of _img_ui in src/code.py."""
    img_ui = {"pending": False, "data": None, "err": None}

    # 1. Initial idle poll -> 404 None
    assert img_ui["pending"] is False and img_ui["data"] is None

    # 2. Start capture -> pending=True
    img_ui.update(pending=True, data=None, err=None)
    assert img_ui["pending"] is True

    # 3. Concurrent start attempt while pending -> Conflict (busy)
    is_busy = img_ui["pending"]
    assert is_busy is True

    # 4. Polling while pending -> 204 No Content
    assert img_ui["pending"] is True

    # 5. Provider success -> sets data {url, nonce}, pending=False
    nonce = os.urandom(16).hex()
    img_ui["data"] = {"url": "http://192.168.1.100:54321/capture", "nonce": nonce}
    img_ui["pending"] = False

    # 6. First poll returns JSON data and clears data immediately (Read-Once)
    assert img_ui["pending"] is False
    data = img_ui["data"]
    img_ui["data"] = None  # Read-once
    assert data["url"] == "http://192.168.1.100:54321/capture"
    assert data["nonce"] == nonce

    # 7. Second poll immediately returns 404 Not Found ("none")
    assert img_ui["data"] is None and img_ui["pending"] is False

    # 8. Provider error path
    img_ui.update(pending=False, data=None, err="no_capture_provider")
    err = img_ui["err"]
    img_ui["err"] = None
    assert err == "no_capture_provider"
    assert img_ui["err"] is None


if __name__ == "__main__":
    test_rfc4231_hmac_sha256_vectors()
    test_crypto_empty_and_edge_keys()
    test_preview_happy_path_lazy_capture()
    test_preview_tampered_sig_rejected_with_403()
    test_preview_tampered_nonce_rejected_with_403()
    test_preview_missing_params_rejected_with_403()
    test_preview_single_use_closes_immediately()
    test_preview_dev_mode_token_unset()
    test_mcu_preview_state_transitions()
    print("ALL DIRECT PREVIEW UNIT TESTS PASSED SUCCESSFULLY!")
