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
REG_ERR = 0xFD
MAX_SLOTS = 256

# Standard error codes (spec/runtime.md §3.0)
ERR_OK = 0
ERR_GENERIC = 1
ERR_INVALID_ARG = 2
ERR_NO_PROVIDER = 3
ERR_OUT_OF_BOUNDS = 4
ERR_HANDLE_INVALID = 5
ERR_RESOURCE_EXHAUSTED = 6
ERR_TIMEOUT = 7
ERR_HANDLE_MISUSE = 8

# --- static backing store (spec/vm.md Appendix A) ---
# Profile constants, same standing as MAX_REGS: platform budgets, not
# spec limits. Bulk byte data NEVER comes from the GC heap per-operation;
# it lives in these arenas. Arenas allocate ON DEMAND (first touch is a
# cold path and may pay for it), are then owned by the middleware
# forever — never returned to the GC — and the steady-state hot path
# allocates nothing.
SNAP_SLOT_BYTES = 128 * 128 * 3  # one full-size snapshot (RECT_MAX_EDGE^2 RGB)
SNAP_SLOTS = 8                   # live snapshots a macro may hold at once
CONST_ARENA_BYTES = 256 * 1024   # \rect literal pixels, per-compile lifetime


class SlotArena:
    """A pool of equal-size slots, grown on demand up to a fixed count.

    Each slot's buffer is allocated on first claim (cold path, one time)
    and kept forever after; free/alloc past warmup are pure free-list
    operations — no heap involvement, no GC churn, no fragmentation.
    Exhaustion returns None; the caller fails the operation
    deterministically ($ret < 0), never a GC retry storm."""

    def __init__(self, slot_bytes, count):
        self._slots = [None] * count  # index -> bytearray, lazily allocated
        self.slot_bytes = slot_bytes
        self._free = []      # indices of allocated-and-free slots
        self._grown = 0      # high-water count of allocated slots

    def alloc(self):
        """Slot index, or None when the pool is exhausted or allocation fails."""
        if self._free:
            return self._free.pop()
        if self._grown < len(self._slots):
            i = self._grown
            try:
                self._slots[i] = bytearray(self.slot_bytes)  # one-time, cold
            except MemoryError:
                return None
            self._grown += 1
            return i
        return None

    def free(self, slot):
        if slot is not None and slot not in self._free:
            self._free.append(slot)

    def view(self, slot, nbytes):
        """Writable view of the slot's first nbytes."""
        return memoryview(self._slots[slot])[:nbytes]


class BumpArena:
    """A bump allocator for compile-lifetime constants, allocated on
    first use and then self-managed.

    The backing buffer appears on the first add (compile is a cold path
    by definition); reset() frees everything logically — the buffer
    itself is KEPT. Per-compile churn is a pointer bump, not garbage."""

    def __init__(self, nbytes):
        self._cap = nbytes
        self._buf = None  # allocated on first add
        self._used = 0

    def add(self, data):
        """Offset of the stored bytes, or None when the arena is full or allocation fails."""
        n = len(data)
        if n > self._cap:
            return None
        if self._buf is None:
            try:
                self._buf = bytearray(self._cap)  # one-time, cold
            except MemoryError:
                return None
        if self._used + n > self._cap:
            return None
        off = self._used
        self._buf[off:off + n] = data
        self._used = off + n
        return off

    def view(self, off, nbytes):
        return memoryview(self._buf)[off:off + nbytes]

    def reset(self):
        self._used = 0


class Runtime:
    def __init__(self):
        self._ns = {}  # ns -> {fn_name: (arg_types, return_type, callable)}
        self._signals = {}  # name -> asyncio.Event
        self._slots = {}  # (name, field) -> {"val": int, "pending": int}
        self._waiters = {}  # (name, field) -> list of asyncio.Event
        # handle ownership (spec/vm.md Part II §4)
        self._handles = {}      # id -> {"kind": str, "data": any}
        self._handle_seq = 0
        self._reg_handles = {}  # register index -> handle id
        self._fresh = None      # newest unclaimed handle id
        # static backing store (§6): snapshot slot pool + const bump arena
        self.snap_pool = SlotArena(SNAP_SLOT_BYTES, SNAP_SLOTS)
        self._const_arena = BumpArena(CONST_ARENA_BYTES)
        # compile-time constant resources (spec/vm.md Part II §5):
        # \rect literals land here, NOT in the bytecode; reset per compile
        self._consts = []       # [{"kind": str, "data": any}]

    def clear_consts(self):
        self._consts.clear()
        self._const_arena.reset()

    def add_const(self, kind, data):
        """Store a compile-time constant. A "rgb" bytes payload is moved
        into the const bump arena (§6); overflow raises ValueError — the
        compiler turns it into a compile error."""
        if isinstance(data, dict) and isinstance(data.get("rgb"), (bytes, bytearray)):
            off = self._const_arena.add(data["rgb"])
            if off is None:
                raise ValueError("const arena full ({}B budget)".format(
                    CONST_ARENA_BYTES))
            data = dict(data, _off=off, _n=len(data["rgb"]))
            del data["rgb"]
        self._consts.append({"kind": kind, "data": data})
        return len(self._consts) - 1

    def const_entry(self, cid):
        if 0 <= cid < len(self._consts):
            e = self._consts[cid]
            data = e["data"]
            if isinstance(data, dict) and "_off" in data:
                return {"kind": e["kind"], "data": {
                    "w": data["w"], "h": data["h"],
                    "rgb": self._const_arena.view(data["_off"], data["_n"])}}
            return e
        return None

    # --- registration (platform side) ---

    def register_ns(self, ns, fns):
        """fns: {fn_name: (arg_types, return_type, callable)}.
        See spec/vm.md Part II for the type discipline."""
        self._ns.setdefault(ns, {}).update(fns)

    def register_signal(self, name):
        self._signals[name] = asyncio.Event()

    def fire_signal(self, name, fields=None):
        """Publish slot fields and wake waiters per spec/runtime.md §1.1 #2.
        Returns True on success, False if MAX_SLOTS capacity exhausted."""
        if not fields:
            key = (name, "val")
            if key not in self._slots:
                if len(self._slots) >= MAX_SLOTS:
                    return False
                self._slots[key] = {"val": 0, "pending": 1}
            else:
                self._slots[key]["pending"] = 1
            for ev in list(self._waiters.get(key, [])):
                ev.set()
            if name in self._signals:
                self._signals[name].set()
            return True

        new_count = sum(1 for k in fields if (name, k) not in self._slots)
        if len(self._slots) + new_count > MAX_SLOTS:
            return False

        for k, v in fields.items():
            key = (name, k)
            if key not in self._slots:
                self._slots[key] = {"val": v, "pending": 1}
            else:
                self._slots[key]["val"] = v
                self._slots[key]["pending"] = 1
            for ev in list(self._waiters.get(key, [])):
                ev.set()

        if name in self._signals:
            self._signals[name].set()
        return True

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
        slot = self._slots.get((name, field))
        return slot["val"] if slot is not None else 0

    def slot_pending(self, name, field):
        """Arrival latch status (0 or 1)."""
        slot = self._slots.get((name, field))
        return slot["pending"] if slot is not None else 0

    async def wait_slot(self, name, field, timeout_ms):
        """Wait on slot receptor per spec/runtime.md §1.1 #3:
        - If slot.pending == 1: consume immediately (pending=0), return True ($timeout=0).
        - If slot.pending == 0 and timeout_ms <= 0: return False ($timeout=1).
        - If slot.pending == 0 and timeout_ms > 0: suspend until projection or timeout.
          Re-inspect slot.pending upon timeout/wake. Return True if arrived, False if timeout.
        """
        key = (name, field)
        slot = self._slots.get(key)
        if slot is None:
            if len(self._slots) >= MAX_SLOTS:
                return False
            slot = {"val": 0, "pending": 0}
            self._slots[key] = slot

        if slot["pending"] == 1:
            slot["pending"] = 0
            return True

        if timeout_ms <= 0:
            return False

        ev = asyncio.Event()
        waiters = self._waiters.setdefault(key, [])
        waiters.append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout_ms / 1000.0)
        except _TimeoutError:
            pass
        finally:
            try:
                waiters.remove(ev)
            except ValueError:
                pass

        if slot["pending"] == 1:
            slot["pending"] = 0
            return True
        return False

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
        if self._fresh is not None:
            if value == self._fresh:
                self._reg_handles[reg] = value
                self._fresh = None
            else:
                orphan = self._fresh
                self._fresh = None
                self._release(orphan)

    def is_handle(self, reg):
        """Check if register holds a typed handle reference."""
        return reg in self._reg_handles

    def get_handle(self, reg):
        """Get the handle ID held by register, or None."""
        return self._reg_handles.get(reg)

    def reg_write(self, dst, value=0, src=None, handle=False):
        """Mediate a register write per spec/vm.md §7:
        1. Detach destination's old typed reference and remember displaced handle.
        2. Install value with type selected by handle. For SET reg<-reg, source
           remains a shared reference. Self-assignment reinstalls the detached reference.
        3. Scan all registers for remaining references to displaced handle.
           Release displaced handle when zero references remain.
        """
        old_hid = self._reg_handles.pop(dst, None)
        if handle:
            if src is not None and src == dst and old_hid is not None:
                self._reg_handles[dst] = old_hid
            elif src is not None and src in self._reg_handles:
                self._reg_handles[dst] = self._reg_handles[src]
            else:
                self._reg_handles[dst] = value
        if old_hid is not None and old_hid not in self._reg_handles.values():
            self._release(old_hid)

    def _release(self, hid):
        e = self._handles.pop(hid, None)  # may be deferred/async in ports
        if e is not None:
            d = e["data"]
            # arena-backed resources return their slot to the pool (§6);
            # plain data just drops out of the table
            if isinstance(d, dict) and d.get("_arena") is not None:
                d["_arena"].free(d["_slot"])

    def release_handles(self):
        """Execution bracket: start and every exit path (spec/runtime.md §4 #6)."""
        for hid in list(self._handles):
            self._release(hid)
        self._handles.clear()
        self._reg_handles.clear()
        self._fresh = None

    release_all = release_handles

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
        """Wait for the NEXT fire of `name` after entry (legacy wrapper)."""
        ev = self._signals.get(name)
        if ev is None:
            ev = asyncio.Event()
            self._signals[name] = ev
        ev.clear()
        if timeout_ms > 0:
            try:
                await asyncio.wait_for(ev.wait(), timeout_ms / 1000.0)
            except _TimeoutError:
                return False
        else:
            await ev.wait()
        return True
