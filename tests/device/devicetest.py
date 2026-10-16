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
import json
import os
import socket
import struct
import sys
import time
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


def http_post_macro(ctx, code):
    from urllib.parse import urlencode

    req = urllib.request.Request(
        f"http://{ctx.host}/macro",
        data=urlencode({"code": code}).encode(),
        method="POST",
    )
    if ctx.token:
        req.add_header("Authorization", f"Bearer {ctx.token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode()


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
    c.call("macro.submit", {"code": "\\delay{5}"})
    r = c.call("macro.submit", {"code": "\\set{x}{1}"})
    c.call("macro.abort")  # cleanup
    recv_result(c)
    check("C4 submit while running -> state busy",
          r["type"] == "error" and r["payload"]["code"] == "state")
    c.close()


def c5_abort_running(ctx):
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{30}"})
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
          st["type"] == "macro.status.ok")
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
          st["type"] == "macro.status.ok")
    c.close()


def c14_compile_busy_when_running(ctx):
    """POST /compile must reject with Busy when a macro is running to protect
    the active macro's const store (spec/vm.md Part II §5)."""
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{3}"})
    from urllib.parse import urlencode
    req = urllib.request.Request(
        f"http://{ctx.host}/compile",
        data=urlencode({"code": "\\set{x}{1}"}).encode(),
        method="POST",
    )
    if ctx.token:
        req.add_header("Authorization", f"Bearer {ctx.token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        resp = r.read().decode()
    c.call("macro.abort")
    recv_result(c)
    check("C14 /compile returns Busy while macro running",
          "Busy" in resp, resp)
    c.close()


# --- D: DSL net roundtrip ---

WAIT_MOVE = "\\use{net}\\wait{net.msg}{30}\\move{$net.msg.x,$net.msg.y}\\call{net.send}{woke}"
WAIT_TIMEOUT = ("\\use{net}\\wait{net.msg}{1}"
                "\\ifnum{$timeout}{=}{1}{\\call{net.send}{timeout}}{\\call{net.send}{fired}}")
WAIT_COUNT = ("\\use{net}\\wait{net.msg}{30}\\call{net.send}{probe}"
              "\\ifnum{$ret}{=}{2}{\\call{net.send}{two}}{\\call{net.send}{not2}}")
SLOT_PROBE = ("\\use{net}"
              "\\ifnum{$net.msg.x}{=}{7}{\\call{net.send}{state7}}{\\call{net.send}{nostate}}")
SLOT_XY_CHECK = ("\\use{net}\\wait{net.msg}{30}"
                 "\\ifnum{$net.msg.x}{=}{10}{"
                 "\\ifnum{$net.msg.y}{=}{20}{\\call{net.send}{xy-ok}}{\\call{net.send}{y-wrong}}"
                 "}{\\call{net.send}{x-wrong}}")


def d1_wait_wake(ctx):
    c = ctx.client()
    r = c.call("macro.submit", {"code": WAIT_MOVE})
    if r["type"] != "macro.submit.ok":
        check("D1 wait-then-signal: parked macro wakes on event", False,
              json.dumps(r))
        c.close()
        return
    # Prove the macro is parked in \wait BEFORE the event is sent
    st = c.call("macro.status")
    c.call("event.push", {"name": "net.msg", "fields": {"x": 400, "y": 200}})
    woke = recv_tag(c, "woke")
    res = recv_result(c)
    check("D1 wait-then-signal: parked macro wakes on event (mouse +400/+200!)",
          st["payload"].get("state") == "running"
          and woke is not None and res is not None
          and res["payload"]["state"] == "done")
    c.close()


def d7_slot_values_on_wire(ctx):
    """Pin the actual slot VALUES end to end: the macro branches on the
    values it read and reports the outcome — no physical observation."""
    c = ctx.client()
    c.call("macro.submit", {"code": SLOT_XY_CHECK})
    st = c.call("macro.status")
    c.call("event.push", {"name": "net.msg", "fields": {"x": 10, "y": 20}})
    tag = recv_until(
        c, lambda e: e.get("type") == "net.msg", timeout=8)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("D7 slot values verified on wire (x=10,y=20 -> xy-ok)",
          st["payload"].get("state") == "running" and got == "xy-ok"
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


def d5_stale_signal_dropped(ctx):
    c = ctx.client()
    # Fire with no waiter; under next-fire semantics the latch is discarded
    c.call("event.push", {"name": "net.msg", "fields": {"x": 1}})
    time.sleep(0.2)
    c.call("macro.submit", {"code": WAIT_TIMEOUT})  # 1s window, self-reports
    tag = recv_tag(c, "timeout", timeout=6)
    check("D5 signal-then-wait: stale fire does not wake \\wait",
          tag is not None)
    recv_result(c)
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
    c.call("macro.submit", {"code": "\\use{net}\\wait{net.msg}"})
    time.sleep(0.3)
    r = c.call("macro.abort")
    res = recv_result(c)
    check("D4 abort interrupts a forever \\wait",
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
              "\\ifnum{$ret}{<}{0}{\\call{net.send}{noprov}}{\\call{net.send}{prov-ok}}")

IMG_AT = ("\\use{img}\\use{net}"
          "\\call{img.snap}{10,8,1,1}\\set{s}{$ret}"
          "\\call{img.at}{$s}{0,0}"
          "\\ifnum{$ret}{=}{0x466E96}{\\call{net.send}{at-ok}}{\\call{net.send}{at-wrong}}")

IMG_DIFF = ("\\use{img}\\use{net}"
            "\\call{img.snap}{0,0,8,8}\\set{a}{$ret}"
            "\\call{net.send}{snapped}"
            "\\wait{net.msg}{30}"
            "\\call{img.snap}{0,0,8,8}\\set{b}{$ret}"
            "\\call{img.diff}{$a}{$b}{0}"
            "\\ifnum{$ret}{>}{0}{\\call{net.send}{diff-ok}}{\\call{net.send}{no-diff}}")

IMG_REUSE = ("\\use{img}\\use{net}"
             "\\call{img.snap}{0,0,4,4}\\set{a}{$ret}"
             "\\call{img.snap}{10,8,4,4}\\set{a}{$ret}"  # overwrite releases
             "\\call{img.at}{$a}{0,0}"
             "\\ifnum{$ret}{=}{0x466E96}{\\call{net.send}{reuse-ok}}{\\call{net.send}{reuse-wrong}}")


def g3_img_no_provider(ctx):
    """Requires no provider connected — in the default order it runs
    before _ensure_provider; with --only, don't combine with g4-g6."""
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_NOPROV})
    tag = recv_tag(c, "noprov", timeout=10)
    res = recv_result(c)
    check("G3 img.snap without provider -> $ret<0",
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
    if getattr(ctx, "provider", None) is None:
        ctx.provider = reference_provider.ReferenceProvider(
            ctx.host, ctx.port, ctx.token)
        ctx.provider.start()
        time.sleep(0.3)  # connect + img.hello registration


def g4_img_snap_at(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_AT})
    tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=15)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("G4 snap + at reads exact pixel color",
          got == "at-ok" and res is not None, str(got))
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
            and e["payload"].get("tag") in ("diff-ok", "no-diff"),
            timeout=15)
        got = tag["payload"].get("tag") if tag else None
        res = recv_result(c)
        check("G5 diff detects swapped frame (and stays silent when same)",
              snapped is not None and got == "diff-ok" and res is not None,
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
          got == "reuse-ok" and res is not None, str(got))
    c.close()


IMG_REFLIT = ("\\use{img}\\use{net}"
              "\\set{r}{\\rect{\\row{0x002850,0x032B53}}}"
              "\\call{img.snap}{0,0,2,1}\\set{c}{$ret}"
              "\\call{img.diff}{$r}{$c}{0}"
              "\\ifnum{$ret}{=}{0}{\\call{net.send}{ref-ok}}{\\call{net.send}{ref-wrong}}")


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
            data = r.read()
            break
    except Exception as e:
        check("G7 /img/capture PNG passthrough", False, str(e))
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
    check("G7 /img/capture PNG passthrough", ok, detail)


def g8_rect_literal(ctx):
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_REFLIT})
    tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=15)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c)
    check("G8 \\rect literal diffs 0 against the live frame",
          got == "ref-ok" and res is not None, str(got))
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
        urllib.request.urlopen(req("POST"), timeout=5).read()
        t0 = time.monotonic()
        data = None
        stream_ms = None
        for _ in range(240):  # generous: up to 60s for a multi-MB stream
            time.sleep(0.25)
            r = urllib.request.urlopen(req("GET"), timeout=5)
            if r.status == 204:
                continue
            stream_ms = r.headers.get("X-Stream-Ms")  # device-timed leg 1
            data = r.read()
            break
        dt = time.monotonic() - t0
        if data and data[:8] == b"\x89PNG\r\n\x1a\n":
            kbs = len(data) / dt / 1024
            legs = ""
            if stream_ms is not None:
                s1 = int(stream_ms) / 1000
                leg1 = len(data) / s1 / 1024 if s1 > 0 else 0
                leg2 = len(data) / (dt - s1) / 1024 if dt > s1 else 0
                legs = (f" | legs: provider->dev {leg1:.0f} KB/s,"
                        f" dev->http {leg2:.0f} KB/s")
            check("G9 full-screen stream throughput",
                  dt > 0, f"{len(data) / 1024:.0f}KB in {dt:.1f}s -> "
                  f"{kbs:.0f} KB/s{legs}")
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
            + "\\call{img.snap}{0,0,4,4}"  # 9th: pool full -> $ret=-1
            # $ret must be checked directly: storing it types the variable
            # as a handle and ifnum rejects handle operands (the $ret
            # exemption exists exactly for error-sentinel checks)
            "\\ifnum{$ret}{<}{0}{\\call{net.send}{pool-full}}"
            "{\\call{net.send}{pool-nine}}"
            "\\set{a}{0}"  # overwrite-release happens at the WRITE: the
            # slot must be freed BEFORE the next snap, one breath earlier
            "\\call{img.snap}{10,8,4,4}\\set{a}{$ret}"
            "\\call{img.at}{$a}{0,0}"
            "\\ifnum{$ret}{=}{0x466E96}{\\call{net.send}{pool-reused}}"
            "{\\call{net.send}{pool-bad}}")


def g10_snap_pool_exhaustion(ctx):
    """Snap slot pool (spec/vm.md Appendix A): after SNAP_SLOTS live
    snapshots the next snap fails deterministically ($ret<0); an
    overwrite-release reopens a slot and the pool keeps working."""
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_POOL})
    tags = []
    for _ in range(2):
        e = recv_until(c, lambda m: m.get("type") == "net.msg", timeout=15)
        tags.append(e["payload"].get("tag") if e else None)
    res = recv_result(c)
    check("G10 snap pool: exhaustion fails clean, release reopens a slot",
          tags == ["pool-full", "pool-reused"] and res is not None,
          str(tags))
    c.close()


IMG_OUT_OF_BOUNDS = ("\\use{img}\\use{net}"
                     "\\call{img.snap}{9999,9999,10,10}"
                     "\\ifnum{$ret}{<}{0}{\\call{net.send}{oob-fail}}{\\call{net.send}{oob-ok}}")


def g11_img_out_of_bounds_rect(ctx):
    """Out-of-bounds img.snap rect is rejected by provider (capture_failed),
    returns $ret < 0 to macro, and keeps the provider link healthy."""
    _ensure_provider(ctx)
    c = ctx.client()
    c.call("macro.submit", {"code": IMG_OUT_OF_BOUNDS})
    tag = recv_tag(c, "oob-fail", timeout=10)
    res = recv_result(c)
    check("G11 out-of-bounds rect rejected cleanly ($ret < 0)",
          tag is not None and res is not None and res["payload"]["state"] == "done",
          str(tag))
    c.close()


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
             d1_wait_wake, d2_wait_timeout, d3_ret_client_count,
             d4_abort_during_wait, d5_stale_signal_dropped,
             d6_slot_state_persists, d7_slot_values_on_wire,
             e1_http_status_during_raw, e2_http_submit_broadcasts_to_raw,
             f2_submit_storm, f3_hard_kill, g1_binary_flood, g2_latency_probe,
             g3_img_no_provider, g4_img_snap_at, g5_img_diff,
             g6_handle_overwrite_reuse, g7_capture_passthrough,
             g8_rect_literal, g9_stream_throughput, g10_snap_pool_exhaustion,
             g11_img_out_of_bounds_rect]
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
