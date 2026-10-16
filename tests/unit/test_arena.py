"""Static backing store (spec/vm.md Appendix A): SlotArena / BumpArena
semantics and their integration with handle ownership.

The point of the arenas is that bulk byte data never touches the GC
heap per operation — these tests pin the management rules: slots are
reused after release, exhaustion is a deterministic failure, and the
const arena resets wholesale per compile.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src" / "lib"))

from runtime import (BumpArena, Runtime, SlotArena,  # noqa: E402
                     CONST_ARENA_BYTES, SNAP_SLOT_BYTES, SNAP_SLOTS)


def test_slot_arena_alloc_free_reuse():
    a = SlotArena(16, 2)
    s0 = a.alloc()
    s1 = a.alloc()
    assert s0 is not None and s1 is not None and s0 != s1
    assert a.alloc() is None  # exhausted: deterministic, not a GC event
    a.free(s0)
    assert a.alloc() == s0  # freed slot comes back


def test_slot_arena_view_writes_land_in_place():
    a = SlotArena(8, 2)
    s = a.alloc()
    a.view(s, 4)[:] = b"\x01\x02\x03\x04"
    assert bytes(a.view(s, 4)) == b"\x01\x02\x03\x04"
    assert bytes(a.view(s, 8))[4:] == b"\x00" * 4  # rest untouched


def test_bump_arena_reset_reclaims_everything():
    a = BumpArena(64)
    assert a.add(b"x" * 40) == 0
    assert a.add(b"y" * 40) is None  # full: deterministic failure
    a.reset()
    assert a.add(b"z" * 40) == 0  # whole arena back after reset


def test_snap_pool_sized_for_full_rect():
    # one slot holds a maximum-size snapshot (RECT_MAX_EDGE^2 RGB)
    assert SNAP_SLOT_BYTES == 128 * 128 * 3
    rt = Runtime()
    # on demand: construction allocates NOTHING (spec/vm.md Appendix A)
    assert rt.snap_pool._grown == 0 and rt.snap_pool._free == []


def test_slot_arena_grows_one_slot_per_first_claim():
    a = SlotArena(16, 4)
    assert a._grown == 0
    s = a.alloc()
    assert a._grown == 1  # only the claimed slot's buffer exists
    a.free(s)
    a.alloc()
    assert a._grown == 1  # reuse: no further growth


def test_handle_release_returns_slot_to_pool():
    rt = Runtime()
    slot = rt.snap_pool.alloc()
    data = {"w": 1, "h": 1, "rgb": rt.snap_pool.view(slot, 3),
            "_arena": rt.snap_pool, "_slot": slot}
    hid = rt.new_handle("snap", data)
    rt._release(hid)
    assert len(rt.snap_pool._free) == 1  # slot returned, buffer kept
    assert rt.snap_pool.alloc() == slot  # and immediately reusable


def test_release_all_returns_every_slot():
    rt = Runtime()
    for _ in range(SNAP_SLOTS):
        slot = rt.snap_pool.alloc()
        rt.new_handle("snap", {"w": 1, "h": 1,
                               "rgb": rt.snap_pool.view(slot, 3),
                               "_arena": rt.snap_pool, "_slot": slot})
    assert len(rt.snap_pool._free) == 0
    rt.release_all()
    assert len(rt.snap_pool._free) == SNAP_SLOTS


def test_plain_handle_release_untouched_by_arenas():
    rt = Runtime()
    hid = rt.new_handle("snap", {"w": 1, "h": 1, "rgb": bytes(3)})  # no _arena
    rt._release(hid)
    assert rt.snap_pool._grown == 0 and rt.snap_pool._free == []


def test_const_rgb_lives_in_bump_arena():
    rt = Runtime()
    cid = rt.add_const("snap", {"w": 1, "h": 1, "rgb": b"\x0a\x0b\x0c"})
    e = rt.const_entry(cid)
    assert bytes(e["data"]["rgb"]) == b"\x0a\x0b\x0c"
    # reset (new compile) reclaims the arena; the entry list is cleared too
    rt.clear_consts()
    assert rt.const_entry(cid) is None
    cid2 = rt.add_const("snap", {"w": 1, "h": 1, "rgb": b"\x01\x02\x03"})
    assert rt._consts[cid2]["data"]["_off"] == 0  # bumped from the start again


def test_const_arena_overflow_is_a_compile_error():
    rt = Runtime()
    try:
        rt.add_const("snap", {"w": 128, "h": 128,
                              "rgb": bytes(CONST_ARENA_BYTES + 3)})
        assert False, "expected ValueError"
    except ValueError as e:
        assert "const arena" in str(e)


def test_slot_arena_double_free_prevented():
    a = SlotArena(16, 2)
    s = a.alloc()
    a.free(s)
    a.free(s)  # duplicate free must not corrupt free-list
    assert len(a._free) == 1
    assert a.alloc() == s
    assert a.alloc() is not None
    assert a.alloc() is None  # pool exhausted cleanly, no phantom slot

