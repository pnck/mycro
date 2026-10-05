#!/usr/bin/env python3
"""MYCRO protocol v1 reference client — see docs/protocol.md.

Self-contained (stdlib only) by design: this file doubles as the
executable specification for third-party host companions.

Usage:
    client.py HOST status
    client.py HOST submit 'hello\\enter'
    client.py HOST submit - < macro.txt      # DSL source from stdin
    client.py HOST abort
    client.py HOST info
    client.py HOST event net.msg x=120 y=-35
    client.py HOST listen                    # print pushes until ^C

Auth: --token or $MYCRO_TOKEN; must match the device's settings.toml.
A wrong token is indistinguishable from a hang until the first recv —
the device closes silently (see the prologue section of protocol.md).
"""

import argparse
import hashlib
import json
import os
import socket
import struct
import sys
import time

AUTH_MAGIC = b"MYCR"
PROTO_VERSION = 1
DEFAULT_PORT = 7373


class ProtocolError(Exception):
    pass


class Client:
    def __init__(self, host, port=DEFAULT_PORT, token="", timeout=5.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        self.sock.sendall(AUTH_MAGIC + bytes([PROTO_VERSION]) + digest)
        self._buf = bytearray()
        self._req_id = 0

    def close(self):
        self.sock.close()

    # --- framing ---
    def _recv_exact(self, n):
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(4096)
            except (socket.timeout, TimeoutError):
                raise  # a slow device is not a closed connection
            except OSError as e:
                # RST surfaces here when the device closes with data in flight
                raise ProtocolError("connection closed by device (auth failed?)") from e
            if not chunk:
                raise ProtocolError("connection closed by device (auth failed?)")
            self._buf += chunk
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def recv_envelope(self):
        """Next inbound envelope. Binary-kind frames surface as a
        {"type": "<binary>"} placeholder until Phase 3 defines them."""
        (hdr,) = struct.unpack("<I", self._recv_exact(4))
        kind, length = hdr >> 31, hdr & 0x7FFFFFFF
        payload = self._recv_exact(length)
        if kind != 0:
            return {"v": PROTO_VERSION, "type": "<binary>", "id": 0, "ts": 0,
                    "payload": {"bytes": length}}
        return json.loads(payload.decode("utf-8"))

    def send_envelope(self, env):
        body = json.dumps(env).encode("utf-8")
        self.sock.sendall(struct.pack("<I", len(body)) + body)

    # --- API ---
    def call(self, type_, payload=None):
        """Send a request and return its response; interleaved pushes are
        printed to stderr."""
        self._req_id = (self._req_id + 1) & 0x7FFFFFFF
        eid = self._req_id
        self.send_envelope({"v": PROTO_VERSION, "type": type_, "id": eid,
                            "ts": int(time.time() * 1000),
                            "payload": payload or {}})
        while True:
            env = self.recv_envelope()
            if env.get("id") == eid:
                return env
            print("push:", _fmt(env), file=sys.stderr)

    def listen(self):
        while True:
            print(_fmt(self.recv_envelope()))


def _fmt(env):
    return json.dumps(env, ensure_ascii=False)


def _parse_fields(pairs):
    fields = {}
    for p in pairs:
        k, sep, v = p.partition("=")
        if not sep:
            raise ProtocolError(f"field must be name=int, got {p!r}")
        fields[k] = int(v, 0)
    return fields


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--token", default=os.environ.get("MYCRO_TOKEN", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("abort")
    sub.add_parser("info")
    p_submit = sub.add_parser("submit")
    p_submit.add_argument("code", help="DSL source, or '-' for stdin")
    p_event = sub.add_parser("event")
    p_event.add_argument("name")
    p_event.add_argument("fields", nargs="*")
    sub.add_parser("listen")
    args = ap.parse_args(argv)

    c = Client(args.host, args.port, args.token)
    try:
        if args.cmd == "listen":
            c.listen()
            return 0
        if args.cmd == "submit":
            code = sys.stdin.read() if args.code == "-" else args.code
            env = c.call("macro.submit", {"code": code})
        elif args.cmd == "event":
            env = c.call("event.push",
                         {"name": args.name, "fields": _parse_fields(args.fields)})
        else:
            env = c.call("macro." + args.cmd if args.cmd != "info" else "device.info")
        print(_fmt(env))
        return 1 if env.get("type") == "error" else 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
