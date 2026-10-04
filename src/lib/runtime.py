r"""Runtime registry: extension namespaces, signals, and slot storage.

The Macro compiler consults the registry for \use / \call / \wait validation;
the VM calls into it for CALL_EXT / WAIT / READSLOT semantics.

Platform-agnostic: code.py wires concrete namespaces (sys now, net/img in
Phase 2/3); tests wire fakes. Signatures declare param types:
'int' | 'color' | 'str' | handle-type names (e.g. 'tpl', 'conn').
"""

import asyncio

try:
    _TimeoutError = asyncio.TimeoutError
except AttributeError:  # very old asyncio
    _TimeoutError = TimeoutError


class Runtime:
    def __init__(self):
        self._ns = {}  # ns -> {fn_name: (signature, callable)}
        self._signals = {}  # name -> asyncio.Event
        self._slots = {}  # name -> {field: int}

    # --- registration (platform side) ---

    def register_ns(self, ns, fns):
        """fns: {fn_name: (signature, callable)}."""
        self._ns.setdefault(ns, {}).update(fns)

    def register_signal(self, name):
        self._signals[name] = asyncio.Event()

    def fire_signal(self, name, fields=None):
        """Publish slot fields and wake waiters (event sources call this)."""
        if fields:
            self._slots[name] = dict(fields)
        self._signals[name].set()

    # --- compiler queries ---

    def has_namespace(self, ns):
        return ns in self._ns

    def get_signature(self, ns, fn):
        entry = self._ns.get(ns, {}).get(fn)
        return entry[0] if entry else None

    def has_signal(self, name):
        return name in self._signals

    def has_slot_ns(self, ns):
        return ns in self._signals or ns in self._ns

    # --- VM entry points ---

    def read_slot(self, name, field):
        """Slot reads are 'last published' semantics; missing field -> 0."""
        return self._slots.get(name, {}).get(field, 0)

    def call(self, ns, fn, args):
        """Invoke an ext function; may return a value or a coroutine."""
        return self._ns[ns][fn][1](*args)

    async def wait_signal(self, name, timeout_ms):
        """Wait for the NEXT fire of `name` after entry: a latch left by a
        fire with no waiter is discarded, so stale events never wake a fresh
        wait. Slot values remain readable as last-published state regardless.
        Returns True if fired, False on timeout."""
        ev = self._signals[name]
        ev.clear()
        if timeout_ms > 0:
            try:
                await asyncio.wait_for(ev.wait(), timeout_ms / 1000.0)
            except _TimeoutError:
                return False
        else:
            await ev.wait()
        return True
