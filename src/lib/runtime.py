r"""Runtime registry: extension namespaces, signals, slots, handle tables.

Middleware per spec/vm.md Part II. The VM core holds plain i32 registers;
resource ownership (handles) lives HERE — the VM only guarantees that
every register write is mediated through reg_write() and that executions
are bracketed by release_all().

The Macro compiler consults the registry for \use / \call / \wait validation;
the VM calls into it for CALL_EXT / WAIT / READSLOT semantics.

Platform-agnostic: code.py wires concrete namespaces (sys/net/img); tests
wire fakes. Signature entries are (arg_types, return_type, callable);
arg types: 'int' | 'color' | 'str' | 'point' | 'rect' | handle-type names
(e.g. 'snap', 'tpl').
"""

import asyncio

try:
    _TimeoutError = asyncio.TimeoutError
except AttributeError:  # very old asyncio
    _TimeoutError = TimeoutError

# Middleware-facing special register addresses (mirror the VM's fixed
# addresses; the contract is spec/vm.md Part I §2)
REG_RET = 0xFF
REG_TIMEOUT = 0xFE


class Runtime:
    def __init__(self):
        self._ns = {}  # ns -> {fn_name: (arg_types, return_type, callable)}
        self._signals = {}  # name -> asyncio.Event
        self._slots = {}  # name -> {field: int}
        # handle ownership (spec/vm.md Part II §4)
        self._handles = {}      # id -> {"kind": str, "data": any}
        self._handle_seq = 0
        self._reg_handles = {}  # register index -> handle id
        self._fresh = None      # newest unclaimed handle id

    # --- registration (platform side) ---

    def register_ns(self, ns, fns):
        """fns: {fn_name: (arg_types, return_type, callable)}.
        See spec/vm.md Part II for the type discipline."""
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

    def get_return_type(self, ns, fn):
        entry = self._ns.get(ns, {}).get(fn)
        return entry[1] if entry and len(entry) == 3 else "int"

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
        self._fresh = None  # a fresh handle belongs to the call that made it
        return self._ns[ns][fn][2](*args)

    # --- handle ownership (register-write mediation) ---

    def new_handle(self, kind, data):
        """Create a resource handle. The fn returns the id; the VM's
        write of that value to a register claims ownership for it."""
        self._handle_seq += 1
        self._handles[self._handle_seq] = {"kind": kind, "data": data}
        self._fresh = self._handle_seq
        return self._handle_seq

    def claim(self, reg, value):
        """Claim the fresh handle for reg when the value written is it."""
        if self._fresh is not None and value == self._fresh:
            self._reg_handles[reg] = value
            self._fresh = None

    def reg_write(self, dst, src=None):
        """Mediate a register write: overwrite releases; a register
        source MOVES ownership to dst (spec/vm.md Part II §4)."""
        old = self._reg_handles.pop(dst, None)
        if old is not None:
            self._release(old)
        if src is not None:
            h = self._reg_handles.pop(src, None)
            if h is not None:
                self._reg_handles[dst] = h

    def _release(self, hid):
        self._handles.pop(hid, None)  # may be deferred/async in ports

    def release_all(self):
        """Execution bracket: start and every exit path."""
        self._handles.clear()
        self._reg_handles.clear()
        self._fresh = None

    def handle_data(self, value, kind):
        """Resolve a handle value to its data; None when invalid: unknown
        id, wrong kind, or stale (no register owns it anymore)."""
        e = self._handles.get(value)
        if e is None or e["kind"] != kind:
            return None
        if value not in self._reg_handles.values():
            return None
        return e["data"]

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
