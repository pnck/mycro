"""Unit tests for MYCRO Host Services (network, mDNS, HTTP redirect, raw port)."""

import os
import pytest

from services.network import normalize_hostname
from services.raw import get_raw_port
from services.http import urldecode, HttpServer


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
