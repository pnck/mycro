"""MYCRO Raw Protocol TCP Server (Protocol v1).

Single round-robin task owns every connection: no per-connection tasks,
no cross-task socket access. Fully exception-isolated against CircuitPython
TCP socket fault risks (#10775).
"""

import os
import time
import traceback
try:
    import asyncio
except ImportError:
    import _asyncio as asyncio

import codec
import proto
from keymap import BTN_ALL


DEFAULT_RAW_PORT = 7373
MAX_CLIENTS = 2       # authed raw clients; extras are rejected at auth time
OUTBOX_CAP = 32       # per-client queued push frames; beyond this the peer is not reading
AUTH_DEADLINE_S = 5   # a connection must complete the auth prologue within this


def get_raw_port():
    """Read MYCRO_RAW_PORT from environment with safe integer validation."""
    val = os.getenv("MYCRO_RAW_PORT")
    if val:
        try:
            p = int(val)
            if 1024 <= p <= 65535:
                return p
        except Exception:
            pass
    return DEFAULT_RAW_PORT


def _now_ms():
    return int(time.monotonic() * 1000)


def _recv_into(conn, view):
    """Zero-allocation recv_into a caller-owned view.

    Returns:
      >0: byte count received
      None: EAGAIN (no data ready)
      0: peer closed / disconnected
    """
    try:
        n = conn.recv_into(view)
    except OSError as e:
        if e.args and e.args[0] == 11:
            return None  # EAGAIN
        return 0  # ENOTCONN (128) et al: peer is gone
    return n


def _recv(conn, nbytes):
    """Cold-path read up to nbytes from a non-blocking socket."""
    buf = bytearray(nbytes)
    r = _recv_into(conn, buf)
    if r is None:
        return None
    if r == 0:
        return b""
    return bytes(buf[:r])


class Client:
    """Encapsulates a connected raw protocol peer."""

    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.inbuf = bytearray()
        self.reasm = codec.Reassembler()
        self.outbox = []
        self.authed = False
        self.alive = True
        self.deadline = time.monotonic() + AUTH_DEADLINE_S
        self.img_stream_seen = False

    def enqueue(self, frame):
        if self.alive and len(self.outbox) < OUTBOX_CAP:
            self.outbox.append((frame, 0))


class ProtoContext:
    """Platform wiring adapter for proto.Dispatcher handlers."""

    def __init__(self, runtime, macro_runner):
        self.runtime = runtime
        self._macro_runner = macro_runner

    def submit(self, code, hold_ms=None):
        return self._macro_runner.submit(code, hold_ms=hold_ms, source="raw")

    def status(self):
        return self._macro_runner.get_status()

    def abort(self):
        return self._macro_runner.abort()

    def device_info(self, gc_first=False):
        import gc
        if gc_first:
            gc.collect()
        u = os.uname()
        return {"board": u.machine, "fw": u.release, "free_mem": gc.mem_free()}


class RawServer:
    """Protocol v1 TCP raw message channel server."""

    def __init__(self, pool, runtime, macro_runner, token=None, port=None):
        self.pool = pool
        self.runtime = runtime
        self.macro_runner = macro_runner
        self.token = token
        self.port = port or get_raw_port()
        self.clients = []
        self.dispatcher = proto.Dispatcher()
        self.proto_ctx = ProtoContext(runtime, macro_runner)

        # Hook listeners
        self.on_chunk = None       # fn(client, payload) -> bool
        self.on_envelope = None    # fn(client, env) -> bool (True if handled)
        self.on_disconnect = None  # fn(client)

    def broadcast(self, env):
        """Stamp and enqueue a server-initiated push to every authed client.
        Returns the number of clients queued."""
        env["ts"] = _now_ms()
        frame = codec.pack_frame(codec.KIND_JSON, codec.encode_envelope(env))
        n = 0
        for c in self.clients:
            if c.authed:
                c.enqueue(frame)
                n += 1
        return n

    def _service_client(self, c, now):
        active = False
        conn = c.conn

        # Auth phase
        if not c.authed:
            if now > c.deadline:
                c.alive = False
                return True
            chunk = _recv(conn, codec.AUTH_LEN - len(c.inbuf))
            if chunk is None:
                return False
            if chunk == b"":
                c.alive = False
                return True
            c.inbuf += chunk
            if len(c.inbuf) >= codec.AUTH_LEN:
                authed_count = sum(1 for x in self.clients if x.authed)
                if codec.check_auth_header(bytes(c.inbuf), self.token) and authed_count < MAX_CLIENTS:
                    c.authed = True
                    print("raw client authed:", c.addr)
                else:
                    print("raw auth rejected:", c.addr)
                    c.alive = False
            return True

        # Frame reading loop (bounded per tick to prevent starvation)
        for _ in range(8):
            if not c.alive:
                return True
            r = _recv_into(conn, c.reasm.write_view())
            if r is None:
                break
            active = True
            if r == 0:
                c.alive = False
                return True
            try:
                frames = c.reasm.feed(r)
            except codec.CodecError as e:
                print("raw frame error:", c.addr, e)
                c.alive = False
                return True

            for kind, payload in frames:
                if not c.alive:
                    return True
                if kind != codec.KIND_JSON:
                    handled = False
                    if self.on_chunk:
                        handled = self.on_chunk(c, payload)
                    resp = None if handled else proto.error_response(
                        None, "unsupported_kind", "no binary consumer registered"
                    )
                else:
                    try:
                        env = codec.decode_envelope(payload)
                    except codec.CodecError as e:
                        resp = proto.error_response(None, "bad_envelope", str(e))
                    else:
                        handled = False
                        if self.on_envelope:
                            handled = self.on_envelope(c, env)
                        if handled:
                            resp = None
                        else:
                            resp = self.dispatcher.dispatch(env, self.proto_ctx)

                if resp is not None:
                    resp["ts"] = _now_ms()
                    c.enqueue(codec.pack_frame(codec.KIND_JSON, codec.encode_envelope(resp)))

        # Flush outbox
        while c.outbox:
            frame, off = c.outbox[0]
            try:
                sent = conn.send(frame[off:])
            except OSError:
                break
            active = True
            off += sent
            if off >= len(frame):
                c.outbox.pop(0)
            else:
                c.outbox[0] = (frame, off)

        return active

    async def loop(self):
        """Asyncio driver for the raw TCP server."""
        if not self.pool:
            print("No socketpool available for raw server")
            return

        try:
            s = self.pool.socket(self.pool.AF_INET, self.pool.SOCK_STREAM)
            s.setsockopt(self.pool.SOL_SOCKET, self.pool.SO_REUSEADDR, 1)
            s.settimeout(0)
            s.bind(("0.0.0.0", self.port))
            s.listen(MAX_CLIENTS)
            print(f"Raw protocol listening on :{self.port} (v{codec.PROTO_VERSION})")

            while True:
                busy = False
                while True:
                    try:
                        conn, addr = s.accept()
                    except OSError:
                        break
                    conn.settimeout(0)
                    client = Client(conn, addr)
                    self.clients.append(client)
                    print("raw conn from", addr)
                    busy = True

                now = time.monotonic()
                for c in self.clients[:]:
                    try:
                        busy = self._service_client(c, now) or busy
                    except Exception as e:
                        print("raw client error:", c.addr, e)
                        traceback.print_exception(e)
                        c.alive = False

                    if not c.alive:
                        if self.on_disconnect:
                            self.on_disconnect(c)
                        self.clients.remove(c)
                        try:
                            c.conn.close()
                        except Exception:
                            pass
                        busy = True

                await asyncio.sleep(0 if busy else 0.01)
        except Exception as e:
            print("raw server disabled:", e)
            traceback.print_exception(e)
