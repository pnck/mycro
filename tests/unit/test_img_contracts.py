"""Host-side contract tests for the real CircuitPython image entry points.

Hardware modules are stubbed only at import time; all image logic under test
comes directly from src/code.py.
"""

import asyncio
import importlib.util
import os
import struct
import sys
import types

import pytest

from bytecode import ERR_HANDLE_MISUSE, REG_ERR, REG_RET

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)


def _load_device_module():
    class StubServer:
        def __init__(self, pool, debug=False):
            self.pool = pool

        def route(self, *args, **kwargs):
            return lambda fn: fn

    class StubSocketPool:
        AF_INET = 2
        SOCK_STREAM = 1
        SOL_SOCKET = 1
        SO_REUSEADDR = 2

        def __init__(self, radio):
            self.radio = radio

    class StubHID:
        pass

    socketpool = types.ModuleType("socketpool")
    socketpool.SocketPool = StubSocketPool
    wifi = types.ModuleType("wifi")
    wifi.radio = object()
    httpserver = types.ModuleType("adafruit_httpserver")
    httpserver.Request = type("Request", (), {})
    httpserver.Response = type("Response", (), {})
    httpserver.FileResponse = type("FileResponse", (), {})
    httpserver.Server = StubServer
    hid_adafruit = types.ModuleType("lib.hid_adafruit")
    hid_adafruit.AdafruitHIDProvider = StubHID

    stubs = {
        "socketpool": socketpool,
        "wifi": wifi,
        "adafruit_httpserver": httpserver,
        "lib.hid_adafruit": hid_adafruit,
    }
    old_modules = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)

    # Import the real entry point without starting its permanent main loop.
    real_run = asyncio.run
    asyncio.run = lambda coro: coro.close()
    try:
        path = os.path.join(SRC_ROOT, "code.py")
        spec = importlib.util.spec_from_file_location("mycro_device_under_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        asyncio.run = real_run
        for name, old in old_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module


device = _load_device_module()


@pytest.fixture(autouse=True)
def reset_device_state():
    device.runtime.release_handles()
    device.runtime.clear_consts()
    device._img_client = None
    device._img_pending = None
    yield
    device.runtime.release_handles()
    device.runtime.clear_consts()
    device._img_client = None
    device._img_pending = None


def _run(coroutine):
    return asyncio.run(coroutine)


def _make_handle(w, h, rgb, register):
    hid = device.runtime.new_handle("snap", {"w": w, "h": h, "rgb": rgb})
    assert hid > 0
    device.runtime.reg_write(register, hid, handle=True)
    device.runtime.claim(register, hid)
    return hid


class FakeClient:
    def __init__(self):
        self.alive = True
        self.authed = True
        self.addr = ("127.0.0.1", 12345)
        self.sent = []
        self.img_stream_seen = False

    def enqueue(self, frame):
        self.sent.append(frame)


class FakeSocket:
    def __init__(self, data=b""):
        self.data = data
        self.sent = bytearray()

    def recv_into(self, view):
        if not self.data:
            return None
        n = min(len(view), len(self.data))
        view[:n] = self.data[:n]
        self.data = self.data[n:]
        return n

    def send(self, data):
        self.sent.extend(data)
        return len(data)


async def _stream_result(total, chunk_count, wire_chunks, client=None, wire_sid=17):
    client = client or FakeClient()
    device._img_client = client
    task = asyncio.create_task(device._img_request("img.frame", {}))
    # Let the real request function publish its pending entry.
    await asyncio.sleep(0)
    ent = device._img_pending
    assert ent is not None
    device._img_resolve(client, {
        "type": "img.frame.ok",
        "v": device.codec.PROTO_VERSION, "ts": 0, "id": ent["id"],
        "payload": {
            "stream_id": 17,
            "chunks": chunk_count,
            "bytes": total,
        },
    })
    for flags, data in wire_chunks:
        payload = struct.pack("<HH", wire_sid, flags) + data
        assert device._img_chunk(client, payload)
    return await task


def test_img_timeout_uses_profile_value():
    assert device.IMG_TIMEOUT_S == 5


@pytest.mark.parametrize("code, expected", [
    ("no_provider", device.ERR_NO_PROVIDER),
    ("provider_lost", device.ERR_NO_PROVIDER),
    ("timeout", device.ERR_TIMEOUT),
    ("field", device.ERR_INVALID_ARG),
    ("out_of_bounds", device.ERR_OUT_OF_BOUNDS),
    ("resource_exhausted", device.ERR_GENERIC),
    ("capture_failed", device.ERR_GENERIC),
    ("unknown_provider_error", device.ERR_GENERIC),
])
def test_snap_maps_provider_errors(monkeypatch, code, expected):
    async def fail_request(type_, payload):
        raise device.ImgError(code)

    monkeypatch.setattr(device, "_img_request", fail_request)
    assert _run(device._img_snap(0, 0, 1, 1)) == (0, expected)


@pytest.mark.parametrize("args", [(-1, 0, 1, 1), (0, -1, 1, 1),
                                  (0, 0, 0, 1), (0, 0, 1, 129)])
def test_snap_argument_failures(args):
    assert _run(device._img_snap(*args)) == (0, device.ERR_INVALID_ARG)


def test_snap_success_is_plain_handle_and_exact_dimensions(monkeypatch):
    async def response(type_, payload):
        return {"w": 2, "h": 1, "width": 2, "height": 1,
                "format": "rgb8"}, b"\x12\x34\x56\xab\xcd\xef"

    monkeypatch.setattr(device, "_img_request", response)
    hid = _run(device._img_snap(0, 0, 2, 1))
    assert isinstance(hid, int) and hid > 0
    data = device.runtime._handles[hid]["data"]
    assert (data["w"], data["h"]) == (2, 1)
    assert bytes(data["rgb"]) == b"\x12\x34\x56\xab\xcd\xef"


def test_snap_rejects_provider_clipping(monkeypatch):
    async def clipped(type_, payload):
        return {"w": 1, "h": 1, "width": 2, "height": 1,
                "format": "rgb8"}, b"\x12\x34\x56"

    monkeypatch.setattr(device, "_img_request", clipped)
    assert _run(device._img_snap(0, 0, 2, 1)) == (0, device.ERR_GENERIC)


@pytest.mark.parametrize("metadata", [
    {"w": 2, "h": 1, "height": 1, "format": "rgb8"},
    {"w": 2, "h": 1, "width": 2, "format": "rgb8"},
    {"w": 2, "h": 1, "width": 2, "height": 1, "format": "gray8"},
    {"w": 2, "h": 1, "width": 1, "height": 1, "format": "rgb8"},
])
def test_snap_rejects_invalid_source_metadata(monkeypatch, metadata):
    async def response(type_, payload):
        return metadata, b"\x00" * 6

    monkeypatch.setattr(device, "_img_request", response)
    assert _run(device._img_snap(0, 0, 2, 1)) == (0, device.ERR_GENERIC)


def test_snap_pool_and_handle_exhaustion(monkeypatch):
    async def response(type_, payload):
        return {"w": 1, "h": 1, "width": 1, "height": 1,
                "format": "rgb8"}, b"\x00\x00\x00"

    monkeypatch.setattr(device, "_img_request", response)
    monkeypatch.setattr(device.runtime.snap_pool, "alloc", lambda: None)
    assert _run(device._img_snap(0, 0, 1, 1)) == (0, device.ERR_RESOURCE_EXHAUSTED)


def test_snap_handle_exhaustion_releases_reserved_pool_slot(monkeypatch):
    async def response(type_, payload):
        return {"w": 1, "h": 1, "width": 1, "height": 1,
                "format": "rgb8"}, b"\x00\x00\x00"

    monkeypatch.setattr(device, "_img_request", response)
    monkeypatch.setattr(device.runtime, "_handle_seq", 2147483647)
    assert _run(device._img_snap(0, 0, 1, 1)) == (0, device.ERR_RESOURCE_EXHAUSTED)
    assert device.runtime.snap_pool._free == [0]


def test_sys_const_results_and_exhaustion(monkeypatch):
    assert device._sys_const(0) == (0, device.ERR_INVALID_ARG)
    cid = device.runtime.add_const("snap", {"w": 1, "h": 1})
    hid = device._sys_const(cid)
    assert isinstance(hid, int) and hid > 0
    monkeypatch.setattr(device.runtime, "_handle_seq", 2147483647)
    assert device._sys_const(cid) == (0, device.ERR_RESOURCE_EXHAUSTED)


def test_at_and_diff_results():
    a = _make_handle(2, 1, b"\x12\x34\x56\xab\xcd\xef", 0)
    b = _make_handle(2, 1, b"\x12\x34\x56\xaa\xbb\xcc", 1)
    assert device._img_at(a, 1, 0) == 0xABCDEF
    assert device._img_at(9999, 0, 0) == (0, device.ERR_HANDLE_INVALID)
    assert device._img_at(a, 2, 0) == (0, device.ERR_OUT_OF_BOUNDS)
    assert device._img_diff(a, b, 0) == 1
    assert device._img_diff(a, 9999, 0) == (0, device.ERR_HANDLE_INVALID)
    c = _make_handle(1, 1, b"\x00\x00\x00", 2)
    assert device._img_diff(a, c, 0) == (0, device.ERR_INVALID_ARG)


def test_call_ext_rejects_handle_in_scalar_parameter():
    source = (r"\use{img}\pixelrect{p}{\row{0}}"
              r"\call{img.diff}{$p}{$p}{$ret}")
    bc, err = device.macro.compile(source)
    assert err is None
    _run(device.macro.execute(bc, default_delay_ms=0, click_hold_ms=0))
    assert device.macro.last_regs[REG_RET] == 0
    assert device.macro.last_regs[REG_ERR] == ERR_HANDLE_MISUSE


def test_stream_completes_on_declared_counts():
    payload, stream = _run(_stream_result(6, 2, [(0, b"abc"), (0, b"def")]))
    assert payload["bytes"] == 6
    assert bytes(stream) == b"abcdef"


@pytest.mark.parametrize("total, chunk_count, wire_chunks, error", [
    (6, 1, [(0, b"abc")], "stream_incomplete"),
    (3, 1, [(0, b"abcd")], "stream_overrun"),
    (3, 1, [(0, b"abc"), (0, b"")], "stream_overrun"),
    (3, 1, [(1, b"abc")], "stream_flags"),
])
def test_stream_rejects_mismatch_and_closes_connection(
        total, chunk_count, wire_chunks, error):
    client = FakeClient()
    with pytest.raises(device.ImgError, match=error):
        _run(_stream_result(total, chunk_count, wire_chunks, client=client))
    assert not client.alive


def test_stream_rejects_unknown_id_and_closes_connection():
    client = FakeClient()
    with pytest.raises(device.ImgError, match="stream_id"):
        _run(_stream_result(3, 1, [(0, b"abc")], client=client, wire_sid=18))
    assert not client.alive


def test_duplicate_stream_begin_is_a_protocol_error():
    client = FakeClient()

    async def scenario():
        device._img_client = client
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        response = {"type": "img.frame.ok", "v": device.codec.PROTO_VERSION, "ts": 0, "id": ent["id"],
                    "payload": {"stream_id": 17, "chunks": 2, "bytes": 6}}
        device._img_resolve(client, response)
        device._img_chunk(client, struct.pack("<HH", 17, 0) + b"abc")
        device._img_resolve(client, response)
        await task

    with pytest.raises(device.ImgError, match="stream_overrun"):
        _run(scenario())
    assert not client.alive


def test_unexpected_response_type_does_not_complete_request():
    client = FakeClient()

    async def scenario():
        device._img_client = client
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        device._img_resolve(client, {
            "type": "macro.status.ok", "v": device.codec.PROTO_VERSION, "ts": 0, "id": ent["id"],
            "payload": {"stream_id": 17, "chunks": 1, "bytes": 3},
        })
        assert not ent["event"].is_set() and ent["stream"] is None
        device._img_resolve(client, {
            "type": "error", "v": device.codec.PROTO_VERSION, "ts": 0, "id": ent["id"],
            "payload": {"code": "field", "message": "stop"},
        })
        await task

    with pytest.raises(device.ImgError, match="field"):
        _run(scenario())
    assert client.alive


def test_stream_buffer_allocation_failure_is_resource_exhaustion(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(device, "_stream_buf", None)

    def fail_alloc(size):
        raise MemoryError

    monkeypatch.setattr(device, "bytearray", fail_alloc, raising=False)

    async def scenario():
        device._img_client = client
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        device._img_resolve(client, {
            "type": "img.frame.ok", "v": device.codec.PROTO_VERSION, "ts": 0, "id": ent["id"],
            "payload": {"stream_id": 17, "chunks": 1, "bytes": 3},
        })
        await task

    with pytest.raises(device.ImgError, match="local_resource_exhausted"):
        _run(scenario())
    assert client.alive


def test_provider_drop_preserves_success_and_specific_errors():
    async def successful_then_eof():
        client = FakeClient()
        device._img_client = client
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        device._img_resolve(client, {
            "type": "img.frame.ok", "v": 1, "ts": 0, "id": ent["id"],
            "payload": {"width": 1, "height": 1},
        })
        device._img_drop(client)
        return await task

    payload, stream = _run(successful_then_eof())
    assert payload == {"width": 1, "height": 1} and stream is None

    client = FakeClient()

    async def protocol_error_then_eof():
        device._img_client = client
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        device._img_resolve(client, {
            "type": "img.frame.ok", "v": 1, "ts": 0, "id": ent["id"],
            "payload": {"stream_id": 17, "chunks": 1, "bytes": 3},
        })
        device._img_chunk(client, struct.pack("<HH", 17, 1) + b"abc")
        device._img_drop(client)
        await task

    with pytest.raises(device.ImgError, match="stream_flags"):
        _run(protocol_error_then_eof())


def test_error_payload_and_early_or_late_chunks_close_cleanly():
    client = FakeClient()

    async def malformed_error():
        device._img_client = client
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        device._img_resolve(client, {
            "type": "error", "v": 1, "ts": 0, "id": ent["id"],
            "payload": None,
        })
        await task

    with pytest.raises(device.ImgError, match="bad_payload"):
        _run(malformed_error())
    assert not client.alive

    client = FakeClient()
    _run(_stream_result(3, 1, [(0, b"abc")], client=client))
    assert device._img_chunk(client, struct.pack("<HH", 17, 0) + b"abc")
    assert not client.alive


def test_deadline_rechecks_completion_and_partial_timeout_closes(monkeypatch):
    async def race(completed):
        client = FakeClient()
        device._img_client = client

        async def expire(awaitable, timeout):
            awaitable.close()
            ent = device._img_pending
            device._img_resolve(client, {
                "type": "img.frame.ok", "v": 1, "ts": 0, "id": ent["id"],
                "payload": {"width": 1, "height": 1} if completed else
                           {"stream_id": 17, "chunks": 1, "bytes": 3},
            })
            raise asyncio.TimeoutError

        monkeypatch.setattr(device.asyncio, "wait_for", expire)
        if completed:
            payload, _ = await device._img_request("img.frame", {})
            assert payload == {"width": 1, "height": 1}
            assert client.alive
        else:
            with pytest.raises(device.ImgError, match="timeout"):
                await device._img_request("img.frame", {})
            assert not client.alive

    _run(race(True))
    _run(race(False))


def test_same_id_from_another_client_uses_normal_dispatcher():
    provider = FakeClient()

    async def scenario():
        device._img_client = provider
        task = asyncio.create_task(device._img_request("img.frame", {}))
        await asyncio.sleep(0)
        ent = device._img_pending
        env = {"v": 1, "type": "macro.status", "id": ent["id"],
               "ts": 0, "payload": {}}
        wire = device.codec.pack_frame(
            device.codec.KIND_JSON, device.codec.encode_envelope(env))
        other = device._Client(FakeSocket(wire), ("127.0.0.1", 9999))
        other.authed = True
        device._service(other, device._now_ms())
        assert other.conn.sent
        response = device.codec.decode_envelope(
            other.conn.sent[4:])
        assert response["type"] == "macro.status.ok"
        assert not ent["event"].is_set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    _run(scenario())
