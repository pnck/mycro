"""MYCRO Macro Execution Task Coordinator.

Manages execution state, asyncio background tasks, cancellation,
and result broadcasting to connected companion clients.
"""

import gc
import time
try:
    import asyncio
except ImportError:
    import _asyncio as asyncio

import proto
from keymap import BTN_ALL


def _now_ms():
    return int(time.monotonic() * 1000)


class MacroRunner:
    """Coordinates compiling, spawning, executing, and aborting macro runs."""

    def __init__(self, macro, raw_server=None, img_provider=None):
        self.macro = macro
        self.raw_server = raw_server
        self.img_provider = img_provider
        self.task = None
        self.state = {"state": "idle", "detail": "", "bytes": 0}

    def set_raw_server(self, raw_server):
        self.raw_server = raw_server

    def set_img_provider(self, img_provider):
        self.img_provider = img_provider

    def get_status(self):
        return self.state["state"], self.state["detail"] or None

    def get_status_string(self):
        body = self.state["state"]
        if self.state["detail"]:
            body += " " + self.state["detail"]
        return body

    def is_running(self):
        return self.state["state"] == "running"

    def abort(self):
        self.macro.hid.keys_release_all()
        self.macro.hid.mouse_release(BTN_ALL)
        if self.task and self.is_running():
            self.task.cancel()
            return True
        return False

    def submit(self, code, hold_ms=None, source="http"):
        if hold_ms is not None:
            if isinstance(hold_ms, bool) or not isinstance(hold_ms, int) or not (0 <= hold_ms <= 65535):
                return None, f"Compile Error: hold_ms must be an integer in [0, 65535], got {hold_ms!r}"

        try:
            bytecode, err = self.macro.compile(code, press_hold_ms=hold_ms)
        except Exception as e:
            return None, f"Compile failed: {type(e).__name__}: {e}"

        if err:
            return None, f"Compile Error: {err}"

        print(f"\n=== Compiled Bytecode ({len(bytecode)} bytes, via {source}) ===")
        print(self.macro.disassemble(bytecode))
        print("=" * 40)

        self.state.update(state="running", detail="", bytes=len(bytecode))
        self.task = asyncio.create_task(self._run(bytecode))
        return len(bytecode), None

    def _broadcast_result(self, t0):
        if not self.raw_server:
            return
        body = {"state": self.state["state"], "ms": _now_ms() - t0}
        if self.state["detail"]:
            body["detail"] = self.state["detail"]
        self.raw_server.broadcast(proto.push("macro.result", body))

    async def _run(self, bytecode):
        t0 = _now_ms()
        try:
            try:
                await self.macro.execute(bytecode)
                self.state["state"] = "done"
            except asyncio.CancelledError:
                self.state["state"] = "aborted"
                self._broadcast_result(t0)
                raise
            except Exception as e:
                self.state["state"] = "error"
                self.state["detail"] = str(e)
                print("Macro runtime error:", e)
            self._broadcast_result(t0)
        finally:
            if self.img_provider:
                await self.img_provider.unwatch_all()
            gc.collect()
