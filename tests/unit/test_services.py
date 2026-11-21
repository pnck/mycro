"""Unit tests for MYCRO Host Services (network, mDNS, HTTP redirect, raw port)."""

import os
import pytest

from services.network import normalize_hostname
from services.raw import get_raw_port
from services.http import urldecode, HttpServer, _macro_form


@pytest.mark.parametrize("raw, expected", [
    ("  Mycro_Lab-1.local.  ", "mycro-lab-1"),
    ("mycro.lan", "mycro-lan"),
    ("!!!", "mycro"),
    (None, "mycro"),
    ("a" * 100, "a" * 63),
])
def test_normalize_hostname(raw, expected):
    assert normalize_hostname(raw) == expected


def test_get_raw_port(monkeypatch):
    monkeypatch.delenv("MYCRO_RAW_PORT", raising=False)
    assert get_raw_port() == 7373

    monkeypatch.setenv("MYCRO_RAW_PORT", "8888")
    assert get_raw_port() == 8888

    # Invalid port inputs fallback to 7373
    monkeypatch.setenv("MYCRO_RAW_PORT", "not_a_number")
    assert get_raw_port() == 7373

    monkeypatch.setenv("MYCRO_RAW_PORT", "80")  # < 1024
    assert get_raw_port() == 7373

    monkeypatch.setenv("MYCRO_RAW_PORT", "70000")  # > 65535
    assert get_raw_port() == 7373


def test_http_urldecode():
    assert urldecode("hello+world") == "hello world"
    assert urldecode("foo%20bar") == "foo bar"
    assert urldecode("%23%21syntax%3A%20free") == "#!syntax: free"
    assert urldecode("plain_text_123") == "plain_text_123"
    assert urldecode("broken%2") == "broken%2"


def test_http_should_redirect():
    server = HttpServer(None, None, hostname="mycro")
    assert server.domain == "mycro.local"

    # Visiting via mDNS domain: NO redirect
    assert server._should_redirect("mycro.local", {}) is False
    assert server._should_redirect("mycro.local:80", {}) is False
    assert server._should_redirect("MYCRO.LOCAL", {}) is False

    # Visiting via raw IP: MUST redirect
    assert server._should_redirect("192.168.1.50", {}) is True
    assert server._should_redirect("192.168.1.50:80", {}) is True
    assert server._should_redirect("10.0.0.1", {}) is True

    # Visiting via other host: MUST redirect
    assert server._should_redirect("wrong-host.local", {}) is True

    # Failsafe bypass: ?ip=1 allows raw IP access
    assert server._should_redirect("192.168.1.50", {"ip": "1"}) is False
    assert server._should_redirect("192.168.1.50", {"ip": "1", "tab": "manual"}) is False

    # Non-bypass query parameter on raw IP: still redirects
    assert server._should_redirect("192.168.1.50", {"ip": "0"}) is True
    assert server._should_redirect("192.168.1.50", {"other": "param"}) is True

    # Missing host: no redirect
    assert server._should_redirect("", {}) is False
    assert server._should_redirect(None, {}) is False


def test_macro_form_hold_ms_parsing():
    class FakeRequest:
        def __init__(self, form):
            self.form_data = form

    # 1. Valid hold_ms
    c1, h1, err1 = _macro_form(FakeRequest({"code": "a", "hold_ms": "25"}))
    assert c1 == "a" and h1 == 25 and err1 is None

    # 2. Omitted hold_ms
    c2, h2, err2 = _macro_form(FakeRequest({"code": "a"}))
    assert c2 == "a" and h2 is None and err2 is None

    # 3. Invalid hold_ms (non-int, out of range)
    for invalid in ("bad", "-5", "70000"):
        _, h_inv, err_inv = _macro_form(FakeRequest({"code": "a", "hold_ms": invalid}))
        assert h_inv is None and err_inv is not None

    # 4. Empty code
    c4, h4, err4 = _macro_form(FakeRequest({"code": ""}))
    assert c4 is None


def test_macro_runner_and_proto_context():
    import asyncio
    from macro import Macro
    from mock_hid import MockHID
    from services.raw import ProtoContext
    from services.runner import MacroRunner

    async def _test():
        runner = MacroRunner(Macro(MockHID()))
        ctx = ProtoContext(None, runner)

        # Valid submissions: custom hold_ms and default fallback (zero state leakage)
        assert ctx.submit(r"\move{0,0}", hold_ms=50)[1] is None
        await runner.task
        assert ctx.submit(r"\move{0,0}")[1] is None  # defaults to 10, no leak
        await runner.task

        # Fast-fail invalid hold_ms
        assert ctx.submit(r"\move{0,0}", hold_ms=-1)[0] is None
        assert ctx.submit(r"\move{0,0}", hold_ms="abc")[0] is None

    asyncio.run(_test())


def test_wifi_retry_delay_only_between_attempts(monkeypatch):
    import asyncio
    import types
    from services import network as network_module

    class FailingRadio:
        def __init__(self):
            self.timeouts = []

        def connect(self, ssid, password, timeout=None):
            self.timeouts.append(timeout)
            raise OSError("offline")

    radio = FailingRadio()
    monkeypatch.setattr(network_module, "wifi", types.SimpleNamespace(radio=radio))
    sleeps = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(network_module.asyncio, "sleep", fake_sleep)
    connected, ip = asyncio.run(network_module.wifi_connect(
        "ssid", "password", attempts=3,
        attempt_timeout_s=0.4, retry_delay_s=0.2,
    ))
    assert not connected and ip is None
    assert radio.timeouts == [0.4, 0.4, 0.4]
    assert sleeps == [0.2, 0.2]


def test_raw_server_timing_parameters_are_injectable(monkeypatch):
    from services import raw as raw_module

    monkeypatch.setattr(raw_module.time, "monotonic", lambda: 100.0)
    client = raw_module.Client(object(), ("127.0.0.1", 1234), auth_timeout_s=0.25)
    assert client.deadline == 100.25

    server = raw_module.RawServer(
        None, None, None,
        auth_timeout_s=0.5, idle_poll_s=0.02,
    )
    assert server.auth_timeout_s == 0.5
    assert server.idle_poll_s == 0.02
