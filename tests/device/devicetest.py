#!/usr/bin/env python3
"""Dual-end on-device acceptance suite organized by functional logical domain.

Drives a real MYCRO device over the raw TCP companion channel (protocol v1)
and HTTP REST interface, verifying end-to-end subsystem integration contracts
on live hardware. Run after deploying:

    python3 tests/device/devicetest.py <device-ip> --token <MYCRO_TOKEN>

Functional Domains:
    raw          Companion TCP transport, SHA-256 auth, wire framing, stream reassembly
    http         REST endpoints, compilation disassembly, cross-channel result broadcast
    macro        VM lifecycle, busy conflict, hardware timing, guards, large program capacity
    signals      Reactive signal wait-wake, slot persistence, safe relative mouse movement
    vision       Reference capture provider, snap/at/diff, streaming throughput, preview HMAC
    resilience   Rapid submit storm, TCP RST hard-kill, binary flood, memory steady-state

Safety Invariant:
    Only harmless relative mouse motion (\\move, \\mouse) and network loopback calls
    (\\call{net.send}) are executed on live hardware. No keystrokes, taps, or mouse clicks.

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
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import AUTH_MAGIC, PROTO_VERSION, Client, ProtocolError  # noqa: E402
import reference_provider  # noqa: E402

REGISTRY = []
RESULTS = []


class Domain:
    def __init__(self, index, name, aliases=(), desc=""):
        self.index = index
        self.name = name
        auto_aliases = [str(index), f"d{index}"]
        all_aliases = set(auto_aliases)
        for a in aliases:
            all_aliases.add(str(a).lower().strip())
        self.aliases = tuple(sorted(all_aliases))
        self.desc = desc
        self.fn = None

    def matches(self, term):
        t = term.lower().strip()
        return (
            t == str(self.index)
            or t == f"d{self.index}"
            or t == self.name
            or t in self.aliases
            or (len(t) >= 2 and self.name.startswith(t))
        )

    def run(self, ctx):
        width = max((len(d.name) for d in REGISTRY), default=10)
        try:
            details = self.fn(ctx)
            msg = ", ".join(details) if isinstance(details, (list, tuple)) else str(details or "")
            check(self.name, True, msg, width=width)
            ok = True
        except Exception as e:
            check(self.name, False, f"{type(e).__name__}: {e}", width=width)
            ok = False
        finally:
            mem_after = _mem_now(ctx)
            if mem_after is not None:
                ctx.mem_marks.append((self.name, mem_after))
        return ok


def domain(name, *aliases, desc=""):
    """Decorator registering a functional domain test into the table-driven registry."""
    def decorator(fn):
        idx = len(REGISTRY) + 1
        d = Domain(idx, name, aliases=aliases, desc=desc)
        d.fn = fn
        REGISTRY.append(d)
        return fn
    return decorator


def check(name, ok, detail="", width=None):
    RESULTS.append((name, bool(ok), detail))
    w = width or (max((len(d.name) for d in REGISTRY), default=10) if REGISTRY else 10)
    print(f"{'PASS' if ok else 'FAIL'}  {name:<{w}} ({detail})")


def auth_header(token):
    return AUTH_MAGIC + bytes([PROTO_VERSION]) + hashlib.sha256(token.encode()).digest()


def recv_until(client, pred, timeout=5.0):
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
    try:
        r = client.call("macro.abort")
        if r.get("type") == "macro.abort.ok":
            recv_result(client, timeout=2)
    except Exception:
        pass


def collect_tag_sequence(client, timeout=15.0):
    tags = []
    result = None
    deadline = time.monotonic() + timeout
    while result is None:
        env = recv_until(client, lambda e: True, max(0.05, deadline - time.monotonic()))
        if env is None:
            break
        if env.get("type") == "net.msg":
            tags.append(env["payload"].get("tag"))
        elif env.get("type") == "macro.result":
            result = env
    return tags, result


def submit_tag_sequence(ctx, code, timeout=15.0):
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
    req = urllib.request.Request(
        f"http://{ctx.host}{path}",
        data=urllib.parse.urlencode(fields).encode(),
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


def _mem_now(ctx):
    try:
        c = ctx.client()
        m = c.call("device.info", {"gc": True})["payload"].get("free_mem")
        c.close()
        return m
    except Exception:
        return None


class Ctx:
    def __init__(self, host, port, token, skip_slow=False, idle_secs=10, mem0=None):
        self.host = host
        self.port = port
        self.token = token
        self.skip_slow = skip_slow
        self.idle_secs = idle_secs
        self.mem0 = mem0
        self.mem_marks = []
        self.provider = None

    def client(self):
        return Client(self.host, self.port, self.token)


# --- Vision Reference Provider Helpers ---

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
    if ctx.provider is not None:
        return
    ctx.provider = reference_provider.ReferenceProvider(ctx.host, ctx.port, ctx.token)
    ctx.provider.start()
    deadline = time.monotonic() + 8
    img_noprov = (
        "\\use{img}\\use{net}\\call{img.snap}{10,8,1,1}"
        "\\if{ \\all{{$ret, =, 0}, {$err, =, 3}} }"
        "{\\call{net.send}{noprov}}{\\call{net.send}{prov_ok}}"
    )
    while True:
        tags, res = submit_tag_sequence(ctx, img_noprov, timeout=8)
        if tags == ["prov_ok"] and res is not None and res["payload"].get("state") == "done":
            return
        if tags != ["noprov"] or time.monotonic() >= deadline:
            raise RuntimeError(f"provider registration failed: {tags}")
        time.sleep(0.05)


def _stop_provider(ctx):
    if ctx.provider is not None:
        try:
            ctx.provider.stop()
        except Exception:
            pass
        ctx.provider = None


# ============================================================================
# Functional Logical Domain 1: raw
# ============================================================================

@domain(
    "raw",
    "channel", "tcp", "transport", "wire", "auth", "framing", "prologue", "a", "b",
    desc="Transport handshake, token auth, client concurrency cap, wire framing, and stream reassembly",
)
def test_raw(ctx):
    """Transport handshake, token auth, client concurrency cap, wire framing,
    and byte-stream reassembly."""
    details = []

    # 1. Valid token auth
    c = ctx.client()
    r = c.call("macro.status")
    if not (r["type"] == "macro.status.ok" and r["payload"].get("state") in ("idle", "done", "aborted")):
        raise AssertionError(f"valid token failed: {r}")
    c.close()
    details.append("auth ok")

    # 2. Invalid token rejection (when token configured)
    if ctx.token:
        try:
            Client(ctx.host, ctx.port, "definitely-wrong").call("macro.status")
            raise AssertionError("wrong token call unexpectedly succeeded")
        except ProtocolError:
            details.append("reject ok")
    else:
        details.append("dev mode")

    # 3. Garbage prologue handling
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(b"GARBAGE-GARBAGE-GARBAGE-GARBAGE-GARBAGE!!")
    closed = expect_closed(s)
    s.close()
    if not closed:
        raise AssertionError("garbage prologue not closed")
    details.append("garbage closed")

    # 4. Connection concurrency limit (max 2 active clients)
    c1, c2 = ctx.client(), ctx.client()
    c1.call("macro.status")
    c2.call("macro.status")
    third = socket.create_connection((ctx.host, ctx.port), timeout=5)
    third.sendall(auth_header(ctx.token))
    third_closed = expect_closed(third)
    third.close()
    c1_alive = c1.call("macro.status")["type"] == "macro.status.ok"
    c2_alive = c2.call("macro.status")["type"] == "macro.status.ok"
    c1.close()
    c2.close()
    if not (third_closed and c1_alive and c2_alive):
        raise AssertionError("client cap=2 enforcement failed")
    details.append("cap=2 ok")

    # 5. Byte-by-byte frame reassembly
    c = ctx.client()
    env = {"v": 1, "type": "macro.status", "id": 900, "ts": 0, "payload": {}}
    body = json.dumps(env).encode()
    frame = struct.pack("<I", len(body)) + body
    for i in range(len(frame)):
        c.sock.sendall(frame[i : i + 1])
        time.sleep(0.005)
    r = recv_until(c, lambda e: e.get("id") == 900)
    if not (r is not None and r["type"] == "macro.status.ok"):
        raise AssertionError("byte-by-byte frame reassembly failed")
    c.close()
    details.append("reassembly ok")

    # 6. Coalesced frame dispatch in order
    c = ctx.client()
    frames = b""
    for i in (901, 902, 903):
        body = json.dumps({"v": 1, "type": "macro.status", "id": i, "ts": 0, "payload": {}}).encode()
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
    c.close()
    if ids != [901, 902, 903]:
        raise AssertionError(f"coalesced frames out of order: {ids}")
    details.append("coalesced ok")

    # 7. Wire framing error resilience
    c = ctx.client()
    # Bad JSON envelope
    c.sock.sendall(struct.pack("<I", 4) + b"{xx}")
    err_json = recv_until(c, lambda e: e.get("type") == "error")
    # Binary kind when no stream armed
    payload = struct.pack("<HH", 1, 0) + b"\x89STREAM"
    c.sock.sendall(struct.pack("<I", len(payload) | 0x80000000) + payload)
    err_kind = recv_until(c, lambda e: e.get("type") == "error")
    # Inbound error envelope ignored without error reply loop
    body = json.dumps({
        "v": PROTO_VERSION, "type": "error", "id": 777, "ts": 0,
        "payload": {"code": "client_error", "message": "ignore me"}
    }).encode()
    c.sock.sendall(struct.pack("<I", len(body)) + body)
    time.sleep(0.1)
    st = c.call("macro.status")
    c.close()
    if not (err_json and err_json["payload"]["code"] == "bad_envelope"):
        raise AssertionError("bad JSON error handling failed")
    if not (err_kind and err_kind["payload"]["code"] == "unsupported_kind"):
        raise AssertionError("unsupported kind error handling failed")
    if not (st["type"] == "macro.status.ok"):
        raise AssertionError("inbound error loop prevention failed")
    details.append("framing resilience ok")

    # 8. Oversize header rejection
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(auth_header(ctx.token))
    time.sleep(0.1)
    s.sendall(struct.pack("<I", 9000))  # over 8192 cap
    s.sendall(b"x" * 64)
    oversize_closed = expect_closed(s)
    s.close()
    if not oversize_closed:
        raise AssertionError("oversize header socket not closed")
    details.append("oversize closed")

    # 9. Slow auth deadline (optional slow check)
    if not ctx.skip_slow:
        s = socket.create_connection((ctx.host, ctx.port), timeout=5)
        s.sendall(auth_header(ctx.token)[:10])
        time.sleep(6)  # AUTH_DEADLINE_S is 5
        try:
            s.sendall(auth_header(ctx.token)[10:])
            slow_closed = expect_closed(s)
        except OSError:
            slow_closed = True
        s.close()
        if not slow_closed:
            raise AssertionError("slow auth past deadline not closed")
        details.append("slow auth closed")

    return details


# ============================================================================
# Functional Logical Domain 2: http
# ============================================================================

@domain(
    "http",
    "rest", "web", "service", "services", "compile", "e",
    desc="REST status, bytecode compilation disassembly, concurrent HTTP responsiveness, and cross-channel push",
)
def test_http(ctx):
    """REST status, bytecode compilation disassembly, concurrent HTTP responsiveness,
    and cross-channel result broadcasting."""
    details = []

    # 1. REST status & /compile disassembly
    status_body = http_get(ctx, "/macro/status")
    st_val = status_body.split()[0]
    if st_val not in ("idle", "done", "aborted", "running"):
        raise AssertionError(f"unexpected /macro/status: {status_body}")
    dis = http_compile(ctx, "\\use{net}\\call{net.send}{v1}")
    if not (dis.startswith("; bytecode v4 (0xA6),") and "CALL net.send" in dis and dis.splitlines()[-1] == "END"):
        raise AssertionError(f"unexpected /compile disassembly: {dis}")
    details.append("rest ok")
    details.append("compile ok")

    # 2. HTTP responsiveness during active raw session
    c = ctx.client()
    c.call("macro.status")
    http_st = http_get(ctx, "/macro/status")
    if http_st.split()[0] not in ("idle", "done", "aborted", "running"):
        c.close()
        raise AssertionError("HTTP status unresponsive during raw session")
    details.append("concurrent ok")

    # 3. HTTP submit broadcasts macro.result to raw companions
    post_body = http_post_macro(ctx, "\\set{x}{2}")
    if not post_body.startswith("Started"):
        c.close()
        raise AssertionError(f"HTTP submit failed: {post_body}")
    res = recv_result(c, timeout=5)
    c.close()
    if not (res is not None and res["payload"].get("state") == "done"):
        raise AssertionError("HTTP submit result not broadcast to raw companion")
    details.append("cross-channel push ok")

    # 4. HTTP macro abort
    http_post_macro(ctx, "\\delay{5s}")
    time.sleep(0.1)
    abort_body = _http_post_form(ctx, "/macro/abort", {})
    if not abort_body.startswith("Abort requested"):
        raise AssertionError(f"HTTP abort failed: {abort_body}")
    time.sleep(0.2)
    st_after = http_get(ctx, "/macro/status")
    if st_after.split()[0] not in ("aborted", "idle"):
        raise AssertionError(f"state not aborted after HTTP abort: {st_after}")
    details.append("abort ok")

    # 5. /compile returns 409 Conflict while macro is running
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{3s}"})
    try:
        code, resp = http_compile_result(ctx, "\\set{x}{1}")
    finally:
        abort_and_drain(c)
        c.close()
    if not (code == 409 and "Busy" in resp):
        raise AssertionError(f"expected 409 Busy on compile while running, got {code}: {resp}")
    details.append("busy 409 ok")
    return details


# ============================================================================
# Functional Logical Domain 3: macro
# ============================================================================

@domain(
    "macro",
    "vm", "compiler", "exec", "execution", "timing", "guards", "guard", "capacity", "hold", "c", "v", "r", "t", "t1",
    desc="Macro lifecycle, busy rejection, hardware timer timing, guarded flow, and large program capacity",
)
def test_macro(ctx):
    """Macro lifecycle (submit->running->done), busy rejection, hardware timer
    timing (press_hold_ms + pace), guarded flow control, and large program capacity."""
    details = []

    # 1. Macro execution lifecycle (standard + protocol hold_ms payload validation)
    c = ctx.client()
    r = c.call("macro.submit", {"code": "\\set{x}{1}"})
    if not (r["type"] == "macro.submit.ok" and r["payload"]["bytes"] > 0):
        c.close()
        raise AssertionError(f"submit failed: {r}")
    res = recv_result(c, timeout=5)
    if not (res is not None and res["payload"]["state"] == "done" and "ms" in res["payload"]):
        c.close()
        raise AssertionError(f"lifecycle result failed: {res}")

    # Explicit hold_ms in proto payload object
    r_hold = c.call("macro.submit", {"code": "\\set{x}{1}", "hold_ms": 25})
    res_hold = recv_result(c, timeout=5) if r_hold.get("type") == "macro.submit.ok" else None
    if not (r_hold.get("type") == "macro.submit.ok" and res_hold and res_hold["payload"]["state"] == "done"):
        c.close()
        raise AssertionError(f"submit with hold_ms failed: {r_hold}, {res_hold}")

    # Invalid hold_ms rejected as field error before execution
    r_bad_hold = c.call("macro.submit", {"code": "\\set{x}{1}", "hold_ms": -1})
    if not (r_bad_hold.get("type") == "error" and r_bad_hold["payload"].get("code") == "field"):
        c.close()
        raise AssertionError(f"expected field error on negative hold_ms, got: {r_bad_hold}")
    c.close()
    details.append("lifecycle ok")

    # 2. Busy conflict rejection & raw abort
    c = ctx.client()
    c.call("macro.submit", {"code": "\\delay{5s}"})
    r_busy = c.call("macro.submit", {"code": "\\set{x}{1}"})
    r_abort = c.call("macro.abort")
    res_abort = recv_result(c, timeout=5)
    c.close()
    if not (r_busy["type"] == "error" and r_busy["payload"]["code"] == "state"):
        raise AssertionError(f"expected busy state error, got: {r_busy}")
    if not (r_abort["type"] == "macro.abort.ok" and res_abort is not None and res_abort["payload"]["state"] == "aborted"):
        raise AssertionError(f"abort failed: {r_abort}, {res_abort}")
    details.append("busy & abort ok")

    # 3. Hardware clock timing: press_hold_ms + pace totals and reset
    c = ctx.client()
    c.call("macro.status")
    timings = []
    states = []
    try:
        for code, hold in (("\\pace{100ms}\\move{0,0}", 20),
                           ("\\pace{100ms}\\move{0,0}\\move{0,0}", 20),
                           ("\\move{0,0}", 0)):
            body = http_post_macro(ctx, code, hold_ms=hold)
            if not body.startswith("Started"):
                raise AssertionError(f"HTTP submit failed: {body}")
            r_t = recv_result(c, timeout=5)
            if r_t is None:
                break
            timings.append(r_t["payload"]["ms"])
            states.append(r_t["payload"].get("state"))
    finally:
        if len(states) != 3:
            abort_and_drain(c)
        try:
            http_post_macro(ctx, "\\set{x}{1}")
            recv_result(c, timeout=3)
        except Exception:
            pass
        c.close()
    one, two, reset = (timings + [None] * 3)[:3]
    timing_ok = (
        states == ["done", "done", "done"]
        and one is not None and 80 <= one < 1000
        and two is not None and 180 <= two < 2000
        and reset is not None and reset < 80
    )
    if not timing_ok:
        raise AssertionError(f"timing bounds failed: one={one}ms, two={two}ms, reset={reset}ms")
    details.append(f"timing: {one}ms/{two}ms/{reset}ms ok")

    # 4. Guarded execution flow
    guard_entry = (
        "\\use{net}\\guarded{ {$slot.net.msg.stop, =, 0} }"
        "{\\call{net.send}{guard_body}}{\\call{net.send}{guard_abort}}"
        "\\call{net.send}{guard_done}"
    )
    c = ctx.client()
    c.call("event.push", {"name": "net.msg", "fields": {"stop": 0}})
    c.close()
    entered_tags, res1 = submit_tag_sequence(ctx, guard_entry)
    c = ctx.client()
    c.call("event.push", {"name": "net.msg", "fields": {"stop": 1}})
    c.close()
    aborted_tags, res2 = submit_tag_sequence(ctx, guard_entry)
    if not (entered_tags == ["guard_body", "guard_done"] and aborted_tags == ["guard_abort", "guard_done"]
            and res1 and res2 and res1["payload"]["state"] == "done" and res2["payload"]["state"] == "done"):
        raise AssertionError(f"guard entry/exit failed: normal={entered_tags}, abort={aborted_tags}")

    # Guard interrupts wait
    guard_wait = (
        "\\use{net}\\wait[0]{$slot.net.msg.r2_idle}"
        "\\guarded[20ms]{ {$slot.net.msg.stop, =, 0} }"
        "{\\call{net.send}{guard_ready}\\wait[30s]{$slot.net.msg.go}\\call{net.send}{guard_late}}"
        "{\\if{ {$timeout, =, 1} }{\\call{net.send}{guard_abort}}"
        "{\\call{net.send}{guard_timeout}}}\\call{net.send}{guard_done}"
    )
    c = ctx.client()
    res_gw = None
    try:
        c.call("event.push", {"name": "net.msg", "fields": {"stop": 0}})
        c.call("macro.submit", {"code": guard_wait})
        ready = recv_tag(c, "guard_ready", timeout=5)
        if ready:
            c.call("event.push", {"name": "net.msg", "fields": {"stop": 1, "go": 1}})
            tags_gw, res_gw = collect_tag_sequence(c, timeout=5)
        else:
            tags_gw = []
        probe_tags, probe_res = submit_tag_sequence(
            ctx,
            "\\use{net}\\wait[0]{$slot.net.msg.go}"
            "\\if{ {$timeout, =, 0} }{\\call{net.send}{target_kept}}"
            "{\\call{net.send}{target_lost}}"
        )
    finally:
        if res_gw is None:
            abort_and_drain(c)
        c.close()
    if not (ready and tags_gw == ["guard_abort", "guard_done"] and res_gw and res_gw["payload"]["state"] == "done"
            and probe_tags == ["target_kept"] and probe_res and probe_res["payload"]["state"] == "done"):
        raise AssertionError(f"guard wait interruption failed: tags={tags_gw}, probe={probe_tags}")
    details.append("guards ok")

    # 5. Program capacity (>4 KiB bytecode limit)
    dis_large = http_compile(ctx, "a" * 2500)
    first_line = dis_large.splitlines()[0] if dis_large else ""
    size = int(first_line.split(",")[1].strip()[:-1]) if ", " in first_line else 0
    if not (size > 4096 and dis_large.splitlines()[-1] == "END"):
        raise AssertionError(f"capacity check failed: size={size}")
    details.append(f">{size}B capacity ok")
    return details


# ============================================================================
# Functional Logical Domain 4: signals
# ============================================================================

@domain(
    "signals",
    "signal", "events", "event", "slots", "slot", "hid", "mouse", "reactive", "wait", "wake", "d",
    desc="Reactive signal wait-wake, slot persistence across macros, bounded wait timeout, and safe relative mouse movement",
)
def test_signals(ctx):
    """Reactive signal wait-wake, slot persistence across macros, bounded wait timeout,
    and safe relative mouse movement."""
    details = []

    # 1. Wait-wake coroutine parking and safe relative mouse move
    wait_move = (
        "\\use{net}\\call{net.send}{wait_ready}"
        "\\wait[30s]{$slot.net.msg.val}"
        "\\move{$slot.net.msg.x,$slot.net.msg.y}\\call{net.send}{woke}"
    )
    c = ctx.client()
    r = c.call("macro.submit", {"code": wait_move})
    if r["type"] != "macro.submit.ok":
        c.close()
        raise AssertionError(f"submit wait_move failed: {r}")
    ready = recv_tag(c, "wait_ready", timeout=5)
    st = c.call("macro.status") if ready else None
    if ready:
        c.call("event.push", {"name": "net.msg", "fields": {"val": 1, "x": 400, "y": 200}})
    woke = recv_tag(c, "woke", timeout=5) if ready else None
    res = recv_result(c, timeout=5)
    c.close()
    if not (ready and st and st["payload"].get("state") == "running" and woke and res and res["payload"]["state"] == "done"):
        raise AssertionError("wait-wake relative mouse move failed")
    details.append("wait-wake mouse +400/+200 px ok")

    # 2. Slot wire fidelity & persistence across macro executions
    slot_check = (
        "\\use{net}\\call{net.send}{slot_ready}"
        "\\wait[30s]{$slot.net.msg.val}"
        "\\if{ {$slot.net.msg.x, =, 10} }{"
        "\\if{ {$slot.net.msg.y, =, 20} }{\\call{net.send}{xy_ok}}{\\call{net.send}{y_wrong}}"
        "}{\\call{net.send}{x_wrong}}"
    )
    c = ctx.client()
    c.call("macro.submit", {"code": slot_check})
    ready = recv_tag(c, "slot_ready", timeout=5)
    if ready:
        c.call("event.push", {"name": "net.msg", "fields": {"val": 1, "x": 10, "y": 20}})
    tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=5)
    got = tag["payload"].get("tag") if tag else None
    res = recv_result(c, timeout=5)
    c.close()
    if not (got == "xy_ok" and res and res["payload"]["state"] == "done"):
        raise AssertionError(f"slot wire verification failed: {got}")

    # Slot state persistence without waiter
    c = ctx.client()
    c.call("event.push", {"name": "net.msg", "fields": {"x": 7}})
    c.close()
    slot_probe = (
        "\\use{net}"
        "\\if{ {$slot.net.msg.x, =, 7} }{\\call{net.send}{state7}}{\\call{net.send}{nostate}}"
    )
    tags, res = submit_tag_sequence(ctx, slot_probe)
    if not (tags == ["state7"] and res and res["payload"]["state"] == "done"):
        raise AssertionError(f"slot state persistence failed: {tags}")
    details.append("slots ok")

    # 3. Bounded wait timeout
    wait_timeout = (
        "\\use{net}\\wait[1s]{$slot.net.msg.val}"
        "\\if{ {$timeout, =, 1} }{\\call{net.send}{timeout}}{\\call{net.send}{fired}}"
    )
    c = ctx.client()
    c.call("macro.submit", {"code": wait_timeout})
    tag = recv_tag(c, "timeout", timeout=5)
    res = recv_result(c, timeout=5)
    c.close()
    if not (tag and res and res["payload"]["state"] == "done"):
        raise AssertionError("wait timeout failed")
    details.append("timeout branch ok")

    # 4. Pending latch consumption (exactly once)
    c = ctx.client()
    c.call("macro.submit", {"code": "\\use{net}\\wait[0]{$slot.net.msg.val}"})
    recv_result(c, timeout=2)
    c.call("event.push", {"name": "net.msg", "fields": {}})
    time.sleep(0.1)
    c.call("macro.submit", {"code": wait_timeout})
    tag_fired = recv_tag(c, "fired", timeout=2)
    res_fired = recv_result(c, timeout=2)
    c.call("macro.submit", {"code":
           "\\use{net}\\wait[0]{$slot.net.msg.val}"
           "\\if{ {$timeout, =, 1} }{\\call{net.send}{drained}}"
           "{\\call{net.send}{latch_leaked}}"})
    tag_drained = recv_tag(c, "drained", timeout=2)
    res_drained = recv_result(c, timeout=2)
    c.close()
    if not (tag_fired and res_fired and tag_drained and res_drained):
        raise AssertionError("pending latch consumption failed")
    details.append("latch consumed ok")

    # 5. Client count & broadcast
    c1, c2 = ctx.client(), ctx.client()
    c1.call("macro.status")
    c2.call("macro.status")
    wait_count = (
        "\\use{net}\\wait[30s]{$slot.net.msg.val}\\call{net.send}{probe}"
        "\\if{ {$ret, =, 2} }{\\call{net.send}{two}}{\\call{net.send}{not2}}"
    )
    c1.call("macro.submit", {"code": wait_count})
    time.sleep(0.2)
    c1.call("event.push", {"name": "net.msg", "fields": {}})
    probe1 = recv_tag(c1, "probe", timeout=5)
    two1 = recv_tag(c1, "two", timeout=5)
    probe2 = recv_tag(c2, "probe", timeout=5)
    two2 = recv_tag(c2, "two", timeout=5)
    res1 = recv_result(c1, timeout=5)
    c1.close()
    c2.close()
    if not (probe1 and two1 and probe2 and two2 and res1 and res1["payload"]["state"] == "done"):
        raise AssertionError("client count broadcast failed")
    details.append("broadcast ok")
    return details


# ============================================================================
# Functional Logical Domain 5: vision
# ============================================================================

@domain(
    "vision",
    "img", "camera", "pixel", "provider", "preview", "stream", "snap", "diff", "hmac", "g", "h", "i",
    desc="Reference capture provider, pixel snap/at/diff, handle lifecycle, direct preview HMAC authentication, full-screen stream throughput, and handle pool teardown",
)
def test_vision(ctx):
    """Reference capture provider, pixel snap/at/diff, handle lifecycle,
    direct preview HMAC authentication, full-screen stream throughput, and handle pool teardown."""
    details = []

    # 1. PixelRect selftest (local RGB packing, row-major reads, self-diff)
    pixelrect_selftest = (
        "\\use{img}\\use{net}"
        "\\pixelrect{p}{\\row{0xFF0000,\\rgb{0,255,0}},\\row{17,0x0000FF}}"
        "\\call{img.at}{$p}{0,0}\\set{c0}{$ret}"
        "\\call{img.at}{$p}{1,0}\\set{c1}{$ret}"
        "\\call{img.at}{$p}{0,1}\\set{c2}{$ret}"
        "\\call{img.at}{$p}{1,1}\\set{c3}{$ret}"
        "\\call{img.diff}{$p}{$p}{0}"
        "\\if{ \\all{{$ret, =, 0}, {$err, =, 0}, "
        "{$c0, =, 0xFF0000}, {$c1, =, 0x00FF00}, "
        "{$c2, =, 17}, {$c3, =, 0x0000FF}} }"
        "{\\call{net.send}{pixelrect_ok}}{\\call{net.send}{pixelrect_bad}}"
    )
    tags, res = submit_tag_sequence(ctx, pixelrect_selftest)
    if not (tags == ["pixelrect_ok"] and res and res["payload"]["state"] == "done"):
        raise AssertionError(f"pixelrect selftest failed: {tags}")
    details.append("pixelrect ok")

    # 2. Reference capture provider handshake
    _ensure_provider(ctx)
    details.append("provider ok")

    try:
        # 3. Snap + At exact pixel reading
        img_at = (
            "\\use{img}\\use{net}"
            "\\call{img.snap}{10,8,1,1}\\set{s}{$ret}"
            "\\call{img.at}{$s}{0,0}"
            "\\if{ {$ret, =, 0x466E96} }{\\call{net.send}{at_ok}}{\\call{net.send}{at_wrong}}"
        )
        tags, res = submit_tag_sequence(ctx, img_at)
        if not (tags == ["at_ok"] and res and res["payload"]["state"] == "done"):
            raise AssertionError(f"snap + at failed: {tags}")

        # 4. Diff detects swapped frames
        img_diff = (
            "\\use{img}\\use{net}"
            "\\call{img.snap}{0,0,8,8}\\set{a}{$ret}"
            "\\call{net.send}{snapped}"
            "\\wait[30s]{$slot.net.msg.val}"
            "\\call{img.snap}{0,0,8,8}\\set{b}{$ret}"
            "\\call{img.diff}{$a}{$b}{0}"
            "\\if{ {$ret, >, 0} }{\\call{net.send}{diff_ok}}{\\call{net.send}{no_diff}}"
        )
        c = ctx.client()
        try:
            c.call("macro.submit", {"code": img_diff})
            snapped = recv_tag(c, "snapped", timeout=15)
            if snapped:
                ctx.provider.set_frame(*_frame_changed())
                c.call("event.push", {"name": "net.msg", "fields": {}})
            tag = recv_until(c, lambda e: e.get("type") == "net.msg", timeout=15)
            got = tag["payload"].get("tag") if tag else None
            res = recv_result(c, timeout=10)
            if not (snapped and got == "diff_ok" and res and res["payload"]["state"] == "done"):
                raise AssertionError(f"diff detection failed: got={got}")
        finally:
            ctx.provider.set_frame(*_frame_default())
            c.close()
        details.append("snap/at/diff ok")

        # 5. Handle lifecycle: overwrite-release & shared aliases
        img_reuse = (
            "\\use{img}\\use{net}"
            "\\call{img.snap}{0,0,4,4}\\set{a}{$ret}"
            "\\call{img.snap}{10,8,4,4}\\set{a}{$ret}"
            "\\call{img.at}{$a}{0,0}"
            "\\if{ {$ret, =, 0x466E96} }{\\call{net.send}{reuse_ok}}{\\call{net.send}{reuse_wrong}}"
        )
        tags, res = submit_tag_sequence(ctx, img_reuse)
        if not (tags == ["reuse_ok"] and res and res["payload"]["state"] == "done"):
            raise AssertionError(f"handle overwrite reuse failed: {tags}")

        shared_handle = (
            "\\use{img}\\use{sys}\\use{net}"
            "\\call{img.snap}{0,0,4,4}\\set{a}{$ret}\\set{b}{$a}"
            "\\call{sys.free}\\set{a}{0}"
            "\\call{img.at}{$b}{0,0}\\set{c0}{$ret}"
            "\\set{b}{$b}\\call{img.at}{$b}{0,0}\\set{c1}{$ret}"
            "\\if{ \\all{{$c0, =, 0x002850}, {$c1, =, 0x002850}, {$err, =, 0}} }"
            "{\\call{net.send}{shared_ok}}{\\call{net.send}{shared_bad}}"
        )
        tags, res = submit_tag_sequence(ctx, shared_handle)
        if not (tags == ["shared_ok"] and res and res["payload"]["state"] == "done"):
            raise AssertionError(f"shared handle alias failed: {tags}")

        # 6. Multi-chunk snapshot (128x128 spanning 7 chunks)
        indices = (0, 2729, 2730, 5460, 16383)
        code_mc = "\\use{img}\\use{net}\\call{img.snap}{0,0,128,128}\\set{s}{$ret}"
        checks_mc = ["{$err, =, 0}"]
        for i, idx in enumerate(indices):
            x, y = idx % 128, idx // 128
            v = (x * 3 + y * 5) % 256
            expected = (v << 16) | (((v + 40) % 256) << 8) | ((v + 80) % 256)
            code_mc += f"\\call{{img.at}}{{$s}}{{{x},{y}}}\\set{{m{i}}}{{$ret}}"
            checks_mc.append(f"{{$m{i}, =, {expected}}}")
        code_mc += (
            "\\if{ \\all{" + ", ".join(checks_mc) + "} }"
            "{\\call{net.send}{multichunk_ok}}{\\call{net.send}{multichunk_bad}}"
        )
        try:
            ctx.provider.set_frame(128, 128, reference_provider.frame_default(128, 128))
            tags, res = submit_tag_sequence(ctx, code_mc, timeout=30)
            if not (tags == ["multichunk_ok"] and res and res["payload"]["state"] == "done"):
                raise AssertionError(f"multichunk snapshot failed: {tags}")
        finally:
            ctx.provider.set_frame(*_frame_default())
        details.append("handles ok")

        # 7. Telemetry watch flow
        img_watch = (
            "\\#!syntax: free\n"
            "\\use{img}\\use{net}\n"
            "\\call{img.watch}{btn}{10,8}{50ms,10s}\n"
            "\\wait[5s]{$slot.img.btn.val}\n"
            "\\if{ {$slot.img.btn.val, =, 0x466E96} }{\n"
            "  \\call{net.send}{watch_ok}\n"
            "}{\n"
            "  \\call{net.send}{watch_wrong}\n"
            "}\n"
            "\\call{img.unwatch}{btn}\n"
        )
        tags, res = submit_tag_sequence(ctx, img_watch, timeout=10)
        if not (tags == ["watch_ok"] and res and res["payload"]["state"] == "done"):
            raise AssertionError(f"telemetry flow failed: {tags}")
        details.append("telemetry ok")

        # 8. Direct preview /img/capture and HMAC security
        def req_preview(method):
            req = urllib.request.Request(f"http://{ctx.host}/img/capture", method=method)
            if ctx.token:
                req.add_header("Authorization", f"Bearer {ctx.token}")
            return req

        def poll_preview():
            urllib.request.urlopen(req_preview("POST"), timeout=5).read()
            for _ in range(60):
                time.sleep(0.25)
                r = urllib.request.urlopen(req_preview("GET"), timeout=5)
                if r.status == 204:
                    continue
                body = r.read()
                if body.startswith(b"{"):
                    return json.loads(body.decode("utf-8"))
            return None

        # Fetch preview PNG byte-exact
        meta = poll_preview()
        if not meta:
            raise AssertionError("preview arming timed out")
        url, nonce = meta["url"], meta["nonce"]
        sig = hmac.new((ctx.token or "").encode("utf-8"), nonce.encode("utf-8"), hashlib.sha256).hexdigest()
        target_url = f"{url}?type=png&nonce={urllib.parse.quote(nonce)}&sig={urllib.parse.quote(sig)}"
        png_data = urllib.request.urlopen(target_url, timeout=5).read()
        if not (png_data and png_data[:8] == b"\x89PNG\r\n\x1a\n"):
            raise AssertionError("invalid PNG magic from direct preview")

        # Security: tampered signature rejected with 403 & single-use socket prevents replay
        meta_sec = poll_preview()
        if not meta_sec:
            raise AssertionError("security preview arming timed out")
        bad_sig = "0" * 64
        bad_url = f"{meta_sec['url']}?type=png&nonce={urllib.parse.quote(meta_sec['nonce'])}&sig={bad_sig}"
        sig_403 = False
        try:
            urllib.request.urlopen(bad_url, timeout=5)
        except urllib.error.HTTPError as e:
            sig_403 = (e.code == 403)
        time.sleep(0.05)
        replay_blocked = False
        try:
            urllib.request.urlopen(bad_url, timeout=2)
        except urllib.error.HTTPError:
            pass
        except (urllib.error.URLError, ConnectionRefusedError, ConnectionResetError):
            replay_blocked = True
        if not (sig_403 and replay_blocked):
            raise AssertionError(f"HMAC security rejection failed: sig_403={sig_403}, replay={replay_blocked}")
        details.append("preview HMAC ok")

        # 9. Full HD stream throughput (1920x1080)
        ctx.provider.set_frame(*_frame_fullhd())
        try:
            t_start = time.monotonic()
            meta_fhd = poll_preview()
            if not meta_fhd:
                raise AssertionError("full HD preview arming failed")
            sig_fhd = hmac.new((ctx.token or "").encode("utf-8"), meta_fhd["nonce"].encode("utf-8"), hashlib.sha256).hexdigest()
            url_fhd = f"{meta_fhd['url']}?type=png&nonce={urllib.parse.quote(meta_fhd['nonce'])}&sig={urllib.parse.quote(sig_fhd)}"
            t0 = time.monotonic()
            data_fhd = urllib.request.urlopen(url_fhd, timeout=10).read()
            transfer_dt = time.monotonic() - t0
            kbs = len(data_fhd) / transfer_dt / 1024 if transfer_dt > 0 else 0
            w, h = struct.unpack(">II", data_fhd[16:24])
            if (w, h) != (1920, 1080):
                raise AssertionError(f"unexpected stream dimensions: {w}x{h}")
            details.append(f"stream {kbs / 1024:.1f} MB/s ok")
        finally:
            ctx.provider.set_frame(*_frame_default())

        # 10. Handle pool exhaustion & abort cleanup
        img_pool = (
            "\\use{img}\\use{net}"
            + "".join(f"\\call{{img.snap}}{{0,0,4,4}}\\set{{{v}}}{{$ret}}" for v in "abcdefgh")
            + "\\call{img.snap}{0,0,4,4}"
            "\\if{ \\all{{$ret, =, 0}, {$err, =, 6}} }{\\call{net.send}{pool_full}}"
            "{\\call{net.send}{pool_nine}}"
            "\\set{a}{0}"
            "\\call{img.snap}{10,8,4,4}\\set{a}{$ret}"
            "\\call{img.at}{$a}{0,0}"
            "\\if{ {$ret, =, 0x466E96} }{\\call{net.send}{pool_reused}}"
            "{\\call{net.send}{pool_bad}}"
        )
        tags, res = submit_tag_sequence(ctx, img_pool, timeout=60)
        if not (tags == ["pool_full", "pool_reused"] and res and res["payload"]["state"] == "done"):
            raise AssertionError(f"pool exhaustion failed: {tags}")
        details.append("pool teardown ok")
        return details

    finally:
        _stop_provider(ctx)


# ============================================================================
# Functional Logical Domain 6: resilience
# ============================================================================

@domain(
    "resilience",
    "stress", "stability", "memory", "drift", "storm", "flood", "latency", "rst", "f",
    desc="Rapid submit storm, socket RST hard-kill handling, binary flood throughput, latency probe, and post-workload memory steady-state verification",
)
def test_resilience(ctx):
    """Rapid submit storm, socket RST hard-kill handling, binary flood throughput,
    latency probe, and post-workload memory steady-state verification."""
    details = []

    # 1. Rapid sequential submit storm (10x)
    c = ctx.client()
    storm_ok = True
    for _ in range(10):
        r = c.call("macro.submit", {"code": "\\set{x}{1}"})
        res = recv_result(c, timeout=5)
        storm_ok = storm_ok and (r["type"] == "macro.submit.ok" and res and res["payload"]["state"] == "done")
    c.close()
    if not storm_ok:
        raise AssertionError("10x submit storm failed")
    details.append("storm 10x ok")

    # 2. Socket hard-kill (TCP RST) handling
    s = socket.create_connection((ctx.host, ctx.port), timeout=5)
    s.sendall(auth_header(ctx.token))
    time.sleep(0.1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()
    time.sleep(0.3)
    c = ctx.client()
    st = c.call("macro.status")
    c.close()
    if st["type"] != "macro.status.ok":
        raise AssertionError("device unresponsive after client TCP RST")
    details.append("RST recovery ok")

    # 3. Binary flood resilience (128x 8KB frames)
    c = ctx.client()
    c.call("macro.status")
    chunk = b"\x00" * (8192 - 4)
    frame = struct.pack("<I", 0x80000000 | len(chunk)) + chunk
    sent = drained = 0
    t0 = time.monotonic()
    frames_count = 128
    burst = 16
    while drained < frames_count:
        while sent < frames_count and sent - drained < burst:
            c.sock.sendall(frame)
            sent += 1
        env = c.recv_envelope()
        if env.get("type") == "error" and env["payload"].get("code") == "unsupported_kind":
            drained += 1
    dt = time.monotonic() - t0
    kbs = frames_count * len(frame) / dt / 1024
    c.close()
    alive = ctx.client().call("macro.status")["type"] == "macro.status.ok"
    if not (drained == frames_count and alive):
        raise AssertionError("binary flood failed")
    details.append(f"flood {kbs:.0f} KB/s ok")

    # 4. Latency probe (10x round-trips)
    c = ctx.client()
    c.call("macro.status")
    rtts = []
    for _ in range(10):
        t_rtt = time.monotonic()
        c.call("device.info")
        rtts.append((time.monotonic() - t_rtt) * 1000)
    c.close()
    avg_rtt = sum(rtts) / len(rtts)
    details.append(f"latency avg={avg_rtt:.0f}ms")

    # 5. Long idle (optional slow check)
    if not ctx.skip_slow and ctx.idle_secs > 0:
        c = ctx.client()
        c.call("macro.status")
        time.sleep(ctx.idle_secs)
        r_idle = c.call("macro.status")
        c.close()
        if r_idle["type"] != "macro.status.ok":
            raise AssertionError(f"connection dropped during {ctx.idle_secs}s idle")
        details.append(f"idle {ctx.idle_secs}s ok")

    # 6. Memory steady-state drift check
    now = _mem_now(ctx)
    if ctx.mem0 and now:
        # On-demand arenas (Appendix A) grow once to the workload's high-water mark
        # and then hold — that growth is not a leak. Re-anchor on big drops (>64KB).
        anchor = ctx.mem0
        prev = ctx.mem0
        for _name, m in getattr(ctx, "mem_marks", []):
            if m is not None and prev and (m - prev < -65536):
                anchor = m
            if m is not None:
                prev = m
        base = anchor or ctx.mem0
        delta = now - base
        pct = (delta / base) * 100
        # Tolerates up to 5% drift against steady-state anchor
        if delta < -base // 20:
            raise AssertionError(f"memory drift excessive: base={base}, end={now} ({pct:+.1f}%)")
        details.append(f"drift {pct:+.1f}% ok")

    return details


# ============================================================================
# Main Entry Point & Table-Driven CLI Runner
# ============================================================================

MANUAL = """
Manual-observation checklist (cannot be automated):
  M1  During 'signals', the mouse jumped +400/+200 px (observable on PC).
  M2  Serial log shows 'raw client authed' / 'raw auth rejected' lines
      matching 'raw', and no unhandled traceback at any point.
  M3  The web UI stayed responsive throughout test execution.
"""


def _expand_terms(raw):
    """Expand comma-separated terms supporting aliases, numbers, and ranges (e.g. 1-3, 1..3)."""
    terms = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        for sep in ("..", "-"):
            if sep in p:
                sub = p.split(sep, 1)
                if sub[0].strip().isdigit() and sub[1].strip().isdigit():
                    s, e = int(sub[0].strip()), int(sub[1].strip())
                    terms.extend(str(i) for i in range(min(s, e), max(s, e) + 1))
                    break
        else:
            terms.append(p)
    return terms


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host", nargs="?", default="", help="Device IP or mDNS hostname")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MYCRO_RAW_PORT", 7373)))
    ap.add_argument("--token", default=os.environ.get("MYCRO_TOKEN", ""))
    ap.add_argument("--idle-secs", type=float, default=10)
    ap.add_argument("--skip-slow", action="store_true", help="Skip slow auth deadline and long idle checks")
    ap.add_argument(
        "--list",
        action="store_true",
        help="List all registered functional logical domains and exit",
    )
    ap.add_argument(
        "--only",
        default="",
        help="Comma-separated domains by index or name/alias (e.g. 1, 1-3, raw, tcp, http, macro, signals, vision, resilience)",
    )
    args = ap.parse_args(argv)

    if args.list:
        print("Registered Functional Logical Domains:")
        for d in REGISTRY:
            aliases_str = ", ".join(a for a in d.aliases if not a.isdigit() and not a.startswith("d"))
            print(f"  [{d.index}] {d.name:<12} (aliases: {aliases_str})")
            print(f"      {d.desc}")
        return 0

    if not args.host:
        ap.error("the following arguments are required: host (or specify --list)")

    ctx = Ctx(
        args.host,
        args.port,
        args.token,
        skip_slow=args.skip_slow,
        idle_secs=args.idle_secs,
    )

    # Preflight: ensure idle, warm up allocations, and record clean GC baseline
    try:
        c = ctx.client()
        c.call("macro.abort")
        recv_result(c, timeout=2)
        info = c.call("device.info")["payload"]
        print(f"device: {info}")

        # Warm up first-use allocations
        for _ in range(3):
            c.call("macro.submit", {"code": "\\set{x}{1}"})
            recv_result(c, timeout=3)

        # Drain test-owned slot latches
        c.call("macro.submit", {"code":
               "\\use{net}\\wait[0]{$slot.net.msg.val}"
               "\\wait[0]{$slot.net.msg.go}"
               "\\wait[0]{$slot.net.msg.r2_idle}"})
        drained = recv_result(c, timeout=3)
        if drained is None or drained["payload"].get("state") != "done":
            raise RuntimeError("could not drain test-owned slot latches")
        time.sleep(0.2)
        ctx.mem0 = c.call("device.info", {"gc": True})["payload"].get("free_mem")
        c.close()
    except Exception as e:
        print(f"preflight failed — is the device up and the token right? {e}")
        return 2

    # Table-driven domain selection
    wanted = _expand_terms(args.only) if args.only else []
    selected = [
        d for d in REGISTRY
        if not wanted or any(d.matches(term) for term in wanted)
    ]
    if not selected:
        print(f"no functional domains match --only {args.only!r}")
        print("Available domains:")
        for d in REGISTRY:
            aliases_str = ", ".join(a for a in d.aliases if not a.isdigit() and not a.startswith("d"))
            print(f"  [{d.index}] {d.name:<12} (aliases: {aliases_str})")
        return 2

    # Table-driven dispatch
    for d in selected:
        d.run(ctx)

    failed = [n for n, ok, _ in RESULTS if not ok]
    total = len(RESULTS)
    passed = total - len(failed)
    print(f"\n{passed}/{total} functional domains passed cleanly ({len(failed)} failures).")
    if failed:
        print("FAILED DOMAINS:", ", ".join(failed))
    print(MANUAL)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
