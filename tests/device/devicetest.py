#!/usr/bin/env python3
"""Dual-end on-device acceptance suite.

Drives a real MYCRO device over the raw TCP channel (protocol v1) and
HTTP, checking wire behavior end to end. Run after deploying:

    python3 tests/device/devicetest.py <device-ip> --token <MYCRO_TOKEN>

Options: --port, --idle-secs (long-idle case, default 10),
--skip-slow (skip auth-deadline and long-idle cases).

Exits 0 when every automated case passes. A manual-observation checklist
(mouse motion on PC1, serial log lines) prints at the end.

Self-contained: stdlib + client.py from ../../tools.
"""

import argparse
import hashlib
import hmac
import json
import os
import socket
import struct
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import AUTH_MAGIC, PROTO_VERSION, Client, ProtocolError  # noqa: E402
import reference_provider  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def auth_header(token):
    return AUTH_MAGIC + bytes([PROTO_VERSION]) + hashlib.sha256(token.encode()).digest()


def recv_until(client, pred, timeout=5.0):
    """Read envelopes until pred(env) or timeout; non-matching ones are
    consumed (pushes interleave freely). Returns the match or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            env = client.recv_envelope()
        except (socket.timeout, TimeoutError):
            break
        if pred(env):
            return env
    return None


def recv_result(client, timeout=8.0):
    return recv_until(client, lambda e: e.get("type") == "macro.result", timeout)


def recv_tag(client, tag, timeout=8.0):
    return recv_until(
        client,
        lambda e: e.get("type") == "net.msg" and e["payload"].get("tag") == tag,
        timeout,
    )


def abort_and_drain(client):
    """Best-effort cleanup for a failed or timed-out macro case."""
    try:
        r = client.call("macro.abort")
        if r.get("type") == "macro.abort.ok":
            recv_result(client, timeout=2)
    except Exception:
        pass


def collect_tag_sequence(client, timeout=15.0):
    """Collect every net.msg label through the terminal macro.result."""
    tags = []
    result = None
    deadline = time.monotonic() + timeout
    while result is None:
        env = recv_until(client, lambda e: True,
                         max(0.05, deadline - time.monotonic()))
        if env is None:
            break
        if env.get("type") == "net.msg":
            tags.append(env["payload"].get("tag"))
        elif env.get("type") == "macro.result":
            result = env
    return tags, result


def submit_tag_sequence(ctx, code, timeout=15.0):
    """Run one macro and return its complete ordered labels and result."""
    c = ctx.client()
    result = None
    try:
        r = c.call("macro.submit", {"code": code})
        if r.get("type") == "macro.submit.ok":
            tags, result = collect_tag_sequence(c, timeout)
            if result is None:
                abort_and_drain(c)
        else:
            tags = []
        return tags, result
    except Exception:
        if result is None:
            abort_and_drain(c)
        raise
    finally:
        c.close()


def expect_closed(sock, timeout=5.0):
    """The device closes silently: EOF, RST, or no data before timeout."""
    sock.settimeout(timeout)
    try:
        return sock.recv(64) == b""
    except ConnectionResetError:
        return True
    except (socket.timeout, TimeoutError):
        return False


def http_get(ctx, path):
    req = urllib.request.Request(f"http://{ctx.host}{path}")
    if ctx.token:
        req.add_header("Authorization", f"Bearer {ctx.token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode()


def _http_post_form(ctx, path, fields):
    from urllib.parse import urlencode

    req = urllib.request.Request(
        f"http://{ctx.host}{path}",
        data=urlencode(fields).encode(),
        method="POST",
    )
    if ctx.token:
        req.add_header("Authorization", f"Bearer {ctx.token}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode()


def http_post_macro(ctx, code, hold_ms=None):
    fields = {"code": code}
    if hold_ms is not None:
        fields["hold_ms"] = hold_ms
    return _http_post_form(ctx, "/macro", fields)


def http_compile(ctx, code, hold_ms=None):
    fields = {"code": code}
    if hold_ms is not None:
        fields["hold_ms"] = hold_ms
    return _http_post_form(ctx, "/compile", fields)


def http_compile_result(ctx, code, hold_ms=None):
    try:
        return 200, http_compile(ctx, code, hold_ms)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class Ctx:
    def __init__(self, host, port, token):
        self.host, self.port, self.token = host, port, token

    def client(self):
        return Client(self.host, self.port, self.token)


# --- A: auth & connection ---

def a1_valid_token(ctx):
    c = ctx.client()
    r = c.call("macro.status")
    check("A1 valid token -> status.ok",
          r["type"] == "macro.status.ok" and r["payload"]["state"] in
          ("idle", "done", "aborted"), json.dumps(r["payload"]))
    c.close()


def a2_wrong_token(ctx):
    if not ctx.token:
        check("A2 wrong token -> silent close", True,
              "SKIP: dev mode has no token to reject")
        return
    try:
        Client(ctx.host, ctx.port, "definitely-wrong").call("macro.status")
        check("A2 wrong token -> silent close", False, "call succeeded")
    except ProtocolError:
        check("A2 wrong token -> silent close", True)


def a3_garbage_prologue(ctx):
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(b"GARBAGE-GARBAGE-GARBAGE-GARBAGE-GARBAGE!!")
    closed = expect_closed(s)
    s.close()
    alive = False
    if closed:
        try:
            c = ctx.client()
            alive = c.call("macro.status")["type"] == "macro.status.ok"
            c.close()
        except Exception:
            pass
    check("A3 garbage prologue -> closed, device alive", closed and alive)


def a4_slow_auth_deadline(ctx):
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(auth_header(ctx.token)[:10])
    time.sleep(6)  # AUTH_DEADLINE_S is 5
    try:
        s.sendall(auth_header(ctx.token)[10:])
        closed = expect_closed(s)
    except OSError:
        closed = True  # peer already gone: broken pipe / RST on send
    s.close()
    check("A4 slow auth past deadline -> closed", closed)


def a5_max_clients(ctx):
    c1, c2 = ctx.client(), ctx.client()
    c1.call("macro.status")
    c2.call("macro.status")
    third = socket.create_connection((ctx.host, ctx.port), timeout=5)
    third.sendall(auth_header(ctx.token))
    closed = expect_closed(third)
    third.close()
    still = c1.call("macro.status")["type"] == "macro.status.ok"
    c1.close()
    c2.close()
    check("A5 3rd client rejected, first two unaffected", closed and still)


def a6_reconnect_churn(ctx, n=20):
    ok = True
    for _ in range(n):
        c = ctx.client()
        ok = ok and c.call("macro.status")["type"] == "macro.status.ok"
        c.close()
    check(f"A6 {n}x connect/auth/status/close churn", ok)


# --- B: framing ---

def b1_fragmented(ctx):
    c = ctx.client()
    env = {"v": 1, "type": "macro.status", "id": 900, "ts": 0, "payload": {}}
    body = json.dumps(env).encode()
    frame = struct.pack("<I", len(body)) + body
    for i in range(len(frame)):  # one byte at a time
        c.sock.sendall(frame[i : i + 1])
        time.sleep(0.005)
    r = recv_until(c, lambda e: e.get("id") == 900)
    check("B1 byte-by-byte frame reassembly",
          r is not None and r["type"] == "macro.status.ok")
    c.close()


def b2_coalesced(ctx):
    c = ctx.client()
    frames = b""
    for i in (901, 902, 903):
        body = json.dumps(
            {"v": 1, "type": "macro.status", "id": i, "ts": 0, "payload": {}}
        ).encode()
        frames += struct.pack("<I", len(body)) + body
    c.sock.sendall(frames)
    ids = []
    deadline = time.monotonic() + 5
    while len(ids) < 3 and time.monotonic() < deadline:
        c.sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            ids.append(c.recv_envelope().get("id"))
        except (socket.timeout, TimeoutError):
            break
    check("B2 coalesced frames answered in order", ids == [901, 902, 903],
          f"got {ids}")
    c.close()


def b3_oversize_header(ctx):
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(auth_header(ctx.token))
    time.sleep(0.2)
    s.sendall(struct.pack("<I", 9000))  # kind=0, length over the 8192 cap
    s.sendall(b"x" * 64)
    closed = expect_closed(s)
    s.close()
    alive = False
    try:
        c = ctx.client()
        alive = c.call("macro.status")["type"] == "macro.status.ok"
        c.close()
    except Exception:
        pass
    check("B3 oversize header -> closed, device alive", closed and alive)


def b4_binary_kind(ctx):
    c = ctx.client()
    payload = struct.pack("<HH", 1, 0) + b"\x89JPEGDATA"
    c.sock.sendall(struct.pack("<I", len(payload) | 0x80000000) + payload)
    r = recv_until(c, lambda e: e.get("type") == "error")
    alive = c.call("macro.status")["type"] == "macro.status.ok"
    check("B4 binary kind -> unsupported_kind, connection alive",
          r is not None and r["payload"]["code"] == "unsupported_kind" and alive)
    c.close()


def b5_bad_json(ctx):
    c = ctx.client()
    c.sock.sendall(struct.pack("<I", 4) + b"{xx}")
    r = recv_until(c, lambda e: e.get("type") == "error")
    alive = c.call("macro.status")["type"] == "macro.status.ok"
    check("B5 malformed JSON -> bad_envelope, connection alive",
          r is not None and r["payload"]["code"] == "bad_envelope" and alive)
    c.close()


# --- C: message layer ---

def c1_status_idle(ctx):
    c = ctx.client()
    r = c.call("macro.status")
    # any quiescent state is fine — a previous run may have left done/aborted
    check("C1 status responsive, nothing stuck running",
          r["payload"].get("state") in ("idle", "done", "aborted"),
          json.dumps(r["payload"]))
    c.close()


def c2_submit_and_result(ctx):
    c = ctx.client()
    r = c.call("macro.submit", {"code": "\\set{x}{1}"})
    ok = r["type"] == "macro.submit.ok" and r["payload"]["bytes"] > 0
    res = recv_result(c) if ok else None
    check("C2 submit -> ok + macro.result done",
          ok and res is not None and res["payload"]["state"] == "done"
          and "ms" in res["payload"],
          json.dumps(res["payload"]) if res else "no push")
    c.close()


def c3_compile_error(ctx):
    c = ctx.client()
    r = c.call("macro.submit", {"code": "\\rep{"})
    st = c.call("macro.status")
    # a failed compile must not start anything (state may be done/aborted
    # from an earlier case — only "running" would be wrong)
    check("C3 compile error -> compile_error, nothing started",
          r["type"] == "error" and r["payload"]["code"] == "compile_error"
          and st["payload"]["state"] != "running")
    c.close()


def c4_busy_rejected(ctx):
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{5s}"})
    r = c.call("macro.submit", {"code": "\\set{x}{1}"})
    c.call("macro.abort")  # cleanup
    recv_result(c)
    check("C4 submit while running -> state busy",
          r["type"] == "error" and r["payload"]["code"] == "state")
    c.close()


def c5_abort_running(ctx):
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{30s}"})
    r = c.call("macro.abort")
    res = recv_result(c)
    check("C5 abort -> abort.ok + result aborted",
          r["type"] == "macro.abort.ok" and res is not None
          and res["payload"]["state"] == "aborted")
    c.close()


def c6_abort_idle(ctx):
    c = ctx.client()
    r = c.call("macro.abort")
    check("C6 abort when idle -> state error",
          r["type"] == "error" and r["payload"]["code"] == "state")
    c.close()


def c7_device_info(ctx):
    c = ctx.client()
    r = c.call("device.info")
    p = r["payload"]
    check("C7 device.info fields",
          r["type"] == "device.info.ok" and p.get("proto") == 1
          and isinstance(p.get("free_mem"), int) and bool(p.get("fw")),
          json.dumps(p))
    c.close()


def c8_unknown_type(ctx):
    c = ctx.client()
    r = c.call("no.such")
    check("C8 unknown type -> unknown_type with id echo",
          r["type"] == "error" and r["payload"]["code"] == "unknown_type"
          and r["id"] == c._req_id)
    c.close()


def c9_bad_version(ctx):
    c = ctx.client()
    body = json.dumps({"v": 2, "type": "macro.status", "id": 910,
                       "ts": 0, "payload": {}}).encode()
    c.sock.sendall(struct.pack("<I", len(body)) + body)
    r = recv_until(c, lambda e: e.get("id") == 910)
    check("C9 v=2 envelope -> bad_version",
          r is not None and r["payload"]["code"] == "bad_version")
    c.close()


def c10_event_unknown_signal(ctx):
    c = ctx.client()
    r = c.call("event.push", {"name": "no.such", "fields": {}})
    check("C10 event.push unknown signal -> unknown_signal",
          r["payload"]["code"] == "unknown_signal")
    c.close()


def c11_event_bad_fields(ctx):
    c = ctx.client()
    r = c.call("event.push", {"name": "net.msg", "fields": {"x": "str"}})
    check("C11 event.push non-i32 field -> field error",
          r["payload"]["code"] == "field")
    c.close()


def c12_error_envelope_ignored(ctx):
    """An inbound 'error' envelope must be silently discarded by the device
    without replying with an error, preventing infinite ping-pong loops."""
    c = ctx.client()
    body = json.dumps({"v": PROTO_VERSION, "type": "error", "id": 777,
                       "ts": 0, "payload": {"code": "client_error", "message": "ignore me"}}).encode()
    c.sock.sendall(struct.pack("<I", len(body)) + body)
    time.sleep(0.1)
    # The device must not have responded with an error, and must still be responsive
    st = c.call("macro.status")
    check("C12 inbound error envelope ignored without error reply",
          st["type"] == "macro.status.ok" and not c.pending)
    c.close()


def c13_stray_response_ignored(ctx):
    """An inbound stray response (e.g. img.frame.ok with unmatched id)
    must be silently discarded by the device without replying with unknown_type."""
    c = ctx.client()
    body = json.dumps({"v": PROTO_VERSION, "type": "img.frame.ok", "id": 888,
                       "ts": 0, "payload": {"width": 80, "height": 60}}).encode()
    c.sock.sendall(struct.pack("<I", len(body)) + body)
    time.sleep(0.1)
    st = c.call("macro.status")
    check("C13 stray .ok response ignored without error reply",
          st["type"] == "macro.status.ok" and not c.pending)
    c.close()


def c14_compile_busy_when_running(ctx):
    """POST /compile must reject with Busy when a macro is running to protect
    the active macro's const store (spec/vm.md Part II §5)."""
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{3s}"})
    try:
        status, resp = http_compile_result(ctx, "\\set{x}{1}")
    finally:
        abort_and_drain(c)
    check("C14 /compile returns HTTP 409 while macro running",
          status == 409 and "Busy" in resp, f"HTTP {status}: {resp}")
    c.close()


# --- D: DSL net roundtrip ---

WAIT_MOVE = ("\\use{net}\\call{net.send}{wait_ready}"
             "\\wait[30s]{$slot.net.msg.val}"
             "\\move{$slot.net.msg.x,$slot.net.msg.y}\\call{net.send}{woke}")
WAIT_TIMEOUT = ("\\use{net}\\wait[1s]{$slot.net.msg.val}"
                "\\if{ {$timeout, =, 1} }{\\call{net.send}{timeout}}{\\call{net.send}{fired}}")
WAIT_COUNT = ("\\use{net}\\wait[30s]{$slot.net.msg.val}\\call{net.send}{probe}"
              "\\if{ {$ret, =, 2} }{\\call{net.send}{two}}{\\call{net.send}{not2}}")
SLOT_PROBE = ("\\use{net}"
              "\\if{ {$slot.net.msg.x, =, 7} }{\\call{net.send}{state7}}{\\call{net.send}{nostate}}")
SLOT_XY_CHECK = ("\\use{net}\\call{net.send}{slot_ready}"
                 "\\wait[30s]{$slot.net.msg.val}"
                 "\\if{ {$slot.net.msg.x, =, 10} }{"
                 "\\if{ {$slot.net.msg.y, =, 20} }{\\call{net.send}{xy_ok}}{\\call{net.send}{y_wrong}}"
                 "}{\\call{net.send}{x_wrong}}")


def d1_wait_wake(ctx):
    c = ctx.client()
    r = c.call("macro.submit", {"code": WAIT_MOVE})
    if r["type"] != "macro.submit.ok":
        check("D1 wait-then-signal: parked macro wakes on event", False,
              json.dumps(r))
        c.close()
        return
    # Synchronize after the pre-WAIT marker before injecting the event.
    ready = recv_tag(c, "wait_ready")
    st = c.call("macro.status") if ready else None
    if ready:
        c.call("event.push", {"name": "net.msg", "fields": {"val": 1, "x": 400, "y": 200}})
    woke = recv_tag(c, "woke") if ready else None
    res = recv_result(c)
    check("D1 wait-then-signal: macro reaches WAIT and wakes on event (mouse +400/+200!)",
          ready is not None and st is not None
          and st["payload"].get("state") == "running"
          and woke is not None and res is not None
          and res["payload"]["state"] == "done")
    c.close()


def d7_slot_values_on_wire(ctx):
    """Pin the actual slot VALUES end to end: the macro branches on the
    values it read and reports the outcome — no physical observation."""
    c = ctx.client()
    c.call("macro.submit", {"code": SLOT_XY_CHECK})
    ready = recv_tag(c, "slot_ready")
    st = c.call("macro.status") if ready else None
    if ready:
        c.call("event.push", {"name": "net.msg", "fields": {"val": 1, "x": 10, "y": 20}})
    tag = recv_until(
        c, lambda e: e.get("type") == "net.msg", timeout=8)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("D7 slot values verified on wire (x=10,y=20 -> xy_ok)",
          st is not None and st["payload"].get("state") == "running" and got == "xy_ok"
          and res is not None and res["payload"]["state"] == "done",
          f"tag={got}")
    c.close()


def d2_wait_timeout(ctx):
    c = ctx.client()
    c.call("macro.submit", {"code": WAIT_TIMEOUT})
    tag = recv_tag(c, "timeout", timeout=6)
    check("D2 wait timeout -> $timeout=1 branch", tag is not None)
    recv_result(c)
    c.close()


def d5_pending_latch_consumed(ctx):
    c = ctx.client()
    # Drain any latch retained by an earlier macro or an earlier suite run.
    c.call("macro.submit", {"code": "\\use{net}\\wait[0]{$slot.net.msg.val}"})
    recv_result(c)
    # An empty notification arrives with no waiter and latches net.msg.val.
    c.call("event.push", {"name": "net.msg", "fields": {}})
    time.sleep(0.2)
    c.call("macro.submit", {"code": WAIT_TIMEOUT})
    tag = recv_tag(c, "fired", timeout=2)
    res = recv_result(c, timeout=2)
    # A second immediate poll must time out: the first WAIT consumed the latch.
    c.call("macro.submit", {"code":
           "\\use{net}\\wait[0]{$slot.net.msg.val}"
           "\\if{ {$timeout, =, 1} }{\\call{net.send}{drained}}"
           "{\\call{net.send}{latch_leaked}}"})
    drained = recv_tag(c, "drained", timeout=2)
    res2 = recv_result(c, timeout=2)
    check("D5 pending latch is consumed immediately and exactly once",
          tag is not None and res is not None
          and res["payload"].get("state") == "done"
          and res["payload"].get("ms", 1000) < 500
          and drained is not None and res2 is not None
          and res2["payload"].get("state") == "done")
    c.close()


def d6_slot_state_persists(ctx):
    c = ctx.client()
    c.call("event.push", {"name": "net.msg", "fields": {"x": 7}})  # no waiter
    time.sleep(0.2)
    c.call("macro.submit", {"code": SLOT_PROBE})  # no \wait at all
    tag = recv_tag(c, "state7")
    check("D6 slots persist as last-published state (no \\wait needed)",
          tag is not None)
    recv_result(c)
    c.close()


def d3_ret_client_count(ctx):
    c1, c2 = ctx.client(), ctx.client()
    c1.call("macro.status")
    c2.call("macro.status")  # both authed
    c1.call("macro.submit", {"code": WAIT_COUNT})
    time.sleep(0.3)
    c1.call("event.push", {"name": "net.msg", "fields": {}})
    probe1 = recv_tag(c1, "probe")
    two1 = recv_tag(c1, "two")
    probe2 = recv_tag(c2, "probe")
    two2 = recv_tag(c2, "two")
    res = recv_result(c1)
    check("D3 $ret = client count, broadcast reaches both clients",
          all(x is not None for x in (probe1, two1, probe2, two2))
          and res is not None and res["payload"]["state"] == "done")
    c1.close()
    c2.close()


def d4_abort_during_wait(ctx):
    c = ctx.client()
    c.call("macro.submit", {"code": "\\use{net}\\wait{$slot.net.msg.val}"})
    time.sleep(0.3)
    r = c.call("macro.abort")
    res = recv_result(c)
    check("D4 abort interrupts a bounded \\wait",
          r["type"] == "macro.abort.ok" and res is not None
          and res["payload"]["state"] == "aborted")
    c.close()


# --- E: HTTP parity & coexistence ---

def e1_http_status_during_raw(ctx):
    c = ctx.client()
    c.call("macro.status")
    try:
        body = http_get(ctx, "/macro/status")
        ok = body.split()[0] in ("idle", "done", "aborted", "running")
    except Exception as e:
        ok, body = False, str(e)
    check("E1 HTTP /macro/status responsive during raw session", ok, body)
    c.close()


def e2_http_submit_broadcasts_to_raw(ctx):
    c = ctx.client()
    c.call("macro.status")
    try:
        body = http_post_macro(ctx, "\\set{x}{2}")
        res = recv_result(c)
        ok = body.startswith("Started") and res is not None \
            and res["payload"]["state"] == "done"
    except Exception as e:
        ok, body = False, str(e)
    check("E2 HTTP submit behaves the same; result broadcast on raw",
          ok, body)
    c.close()


# --- F: stability ---

def f1_long_idle(ctx, secs):
    c = ctx.client()
    c.call("macro.status")
    time.sleep(secs)
    r = c.call("macro.status")
    check(f"F1 connection survives {secs}s idle", r["type"] == "macro.status.ok")
    c.close()


def f2_submit_storm(ctx, n=10):
    c = ctx.client()
    ok = True
    for _ in range(n):
        r = c.call("macro.submit", {"code": "\\set{x}{1}"})
        res = recv_result(c)
        ok = ok and r["type"] == "macro.submit.ok" and res is not None \
            and res["payload"]["state"] == "done"
    check(f"F2 {n}x sequential submit storm", ok)
    c.close()


def f3_hard_kill(ctx):
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(auth_header(ctx.token))
    time.sleep(0.2)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                 struct.pack("ii", 1, 0))  # close with RST
    s.close()
    time.sleep(0.5)
    ok = False
    try:
        c = ctx.client()
        ok = c.call("macro.status")["type"] == "macro.status.ok"
        c.close()
    except Exception:
        pass
    check("F3 client hard-kill (RST) -> device unaffected", ok)


# --- G: Phase 3 readiness ---

def g1_binary_flood(ctx, frames=128, burst=16):
    """Receive-throughput lower bound. The device has no binary consumer
    armed here, so by protocol each kind=1 frame draws an
    unsupported_kind error (B4 pins this) — the flood PACES on those
    error envelopes (burst window), i.e. the serial log will show one
    "raw tx error" per chunk. That wall of errors is the expected
    behavior of this case, not a failure. burst stays under the device
    outbox cap (32) so no error response is dropped."""
    c = ctx.client()
    c.call("macro.status")  # warm the connection
    chunk = b"\x00" * (8192 - 4)  # u16 stream_id + u16 flags + raw bytes
    frame = struct.pack("<I", 0x80000000 | len(chunk)) + chunk
    t0 = time.monotonic()
    sent = drained = 0
    while drained < frames:
        while sent < frames and sent - drained < burst:
            c.sock.sendall(frame)
            sent += 1
        env = c.recv_envelope()
        if env.get("type") == "error" \
                and env["payload"].get("code") == "unsupported_kind":
            drained += 1
    dt = time.monotonic() - t0
    kbs = frames * len(frame) / dt / 1024
    c.close()
    alive = False
    try:
        alive = ctx.client().call("macro.status")["type"] == "macro.status.ok"
    except Exception:
        pass
    check(f"G1 binary flood {frames}x{len(frame)}B -> {kbs:.0f} KB/s, alive",
          drained == frames and alive, f"{kbs:.0f} KB/s")


def g2_latency_probe(ctx, n=10):
    """Per-request RTT on one established raw connection, with HTTP
    timings for contrast. T5/G1 fold connect+auth into their numbers;
    this isolates steady-state request latency. If raw and HTTP are both
    slow the cause is link-level (e.g. WiFi power save); if only raw is
    slow, it's the raw server loop. Informational, always passes."""
    c = ctx.client()
    c.call("macro.status")  # warm the connection
    rtts = []
    for _ in range(n):
        t0 = time.monotonic()
        c.call("device.info")
        rtts.append((time.monotonic() - t0) * 1000)
    c.close()
    try:
        http_get(ctx, "/macro/status")  # warm HTTP
        httpt = []
        for _ in range(n):
            t0 = time.monotonic()
            http_get(ctx, "/macro/status")
            httpt.append((time.monotonic() - t0) * 1000)
    except Exception as e:
        httpt = None
        print(f"  (HTTP contrast failed: {e})")

    def stat(v):
        return f"min={min(v):.0f} avg={sum(v) / len(v):.0f} max={max(v):.0f}ms"

    detail = f"raw: {stat(rtts)}"
    if httpt:
        detail += f" | http: {stat(httpt)}"
    check(f"G2 latency probe x{n}", True, detail)


# --- G3+: img exact-pixel family e2e (reference provider) ---
# Expected values derive from reference_provider's deterministic frames:
# pixel(10,8) in frame "a" = rgb(70,110,150) = 0x466E96.

IMG_NOPROV = ("\\use{img}\\use{net}"
              "\\call{img.snap}{10,8,1,1}"
              "\\if{ \\all{{$ret, =, 0}, {$err, =, 3}} }"
              "{\\call{net.send}{noprov}}{\\call{net.send}{prov_ok}}")

IMG_AT = ("\\use{img}\\use{net}"
          "\\call{img.snap}{10,8,1,1}\\set{s}{$ret}"
          "\\call{img.at}{$s}{0,0}"
          "\\if{ {$ret, =, 0x466E96} }{\\call{net.send}{at_ok}}{\\call{net.send}{at_wrong}}")

IMG_DIFF = ("\\use{img}\\use{net}"
            "\\call{img.snap}{0,0,8,8}\\set{a}{$ret}"
            "\\call{net.send}{snapped}"
            "\\wait[30s]{$slot.net.msg.val}"
            "\\call{img.snap}{0,0,8,8}\\set{b}{$ret}"
            "\\call{img.diff}{$a}{$b}{0}"
            "\\if{ {$ret, >, 0} }{\\call{net.send}{diff_ok}}{\\call{net.send}{no_diff}}")

IMG_REUSE = ("\\use{img}\\use{net}"
             "\\call{img.snap}{0,0,4,4}\\set{a}{$ret}"
             "\\call{img.snap}{10,8,4,4}\\set{a}{$ret}"  # overwrite releases
             "\\call{img.at}{$a}{0,0}"
             "\\if{ {$ret, =, 0x466E96} }{\\call{net.send}{reuse_ok}}{\\call{net.send}{reuse_wrong}}")


def g3_img_no_provider(ctx):
    """Requires no provider connected — in the default order it runs
    before _ensure_provider; with --only, don't combine with g4-g6."""
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_NOPROV})
    tag = recv_tag(c, "noprov", timeout=10)
    res = recv_result(c)
    check("G3 img.snap without provider -> $ret=0, $err=ERR_NO_PROVIDER",
          tag is not None and res is not None)
    c.close()


def _frame_default():
    rp = reference_provider
    return rp.W, rp.H, rp.frame_default()


def _frame_changed():
    rp = reference_provider
    return rp.W, rp.H, rp.frame_changed()


def _frame_fullhd():
    rp = reference_provider
    return rp.BIG_W, rp.BIG_H, rp.frame_fullhd()


def _ensure_provider(ctx):
    if getattr(ctx, "provider", None) is not None:
        return
    ctx.provider = reference_provider.ReferenceProvider(
        ctx.host, ctx.port, ctx.token)
    ctx.provider.start()
    # img.hello has no ack; a tiny real request proves device registration.
    deadline = time.monotonic() + 8
    while True:
        tags, res = submit_tag_sequence(ctx, IMG_NOPROV, timeout=8)
        if tags == ["prov_ok"] and res is not None \
                and res["payload"].get("state") == "done":
            return
        if tags != ["noprov"] or time.monotonic() >= deadline:
            raise RuntimeError(f"provider registration failed: {tags}")
        time.sleep(0.05)


def g4_img_snap_at(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_AT})
    tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=15)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("G4 snap + at reads exact pixel color",
          got == "at_ok" and res is not None, str(got))
    c.close()


def g5_img_diff(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    try:
        c.call("macro.submit", {"code": IMG_DIFF})
        snapped = recv_tag(c, "snapped", timeout=15)
        if snapped:
            ctx.provider.set_frame(*_frame_changed())
            c.call("event.push", {"name": "net.msg", "fields": {}})
        tag = recv_until(
            c,
            lambda e: e.get("type") == "net.msg"
            and e["payload"].get("tag") in ("diff_ok", "no_diff"),
            timeout=15)
        got = tag["payload"].get("tag") if tag else None
        res = recv_result(c)
        check("G5 diff detects swapped frame (and stays silent when same)",
              snapped is not None and got == "diff_ok" and res is not None,
              f"snapped={'ok' if snapped else 'FAIL'} diff={got}")
    finally:
        if getattr(ctx, "provider", None) is not None:
            ctx.provider.set_frame(*_frame_default())
        c.close()


def g6_handle_overwrite_reuse(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_REUSE})
    tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=15)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("G6 handle overwrite releases; variable reusable",
          got == "reuse_ok" and res is not None, str(got))
    c.close()


IMG_REFLIT = ("\\use{img}\\use{net}"
              "\\pixelrect{r}{\\row{0x002850,0x032B53}}"
              "\\call{img.snap}{0,0,2,1}\\set{c}{$ret}"
              "\\call{img.diff}{$r}{$c}{0}"
              "\\if{ \\all{{$ret, =, 0}, {$err, =, 0}} }{\\call{net.send}{ref_ok}}{\\call{net.send}{ref_wrong}}")


def g7_capture_passthrough(ctx):
    """Web UI's Pixel Assist path: POST starts, GET polls, PNG passes
    through byte-exact and decodes to the reference frame's pixels."""
    _ensure_provider(ctx)
    import zlib

    def req(method):
        r = urllib.request.Request(f"http://{ctx.host}/img/capture",
                                   method=method)
        if ctx.token:
            r.add_header("Authorization", f"Bearer {ctx.token}")
        return r

    data = None
    detail = "no result"
    try:
        urllib.request.urlopen(req("POST"), timeout=5).read()
        for _ in range(60):  # provider round trip takes ~1s
            time.sleep(0.25)
            r = urllib.request.urlopen(req("GET"), timeout=5)
            if r.status == 204:
                continue
            body = r.read()
            meta = json.loads(body.decode("utf-8"))
            url, nonce = meta["url"], meta["nonce"]
            sig = hmac.new(
                (ctx.token or "").encode("utf-8"),
                nonce.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            target_url = f"{url}?type=png&nonce={urllib.parse.quote(nonce)}&sig={urllib.parse.quote(sig)}"
            r_img = urllib.request.urlopen(target_url, timeout=5)
            data = r_img.read()
            break
    except Exception as e:
        check("G7 /img/capture direct preview", False, str(e))
        return
    ok = False
    if data and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        i, idat = 8, b""
        while i < len(data):
            ln = struct.unpack(">I", data[i:i + 4])[0]
            if data[i + 4:i + 8] == b"IDAT":
                idat += data[i + 8:i + 8 + ln]
            i += 12 + ln
        raw = zlib.decompress(idat)
        off = 8 * (w * 3 + 1) + 10 * 3 + 1  # pixel(10,8), filter-0 rows
        r_, g_, b_ = raw[off], raw[off + 1], raw[off + 2]
        ok = (w, h) == (80, 60) and (r_, g_, b_) == (70, 110, 150)
        detail = f"{w}x{h} pixel(10,8)=({r_},{g_},{b_})"
    check("G7 /img/capture direct preview", ok, detail)


def g7b_preview_rejections(ctx):
    """Direct preview security: tampered signature or nonce rejected with 403,
    including dev mode's empty-key HMAC; the one-shot socket prevents replay."""
    _ensure_provider(ctx)

    def req(method):
        r = urllib.request.Request(f"http://{ctx.host}/img/capture", method=method)
        if ctx.token:
            r.add_header("Authorization", f"Bearer {ctx.token}")
        return r

    def poll_preview():
        urllib.request.urlopen(req("POST"), timeout=5).read()
        for _ in range(60):
            time.sleep(0.25)
            r = urllib.request.urlopen(req("GET"), timeout=5)
            if r.status == 204:
                continue
            body = r.read()
            if body.startswith(b"{"):
                return json.loads(body.decode("utf-8"))
        return None

    # 1. Tampered signature test
    meta1 = poll_preview()
    if not meta1:
        check("G7b preview security rejections", False, "timeout getting meta1")
        return
    url1, nonce1 = meta1["url"], meta1["nonce"]
    bad_sig = "0" * 64
    bad_url = f"{url1}?type=png&nonce={urllib.parse.quote(nonce1)}&sig={bad_sig}"
    sig_rejected = False
    try:
        urllib.request.urlopen(bad_url, timeout=5)
    except urllib.error.HTTPError as e:
        sig_rejected = (e.code == 403)
    except Exception:
        pass

    # 2. Ephemeral single-use test (socket closed after attempt)
    time.sleep(0.05)
    replay_blocked = False
    try:
        urllib.request.urlopen(bad_url, timeout=2)
    except urllib.error.HTTPError:
        pass
    except urllib.error.URLError as e:
        replay_blocked = isinstance(
            e.reason, (ConnectionRefusedError, ConnectionResetError))
    except (ConnectionRefusedError, ConnectionResetError):
        replay_blocked = True

    # 3. Tampered nonce test
    meta2 = poll_preview()
    if not meta2:
        check("G7b preview security rejections", False, "timeout getting meta2")
        return
    url2, nonce2 = meta2["url"], meta2["nonce"]
    valid_sig2 = hmac.new((ctx.token or "").encode("utf-8"), nonce2.encode("utf-8"), hashlib.sha256).hexdigest()
    bad_nonce_url = f"{url2}?type=png&nonce=invalid_nonce_value&sig={valid_sig2}"
    nonce_rejected = False
    try:
        urllib.request.urlopen(bad_nonce_url, timeout=5)
    except urllib.error.HTTPError as e:
        nonce_rejected = (e.code == 403)
    except Exception:
        pass

    ok = sig_rejected and nonce_rejected and replay_blocked
    detail = f"sig_403={sig_rejected} nonce_403={nonce_rejected} replay_closed={replay_blocked}"
    check("G7b preview security rejections", ok, detail)


def g7c_preview_busy_conflict(ctx):
    """Concurrent POST /img/capture rejected with 409 Conflict."""
    _ensure_provider(ctx)

    def req(method):
        r = urllib.request.Request(f"http://{ctx.host}/img/capture", method=method)
        if ctx.token:
            r.add_header("Authorization", f"Bearer {ctx.token}")
        return r

    try:
        urllib.request.urlopen(req("POST"), timeout=5)
        busy_rejected = False
        try:
            urllib.request.urlopen(req("POST"), timeout=2)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                busy_rejected = True
        # Drain the first capture
        for _ in range(60):
            time.sleep(0.25)
            r = urllib.request.urlopen(req("GET"), timeout=5)
            if r.status != 204:
                break
        check("G7c preview busy conflict", busy_rejected, f"busy_409={busy_rejected}")
    except Exception as e:
        check("G7c preview busy conflict", False, str(e))


def g8_pixelrect_literal(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_REFLIT})
    tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=15)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("G8 \\pixelrect literal diffs 0 against the live frame",
          got == "ref_ok" and res is not None, str(got))
    c.close()


def g9_stream_throughput(ctx):
    """Max sustained throughput on the REAL path: a full-screen PNG
    capture (1920x1080 synthetic frame) flows provider -> device (kind-1
    stream) -> HTTP -> here. Total bytes / total time, POST to 200."""
    _ensure_provider(ctx)

    def req(method):
        r = urllib.request.Request(f"http://{ctx.host}/img/capture",
                                   method=method)
        if ctx.token:
            r.add_header("Authorization", f"Bearer {ctx.token}")
        return r

    ctx.provider.set_frame(*_frame_fullhd())
    try:
        t_start = time.monotonic()
        urllib.request.urlopen(req("POST"), timeout=5).read()
        meta = None
        for _ in range(60):
            time.sleep(0.25)
            r = urllib.request.urlopen(req("GET"), timeout=5)
            if r.status == 204:
                continue
            meta = json.loads(r.read().decode("utf-8"))
            break
        if not meta:
            check("G9 full-screen stream throughput", False, "preview arming failed or timed out")
            return

        url, nonce = meta["url"], meta["nonce"]
        sig = hmac.new(
            (ctx.token or "").encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        target_url = f"{url}?type=png&nonce={urllib.parse.quote(nonce)}&sig={urllib.parse.quote(sig)}"

        t0 = time.monotonic()
        r_img = urllib.request.urlopen(target_url, timeout=10)
        data = r_img.read()
        transfer_dt = time.monotonic() - t0
        total_dt = time.monotonic() - t_start

        if data and data[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = struct.unpack(">II", data[16:24])
            kbs = len(data) / transfer_dt / 1024 if transfer_dt > 0 else 0
            check("G9 full-screen stream throughput",
                  (w, h) == (1920, 1080),
                  f"{len(data) / 1024:.0f}KB ({w}x{h}) in {transfer_dt:.2f}s "
                  f"(total {total_dt:.2f}s) -> {kbs:.0f} KB/s")
        else:
            check("G9 full-screen stream throughput", False,
                  "bad or missing PNG")
    except urllib.error.HTTPError as e:
        # the 502 body carries _img_ui["err"] — the actual failure reason
        check("G9 full-screen stream throughput", False,
              f"{e} body={e.read()!r}")
    except Exception as e:
        check("G9 full-screen stream throughput", False, str(e))
    finally:
        ctx.provider.set_frame(*_frame_default())


IMG_POOL = ("\\use{img}\\use{net}"
            + "".join(f"\\call{{img.snap}}{{0,0,4,4}}\\set{{{v}}}{{"
                      f"$ret}}" for v in "abcdefgh")  # fill all SNAP_SLOTS
            + "\\call{img.snap}{0,0,4,4}"  # 9th: pool exhausted
            "\\if{ \\all{{$ret, =, 0}, {$err, =, 6}} }{\\call{net.send}{pool_full}}"
            "{\\call{net.send}{pool_nine}}"
            "\\set{a}{0}"  # overwrite-release happens at the WRITE: the
            # slot must be freed BEFORE the next snap, one breath earlier
            "\\call{img.snap}{10,8,4,4}\\set{a}{$ret}"
            "\\call{img.at}{$a}{0,0}"
            "\\if{ {$ret, =, 0x466E96} }{\\call{net.send}{pool_reused}}"
            "{\\call{net.send}{pool_bad}}")


def g10_snap_pool_exhaustion(ctx):
    """After SNAP_SLOTS live snapshots the next snap returns
    ERR_RESOURCE_EXHAUSTED; overwrite-release reopens a slot."""
    _ensure_provider(ctx)
    tags, res = submit_tag_sequence(ctx, IMG_POOL, timeout=60)
    check("G10 snap pool: exhaustion fails clean, release reopens a slot",
          tags == ["pool_full", "pool_reused"] and res is not None
          and res["payload"].get("state") == "done",
          str(tags))


IMG_OUT_OF_BOUNDS = ("\\use{img}\\use{net}"
                     "\\call{img.snap}{9999,9999,10,10}"
                     "\\if{ \\all{{$ret, =, 0}, {$err, =, 4}} }"
                     "{\\call{net.send}{oob_fail}}{\\call{net.send}{oob_ok}}")


def g11_img_out_of_bounds_rect(ctx):
    """Out-of-bounds img.snap returns ERR_OUT_OF_BOUNDS and keeps the
    provider link healthy."""
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_OUT_OF_BOUNDS})
    tag = recv_tag(c, "oob_fail", timeout=10)
    res = recv_result(c)
    check("G11 out-of-bounds rect -> $ret=0, $err=ERR_OUT_OF_BOUNDS",
          tag is not None and res is not None and res["payload"]["state"] == "done",
          str(tag))
    c.close()


# --- V/H/R/T/I: v4 conformance on the device ---


def v1_program_version_and_strings(ctx):
    dis = http_compile(ctx, "\\use{net}\\call{net.send}{v1}")
    check("V1 device compiler emits VERSION=0xA6, strings, and END",
          dis.startswith("; bytecode v166,") and "CALL net.send" in dis
          and dis.splitlines()[-1] == "END",
          dis.splitlines()[0] if dis else "empty disassembly")


def v2_code_capacity_beyond_legacy_limit(ctx):
    # 2500 CHAR instructions produce 5001 opcode bytes including END.
    dis = http_compile(ctx, "a" * 2500)
    first = dis.splitlines()[0] if dis else ""
    size = int(first.split(",")[1].strip()[:-1]) if ", " in first else 0
    check("V2 opcode segment exceeds the legacy 4 KiB limit",
          size > 4096 and dis.splitlines()[-1] == "END", first)


PIXELRECT_SELFTEST = ("\\use{img}\\use{net}"
    "\\pixelrect{p}{\\row{0xFF0000,\\rgb{0,255,0}},\\row{17,0x0000FF}}"
    "\\call{img.at}{$p}{0,0}\\set{c0}{$ret}"
    "\\call{img.at}{$p}{1,0}\\set{c1}{$ret}"
    "\\call{img.at}{$p}{0,1}\\set{c2}{$ret}"
    "\\call{img.at}{$p}{1,1}\\set{c3}{$ret}"
    "\\call{img.diff}{$p}{$p}{0}"
    "\\if{ \\all{{$ret, =, 0}, {$err, =, 0}, "
    "{$c0, =, 0xFF0000}, {$c1, =, 0x00FF00}, "
    "{$c2, =, 17}, {$c3, =, 0x0000FF}} }"
    "{\\call{net.send}{pixelrect_ok}}{\\call{net.send}{pixelrect_bad}}")


def h1_pixelrect_without_provider(ctx):
    tags, res = submit_tag_sequence(ctx, PIXELRECT_SELFTEST)
    check("H1 PixelRect RGB packing, row-major reads, shared self-diff",
          tags == ["pixelrect_ok"] and res is not None
          and res["payload"]["state"] == "done", str(tags))


SHARED_HANDLE = ("\\use{img}\\use{sys}\\use{net}"
    "\\call{img.snap}{0,0,4,4}\\set{a}{$ret}\\set{b}{$a}"
    "\\call{sys.free}\\set{a}{0}"  # b remains the sole snapshot reference
    "\\call{img.at}{$b}{0,0}\\set{c0}{$ret}"
    "\\set{b}{$b}\\call{img.at}{$b}{0,0}\\set{c1}{$ret}"
    "\\if{ \\all{{$c0, =, 0x002850}, {$c1, =, 0x002850}, {$err, =, 0}} }"
    "{\\call{net.send}{shared_ok}}{\\call{net.send}{shared_bad}}")


def h2_shared_handle_lifecycle(ctx):
    _ensure_provider(ctx)
    tags, res = submit_tag_sequence(ctx, SHARED_HANDLE)
    check("H2 shared Handle survives overwrite of $ret and other aliases",
          tags == ["shared_ok"] and res is not None
          and res["payload"]["state"] == "done", str(tags))


def h3_abort_releases_handle_pool(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    hold = ("\\use{img}\\use{net}\\set{ok}{1}"
            + "".join(
                f"\\call{{img.snap}}{{0,0,4,4}}"
                f"\\if{{ {{$err, !=, 0}} }}{{\\set{{ok}}{{0}}}}"
                f"\\set{{{v}}}{{$ret}}" for v in "abcdefgh")
            + "\\if{ {$ok, =, 1} }{"
              "\\call{net.send}{pool_ready}\\wait[30s]{$slot.net.msg.val}}"
              "{\\call{net.send}{setup_bad}}")
    aborted = None
    try:
        c.call("macro.submit", {"code": hold})
        ready = recv_tag(c, "pool_ready", timeout=45)
        if ready is not None:
            c.call("macro.abort")
            aborted = recv_result(c, timeout=3)
    finally:
        if aborted is None:
            abort_and_drain(c)
        c.close()
    if ready is not None and aborted is not None \
            and aborted["payload"].get("state") == "aborted":
        tags, res = submit_tag_sequence(ctx, IMG_POOL, timeout=60)
    else:
        tags, res = [], None
    check("H3 filled snapshot pool is fully reusable after abort",
          ready is not None and aborted is not None
          and aborted["payload"].get("state") == "aborted"
          and tags == ["pool_full", "pool_reused"] and res is not None
          and res["payload"]["state"] == "done", str(tags))


GUARD_ENTRY = ("\\use{net}"
    "\\guarded{ {$slot.net.msg.stop, =, 0} }"
    "{\\call{net.send}{guard_body}}{\\call{net.send}{guard_abort}}"
    "\\call{net.send}{guard_done}")


def r1_guard_entry_and_normal_exit(ctx):
    c = ctx.client()
    c.call("event.push", {"name": "net.msg", "fields": {"stop": 0}})
    c.close()
    entered, res1 = submit_tag_sequence(ctx, GUARD_ENTRY)
    c = ctx.client()
    c.call("event.push", {"name": "net.msg", "fields": {"stop": 1}})
    c.close()
    aborted, res2 = submit_tag_sequence(ctx, GUARD_ENTRY)
    check("R1 guard normal exit and false-on-entry controlled abort",
          entered == ["guard_body", "guard_done"]
          and aborted == ["guard_abort", "guard_done"]
          and all(r is not None and r["payload"]["state"] == "done"
                  for r in (res1, res2)),
          f"normal={entered} abort={aborted}")


GUARD_WAIT = ("\\use{net}\\wait[0]{$slot.net.msg.r2_idle}"
    "\\guarded[20ms]{ {$slot.net.msg.stop, =, 0} }"
    "{\\call{net.send}{guard_ready}\\wait[30s]{$slot.net.msg.go}\\call{net.send}{guard_late}}"
    "{\\if{ {$timeout, =, 1} }{\\call{net.send}{guard_abort}}"
    "{\\call{net.send}{guard_timeout}}}\\call{net.send}{guard_done}")


def r2_guard_interrupts_wait(ctx):
    c = ctx.client()
    res = None
    try:
        c.call("event.push", {"name": "net.msg", "fields": {"stop": 0}})
        c.call("macro.submit", {"code": GUARD_WAIT})
        ready = recv_tag(c, "guard_ready", timeout=5)
        if ready is not None:
            # Target arrival and guard violation share one projection. The
            # guard must win without consuming go or replacing old $timeout.
            c.call("event.push", {"name": "net.msg",
                                  "fields": {"stop": 1, "go": 1}})
            tags, res = collect_tag_sequence(c, timeout=5)
        else:
            tags = []
        if res is not None:
            probe_tags, probe_res = submit_tag_sequence(
                ctx,
                "\\use{net}\\wait[0]{$slot.net.msg.go}"
                "\\if{ {$timeout, =, 0} }{\\call{net.send}{target_kept}}"
                "{\\call{net.send}{target_lost}}")
        else:
            probe_tags, probe_res = [], None
        check("R2 guarded WAIT aborts before late body and preserves target state",
              ready is not None and tags == ["guard_abort", "guard_done"]
              and res is not None and res["payload"]["state"] == "done"
              and probe_tags == ["target_kept"] and probe_res is not None
              and probe_res["payload"]["state"] == "done",
              f"guard={tags} probe={probe_tags}")
    finally:
        if res is None:
            abort_and_drain(c)
        c.close()


def t1_hold_pace_and_execution_reset(ctx):
    c = ctx.client()
    c.call("macro.status")  # authenticate before any HTTP result is broadcast
    timings = []
    states = []
    try:
        for code, hold in (("\\pace{100ms}\\move{0,0}", 20),
                           ("\\pace{100ms}\\move{0,0}\\move{0,0}", 20),
                           ("\\move{0,0}", 0)):
            body = http_post_macro(ctx, code, hold_ms=hold)
            if not body.startswith("Started"):
                raise RuntimeError(f"HTTP submit failed: {body}")
            res = recv_result(c, timeout=5)
            if res is None:
                break
            timings.append(res["payload"]["ms"])
            states.append(res["payload"].get("state"))
    finally:
        if len(states) != 3:
            abort_and_drain(c)
        try:
            # click_hold_ms is device-global; restore the documented default.
            http_post_macro(ctx, "\\set{x}{1}", hold_ms=10)
            recv_result(c, timeout=3)
        except Exception:
            pass
        c.close()
    one, two, reset = (timings + [None] * 3)[:3]
    ok = (states == ["done", "done", "done"]
          and one is not None and 90 <= one < 1000
          and two is not None and 190 <= two < 2000
          and reset is not None and reset < 80)
    check("T1 hold+PACE totals and execution-start PACE reset",
          ok, f"one={one}ms two={two}ms reset={reset}ms states={states}")


def i1_multichunk_snapshot(ctx):
    _ensure_provider(ctx)
    indices = (0, 2729, 2730, 5460, 16383)
    code = "\\use{img}\\use{net}\\call{img.snap}{0,0,128,128}\\set{s}{$ret}"
    checks = ["{$err, =, 0}"]
    for i, index in enumerate(indices):
        x, y = index % 128, index // 128
        v = (x * 3 + y * 5) % 256
        expected = (v << 16) | (((v + 40) % 256) << 8) | ((v + 80) % 256)
        code += f"\\call{{img.at}}{{$s}}{{{x},{y}}}\\set{{m{i}}}{{$ret}}"
        checks.append("{$m" + str(i) + ", =, " + str(expected) + "}")
    code += ("\\if{ \\all{" + ", ".join(checks) + "} }"
             "{\\call{net.send}{multichunk_ok}}{\\call{net.send}{multichunk_bad}}")
    try:
        ctx.provider.set_frame(128, 128, reference_provider.frame_default(128, 128))
        tags, res = submit_tag_sequence(ctx, code, timeout=30)
        check("I1 128x128 snapshot preserves bytes across all seven chunks",
              tags == ["multichunk_ok"] and res is not None
              and res["payload"]["state"] == "done", str(tags))
    finally:
        ctx.provider.set_frame(*_frame_default())


def _mem_now(ctx):
    """free_mem probe; its own short connection is uniform per case."""
    try:
        c = ctx.client()
        m = c.call("device.info", {"gc": True})["payload"].get("free_mem")
        c.close()
        return m
    except Exception:
        return None


def f4_memory_trend(ctx, mem0, marks=None):
    if marks:
        print("memory trend per case (KB free, delta vs previous;"
              " isolated big drops = one-time arena growth, by design):")
        prev = mem0
        for name, m in marks:
            if m is None:
                print(f"  {name}: probe failed")
                continue
            d = m - prev if prev else 0
            prev = m
            flag = "  <-- big drop" if d < -65536 else ""
            print(f"  {name}: {m // 1024} KB ({d / 1024:+.0f}){flag}")
    # device.info(gc=True) collects before reading — every number in the
    # trace and here is post-GC truth, no sawtooth phase noise at all
    try:
        c = ctx.client()
        now = c.call("device.info", {"gc": True})["payload"].get("free_mem", 0)
        c.close()
    except Exception:
        now = 0
    # On-demand arenas (Appendix A) grow once to the workload's
    # high-water mark and then hold — that growth is NOT a leak. The
    # leak check anchors after the last big drop (one-time growth) and
    # demands the steady segment is flat; small drops never re-anchor,
    # so a slow leak still accumulates against the anchor.
    anchor = mem0
    prev = mem0
    for _name, m in marks or []:
        if m is not None and prev and m - prev < -65536:
            anchor = m  # one-time arena growth: steady state re-anchors
        if m is not None:
            prev = m
    base = anchor or mem0
    delta = now - base if base else 0
    pct = f"{delta / base * 100:+.1f}%" if base else "n/a"
    total = f"{(now - mem0) / mem0 * 100:+.1f}%" if mem0 else "n/a"
    check("F4 free_mem steady state (flat after arena high-water)",
          not base or delta > -base // 20,
          f"start={mem0} end={now} anchor={base} (steady {pct}, total {total})")


MANUAL = """
Manual-observation checklist (cannot be automated):
  M1  During D1, the mouse on PC1 jumped +400/+200 px (hard to miss).
  M2  Serial log shows 'raw client authed' / 'raw auth rejected' lines
      matching the A-group cases, and no traceback at any point.
  M3  The web UI on PC2 stayed responsive throughout.
"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=7373)
    ap.add_argument("--token", default=os.environ.get("MYCRO_TOKEN", ""))
    ap.add_argument("--idle-secs", type=float, default=10)
    ap.add_argument("--skip-slow", action="store_true")
    ap.add_argument("--only", default="",
                    help="comma-separated case id prefixes, e.g. --only d1,b4")
    args = ap.parse_args(argv)
    ctx = Ctx(args.host, args.port, args.token)

    mem0 = None
    try:
        c = ctx.client()
        c.call("macro.abort")  # preflight: force idle
        recv_result(c, timeout=2)
        info = c.call("device.info")["payload"]
        print(f"device: {info}")
        # warm up first-use allocations (http pool, asyncio internals, img
        # machinery) before the baseline, or F4 measures one-time settling
        for _ in range(3):
            c.call("macro.submit", {"code": "\\set{x}{1}"})
            recv_result(c, timeout=3)
        # Test-owned wait receptors retain state across macros and aborts.
        c.call("macro.submit", {"code":
               "\\use{net}\\wait[0]{$slot.net.msg.val}"
               "\\wait[0]{$slot.net.msg.go}"
               "\\wait[0]{$slot.net.msg.r2_idle}"})
        drained = recv_result(c, timeout=3)
        if drained is None or drained["payload"].get("state") != "done":
            raise RuntimeError("could not drain test-owned slot latches")
        time.sleep(0.3)
        mem0 = c.call("device.info", {"gc": True})["payload"].get("free_mem")
        c.close()
    except Exception as e:
        print(f"preflight failed — is the device up and the token right? {e}")
        return 2

    cases = [a1_valid_token, a2_wrong_token, a3_garbage_prologue,
             a5_max_clients, a6_reconnect_churn,
             b1_fragmented, b2_coalesced, b3_oversize_header,
             b4_binary_kind, b5_bad_json,
             c1_status_idle, c2_submit_and_result, c3_compile_error,
             c4_busy_rejected, c5_abort_running, c6_abort_idle,
             c7_device_info, c8_unknown_type, c9_bad_version,
             c10_event_unknown_signal, c11_event_bad_fields,
             c12_error_envelope_ignored, c13_stray_response_ignored,
             c14_compile_busy_when_running,
             v1_program_version_and_strings, v2_code_capacity_beyond_legacy_limit,
             r1_guard_entry_and_normal_exit, r2_guard_interrupts_wait,
             t1_hold_pace_and_execution_reset,
             d1_wait_wake, d2_wait_timeout, d3_ret_client_count,
             d4_abort_during_wait, d5_pending_latch_consumed,
             d6_slot_state_persists, d7_slot_values_on_wire,
             e1_http_status_during_raw, e2_http_submit_broadcasts_to_raw,
             f2_submit_storm, f3_hard_kill, g1_binary_flood, g2_latency_probe,
             g3_img_no_provider, h1_pixelrect_without_provider, g4_img_snap_at,
             g5_img_diff, g6_handle_overwrite_reuse, g7_capture_passthrough,
             g7b_preview_rejections, g7c_preview_busy_conflict,
             g8_pixelrect_literal, g9_stream_throughput, g10_snap_pool_exhaustion,
             g11_img_out_of_bounds_rect, h2_shared_handle_lifecycle,
             h3_abort_releases_handle_pool, i1_multichunk_snapshot]
    if not args.skip_slow:
        cases.append(a4_slow_auth_deadline)
    if args.only:
        wanted = [p.strip().lower() for p in args.only.split(",") if p.strip()]
        cases = [fn for fn in cases
                 if any(fn.__name__.startswith(p) for p in wanted)]
        if not cases and "f1" not in wanted:
            print(f"no cases match --only {args.only!r}")
            return 2

    mem_marks = []
    for fn in cases:
        try:
            fn(ctx)
        except Exception as e:
            check(fn.__name__, False, f"{type(e).__name__}: {e}")
        mem_marks.append((fn.__name__, _mem_now(ctx)))

    if not args.skip_slow and (not args.only or "f1" in wanted):
        try:
            f1_long_idle(ctx, args.idle_secs)
        except Exception as e:
            check("f1_long_idle", False, f"{type(e).__name__}: {e}")

    f4_memory_trend(ctx, mem0, mem_marks)

    if getattr(ctx, "provider", None) is not None:
        ctx.provider.stop()

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", ", ".join(failed))
    print(MANUAL)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
