#!/usr/bin/env python3
"""Phase 2 dual-end on-device acceptance suite.

Drives a real MYCRO device over the raw TCP channel (protocol v1) and
HTTP, checking wire behavior end to end. Run after deploying:

    python3 tools/devicetest.py <device-ip> --token <MYCRO_TOKEN>

Options: --port, --idle-secs (long-idle case, default 10),
--skip-slow (skip auth-deadline and long-idle cases).

Exits 0 when every automated case passes. A manual-observation checklist
(mouse motion on PC1, serial log lines) prints at the end.

Self-contained: stdlib + client.py from the same directory.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import AUTH_MAGIC, PROTO_VERSION, Client, ProtocolError  # noqa: E402

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


def f4_memory_trend(ctx, mem0):
    c = ctx.client()
    p = c.call("device.info")["payload"]
    c.close()
    now = p.get("free_mem", 0)
    delta = now - mem0 if mem0 else 0
    pct = f"{delta / mem0 * 100:+.1f}%" if mem0 else "n/a"
    check("F4 free_mem trend (warn if <-20%)", not mem0 or delta > -mem0 // 5,
          f"start={mem0} end={now} ({pct})")


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
        mem0 = info.get("free_mem")
        print(f"device: {info}")
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
             d1_wait_wake, d2_wait_timeout, d3_ret_client_count,
             d4_abort_during_wait, d5_stale_signal_dropped,
             d6_slot_state_persists, d7_slot_values_on_wire,
             e1_http_status_during_raw, e2_http_submit_broadcasts_to_raw,
             f2_submit_storm, f3_hard_kill]
    if not args.skip_slow:
        cases.append(a4_slow_auth_deadline)
    if args.only:
        wanted = [p.strip().lower() for p in args.only.split(",") if p.strip()]
        cases = [fn for fn in cases
                 if any(fn.__name__.startswith(p) for p in wanted)]
        if not cases and "f1" not in wanted:
            print(f"no cases match --only {args.only!r}")
            return 2

    for fn in cases:
        try:
            fn(ctx)
        except Exception as e:
            check(fn.__name__, False, f"{type(e).__name__}: {e}")

    if not args.skip_slow and (not args.only or "f1" in wanted):
        try:
            f1_long_idle(ctx, args.idle_secs)
        except Exception as e:
            check("f1_long_idle", False, f"{type(e).__name__}: {e}")
            check("f1_long_idle", False, f"{type(e).__name__}: {e}")

    f4_memory_trend(ctx, mem0)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", ", ".join(failed))
    print(MANUAL)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
