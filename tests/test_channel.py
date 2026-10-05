"""End-to-end channel tests: tools/client.py against a loopback server
that mirrors code.py's raw-TCP service loop (auth prologue → Reassembler
→ Dispatcher → framed response). Real sockets, real framing."""

import importlib.util
import socket
import threading

import pytest

import codec
import proto
from runtime import Runtime

_spec = importlib.util.spec_from_file_location("client", "tools/client.py")
client_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(client_mod)

TOKEN = "test-token"


class FakeCtx:
    def __init__(self):
        self.runtime = Runtime()
        self.runtime.register_signal("net.msg")
        self.submitted = []

    def submit(self, code):
        self.submitted.append(code)
        return 26, None

    def status(self):
        return ("idle", None)

    def abort(self):
        return False

    def device_info(self):
        return {"board": "testboard"}


def _serve_once(listener, ctx):
    """Accept one connection and service it until close (blocking)."""
    conn, _ = listener.accept()
    conn.settimeout(5)
    try:
        buf = bytearray()
        while len(buf) < codec.AUTH_LEN:
            chunk = conn.recv(codec.AUTH_LEN - len(buf))
            if not chunk:
                return
            buf += chunk
        if not codec.check_auth_header(bytes(buf), TOKEN):
            return  # device behavior: close silently
        reasm = codec.Reassembler()
        disp = proto.Dispatcher()
        while True:
            data = conn.recv(4096)
            if not data:
                return
            for kind, payload in reasm.feed(data):
                if kind != codec.KIND_JSON:
                    resp = proto.error_response(
                        None, "unsupported_kind", "no binary consumer registered")
                else:
                    try:
                        env = codec.decode_envelope(payload)
                    except codec.CodecError as e:
                        resp = proto.error_response(None, "bad_envelope", str(e))
                    else:
                        resp = disp.dispatch(env, ctx)
                if resp is not None:
                    conn.sendall(codec.pack_frame(
                        codec.KIND_JSON, codec.encode_envelope(resp)))
    finally:
        conn.close()


@pytest.fixture()
def server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    ctx = FakeCtx()
    t = threading.Thread(target=_serve_once, args=(listener, ctx), daemon=True)
    t.start()
    yield listener.getsockname()[1], ctx
    listener.close()


def test_status_roundtrip(server):
    port, _ = server
    c = client_mod.Client("127.0.0.1", port, TOKEN)
    r = c.call("macro.status")
    assert r["type"] == "macro.status.ok"
    assert r["payload"]["state"] == "idle"
    c.close()


def test_submit_roundtrip(server):
    port, ctx = server
    c = client_mod.Client("127.0.0.1", port, TOKEN)
    r = c.call("macro.submit", {"code": "hello\\enter"})
    assert r["type"] == "macro.submit.ok" and r["payload"]["bytes"] == 26
    assert ctx.submitted == ["hello\\enter"]
    c.close()


def test_event_push_fires_signal(server):
    port, ctx = server
    c = client_mod.Client("127.0.0.1", port, TOKEN)
    r = c.call("event.push", {"name": "net.msg", "fields": {"x": 120}})
    assert r["type"] == "event.push.ok"
    assert ctx.runtime.read_slot("net.msg", "x") == 120
    c.close()


def test_error_echoes_id(server):
    port, _ = server
    c = client_mod.Client("127.0.0.1", port, TOKEN)
    r = c.call("no.such")
    assert r["type"] == "error"
    assert r["payload"]["code"] == "unknown_type"
    assert r["id"] == c._req_id
    c.close()


def test_wrong_token_closes_silently(server):
    port, _ = server
    c = client_mod.Client("127.0.0.1", port, "wrong-token")
    with pytest.raises(client_mod.ProtocolError):
        c.call("macro.status")
    c.close()


def test_binary_frame_rejected(server):
    port, _ = server
    c = client_mod.Client("127.0.0.1", port, TOKEN)
    c.sock.sendall(codec.pack_frame(codec.KIND_BINARY, b"\x00\x00raw"))
    r = c.recv_envelope()
    assert r["payload"]["code"] == "unsupported_kind"
    c.close()
