"""Stage 2 dispatcher tests: envelope validation, standard handlers,
error correlation. Uses a fake ctx; runtime registry is the real one."""

import pytest

import asyncio

from proto import Dispatcher, error_response, push, response, validate_envelope
from runtime import Runtime


class FakeCtx:
    def __init__(self):
        self.runtime = Runtime()
        self.runtime.register_signal("net.msg")
        self.submitted = []
        self.fail_compile = False
        self.state = ("idle", None)
        self.aborted = False

    def submit(self, code):
        self.submitted.append(code)
        if self.fail_compile:
            return None, "line 1: bad syntax"
        return 42, None

    def status(self):
        return self.state

    def abort(self):
        was = self.state[0] == "running"
        self.aborted = True
        return was

    def device_info(self):
        return {"board": "testboard", "fw": "10.3.0"}


def req(type_, payload=None, id_=7):
    return {"v": 1, "type": type_, "id": id_, "ts": 0,
            "payload": payload if payload is not None else {}}


def dispatch(ctx, type_, payload=None, id_=7):
    return Dispatcher().dispatch(req(type_, payload, id_), ctx)


# --- envelope validation ---

@pytest.mark.parametrize("env,code", [
    ({"v": 2, "type": "t", "id": 1}, "bad_version"),
    ({"v": 1, "id": 1}, "bad_type"),
    ({"v": 1, "type": "", "id": 1}, "bad_type"),
    ({"v": 1, "type": "t"}, "bad_id"),
    ({"v": 1, "type": "t", "id": True}, "bad_id"),
    ({"v": 1, "type": "t", "id": -1}, "bad_id"),
    ({"v": 1, "type": "t", "id": 2**32}, "bad_id"),
    ({"v": 1, "type": "t", "id": 1, "ts": "now"}, "bad_ts"),
    ({"v": 1, "type": "t", "id": 1, "payload": [1]}, "bad_payload"),
])
def test_validate_rejects(env, code):
    assert validate_envelope(env)[0] == code


def test_validate_accepts_minimal():
    assert validate_envelope({"v": 1, "type": "t", "id": 0}) is None


def test_dispatch_unknown_type():
    r = dispatch(FakeCtx(), "no.such")
    assert r["type"] == "error" and r["payload"]["code"] == "unknown_type"
    assert r["id"] == 7


def test_validation_error_echoes_id_when_parseable():
    r = Dispatcher().dispatch({"v": 2, "type": "x", "id": 9}, FakeCtx())
    assert r["type"] == "error" and r["id"] == 9


def test_handler_exception_becomes_internal_error():
    class Boom(FakeCtx):
        def status(self):
            raise RuntimeError("boom")

    r = dispatch(Boom(), "macro.status")
    assert r["payload"]["code"] == "internal"
    assert "boom" in r["payload"]["message"]
    assert r["id"] == 7


# --- standard handlers ---

def test_submit_ok():
    ctx = FakeCtx()
    r = dispatch(ctx, "macro.submit", {"code": "hello"})
    assert r["type"] == "macro.submit.ok" and r["payload"]["bytes"] == 42
    assert ctx.submitted == ["hello"]


def test_submit_compile_error():
    ctx = FakeCtx()
    ctx.fail_compile = True
    r = dispatch(ctx, "macro.submit", {"code": "bad"})
    assert r["payload"]["code"] == "compile_error"
    assert "bad syntax" in r["payload"]["message"]


def test_submit_requires_code():
    assert dispatch(FakeCtx(), "macro.submit", {})["payload"]["code"] == "field"
    assert dispatch(FakeCtx(), "macro.submit", {"code": 5})["payload"]["code"] == "field"


def test_submit_busy_is_state_error():
    ctx = FakeCtx()
    ctx.state = ("running", None)
    r = dispatch(ctx, "macro.submit", {"code": "hello"})
    assert r["payload"]["code"] == "state"
    assert ctx.submitted == []


def test_status_with_detail():
    ctx = FakeCtx()
    ctx.state = ("error", "pystack exhausted")
    r = dispatch(ctx, "macro.status")
    assert r["payload"] == {"state": "error", "detail": "pystack exhausted"}


def test_status_without_detail_omits_key():
    assert dispatch(FakeCtx(), "macro.status")["payload"] == {"state": "idle"}


def test_abort_when_running():
    ctx = FakeCtx()
    ctx.state = ("running", None)
    assert dispatch(ctx, "macro.abort")["type"] == "macro.abort.ok"
    assert ctx.aborted


def test_abort_when_idle_is_state_error():
    r = dispatch(FakeCtx(), "macro.abort")
    assert r["payload"]["code"] == "state"


def test_device_info_carries_proto_version():
    r = dispatch(FakeCtx(), "device.info")
    assert r["payload"] == {"board": "testboard", "fw": "10.3.0", "proto": 1}


def test_event_push_fires_signal():
    ctx = FakeCtx()
    r = dispatch(ctx, "event.push", {"name": "net.msg", "fields": {"x": 120, "y": -35}})
    assert r["type"] == "event.push.ok"
    assert ctx.runtime.read_slot("net.msg", "x") == 120
    assert ctx.runtime.read_slot("net.msg", "y") == -35


def test_event_push_rejects_unknown_signal():
    r = dispatch(FakeCtx(), "event.push", {"name": "nope.sig"})
    assert r["payload"]["code"] == "unknown_signal"


@pytest.mark.parametrize("fields", [
    {"x": "str"},
    {"x": True},
    {"x": 2**31},
    ["not", "dict"],
])
def test_event_push_rejects_bad_fields(fields):
    r = dispatch(FakeCtx(), "event.push", {"name": "net.msg", "fields": fields})
    assert r["payload"]["code"] == "field"


# --- response helpers ---

def test_response_echoes_request_id():
    assert response(req("t", id_=99), "t.ok")["id"] == 99


def test_push_uses_id_zero():
    p = push("macro.result", {"state": "done"})
    assert p["id"] == 0 and p["v"] == 1


def test_error_response_tolerates_garbage_req():
    r = error_response({"id": "not-an-int"}, "c", "m")
    assert r["id"] == 0
    r = error_response(None, "c", "m")
    assert r["id"] == 0


def test_event_push_actually_wakes_waiter():
    ctx = FakeCtx()

    async def scenario():
        task = asyncio.create_task(ctx.runtime.wait_signal("net.msg", 1000))
        await asyncio.sleep(0)  # let the waiter clear any stale latch + park
        dispatch(ctx, "event.push", {"name": "net.msg", "fields": {"x": 1}})
        return await task

    assert asyncio.run(scenario())
