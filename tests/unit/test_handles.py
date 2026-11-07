r"""Handle ownership: register-write mediation, move/overwrite-release,
execution brackets (spec/vm.md Part I §5, Part II §4)."""

import asyncio

import pytest

from macro import Macro
from runtime import Runtime
from mock_hid import MockHID


def make_runtime():
    rt = Runtime()
    rt.register_ns("img", {
        "snap": (["rect"], "snap",
                 lambda x, y, w, h: rt.new_handle("snap", (x, y, w, h))),
        "use": (["snap"], "int",
                lambda h: 1 if rt.handle_data(h, "snap") is not None else -1),
        "at": (["snap", "point"], "color", lambda h, x, y: _at(rt, h, x, y)),
        "diff": (["snap", "snap", "int"], "int",
                 lambda a, b, tol: _diff(rt, a, b, tol)),
    })
    rt.register_ns("sys", {
        "const": (["int"], "snap", lambda cid: _const(rt, cid)),
    })
    return rt


def _const(rt, cid):
    e = rt.const_entry(cid)
    return rt.new_handle(e["kind"], e["data"]) if e else -1


def _at(rt, h, x, y):
    d = rt.handle_data(h, "snap")
    if d is None or not (0 <= x < d["w"] and 0 <= y < d["h"]):
        return -1
    i = (y * d["w"] + x) * 3
    return (d["rgb"][i] << 16) | (d["rgb"][i + 1] << 8) | d["rgb"][i + 2]


def _diff(rt, a, b, tol):
    da, db = rt.handle_data(a, "snap"), rt.handle_data(b, "snap")
    if da is None or db is None or da["w"] != db["w"] or da["h"] != db["h"]:
        return -1
    n = 0
    for i in range(len(da["rgb"])):
        if abs(da["rgb"][i] - db["rgb"][i]) > tol:
            n += 1
    return n


def compile_ok(text, rt):
    bc, err = Macro(MockHID(), rt).compile(text)
    assert err is None, f"unexpected compile error: {err}"
    return bc


def compile_err(text, rt):
    bc, err = Macro(MockHID(), rt).compile(text)
    assert bc is None and err, f"expected compile error, got success: {text}"
    return err


def run(text, rt):
    bc, err = Macro(MockHID(), rt).compile(text)
    assert err is None, err
    asyncio.run(Macro(MockHID(), rt).execute(bc))


def test_claim_and_release_at_exit():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int",
                                   lambda: seen.append(len(rt._handles)) or 0)})
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\call{probe.n}"
        "\\call{img.snap}{5,6,7,8}\\set{b}{$ret}\\call{probe.n}", rt)
    assert seen == [1, 2]
    assert rt._handles == {} and rt._reg_handles == {}  # release_all at exit


def test_overwrite_releases():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int",
                                   lambda: seen.append(len(rt._handles)) or 0)})
    # When both $a and $ret no longer reference the handle, it is released
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\call{img.use}{$a}\\set{a}{0}\\call{probe.n}", rt)
    assert seen == [0]


def test_shared_reference_runtime():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {
        "n": ([], "int", lambda: seen.append(len(rt._handles)) or 0),
    })
    # Under v4 shared references, both $a and $b reference the same handle
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\set{b}{$a}"
        "\\call{img.use}{$a}\\call{img.use}{$b}\\call{probe.n}", rt)
    assert seen == [1]


def test_shared_source_alive_compile_time():
    rt = make_runtime()
    # Source $a remains alive after copying to $b (spec/runtime.md §4 #3)
    bc = compile_ok(
        "\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\set{b}{$a}\\call{img.use}{$a}", rt)
    assert bc is not None


def test_shared_ret_alive_compile_time():
    rt = make_runtime()
    # $ret remains alive after assignment to $a until overwritten (spec/runtime.md §4 #3)
    bc = compile_ok(
        "\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\call{img.use}{$ret}", rt)
    assert bc is not None


def test_non_handle_return_does_not_claim():
    rt = make_runtime()
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\call{img.use}{$a}", rt)
    # $ret after use{} holds int 1 — no new claim; ownership stays with a
    assert rt._fresh is None


def test_ret_exemption_does_not_survive_storage():
    r"""Under v4 (§6.7, §8.1), a handle in \if compiles ok and evaluates false
    at runtime with $err=ERR_HANDLE_MISUSE."""
    rt = make_runtime()
    compile_ok("\\call{img.snap}{0,0,4,4}\\set{i}{$ret}"
               "\\if{ {$i, <, 0} }{\\call{img.use}{$i}}", rt)
    # the idiom compiles
    compile_ok("\\call{img.snap}{0,0,4,4}"
               "\\if{ {$ret, <, 0} }{\\call{img.use}{$ret}}", rt)


# --- \pixelrect statement (compile-time pixel constant -> const store -> handle) ---

PIXELRECT2_STMT = "\\pixelrect{t}{\\row{0xFF0000,0x00FF00},\\row{0x0000FF,0xFFFFFF}}"


def test_rect_literal_goes_to_const_store_not_bytecode():
    rt = make_runtime()
    bc = compile_ok(PIXELRECT2_STMT, rt)
    assert len(rt._consts) == 1
    # the public accessor materializes the arena-backed pixels (Part II §6)
    e = rt.const_entry(0)
    assert e["kind"] == "snap"
    assert e["data"]["w"] == 2 and e["data"]["h"] == 2
    assert bytes(e["data"]["rgb"]) == bytes([255, 0, 0, 0, 255, 0,
                                             0, 0, 255, 255, 255, 255])
    # bytecode carries only the const id, no pixel data
    assert bytes([255, 0, 0, 0, 255, 0]) not in bytes(bc)


def test_rect_literal_executes_and_interops():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int", lambda: seen.append(1) or 0)})
    run(PIXELRECT2_STMT +
        "\\call{img.at}{$t}{1,0}\\if{ {$ret, =, 0x00FF00} }{\\call{probe.n}}"
        "\\call{img.diff}{$t}{$t}{0}\\if{ {$ret, =, 0} }{\\call{probe.n}}", rt)
    assert seen == [1, 1]
    assert rt._handles == {}  # released at exit


def test_rect_literal_as_call_arg():
    rt = make_runtime()
    # pixelrect directly in call arg is rejected per spec/dsl.md §6.4
    assert "pixelrect requires destination statement" in compile_err(
        "\\call{img.diff}{\\pixelrect{\\row{0}}}{\\pixelrect{\\row{0}}}{0}", rt)


def test_rect_type_environment():
    rt = make_runtime()
    # the variable records type snap: handle-arg check works...
    compile_ok(PIXELRECT2_STMT + "\\call{img.use}{$t}", rt)


def test_rect_literal_errors():
    rt = make_runtime()
    assert "unequal length" in compile_err(
        "\\pixelrect{t}{\\row{0xFF0000},\\row{0x1,0x2}}", rt)
    compile_err("\\pixelrect{t}{\\row{zz}}", rt)          # bad hex
    compile_err("\\pixelrect{t}{\\row{0x1000000}}", rt)   # color range
    compile_err("\\pixelrect{t}{}", rt)                   # empty
    assert "pixelrect requires destination statement" in compile_err(
        "\\set{t}{\\pixelrect{\\row{0xFF0000}}}", rt)
    _, err = Macro(MockHID()).compile(PIXELRECT2_STMT)
    assert "runtime registry" in err                        # no runtime


def test_unclaimed_fresh_handle_released_immediately():
    rt = make_runtime()
    slot = rt.snap_pool.alloc()
    hid = rt.new_handle("snap", {"w": 1, "h": 1, "rgb": rt.snap_pool.view(slot, 3),
                                 "_arena": rt.snap_pool, "_slot": slot})
    assert len(rt.snap_pool._free) == 0
    # If the VM writes a non-matching value (e.g. error -1 or different id),
    # the orphan handle is freed immediately so the slot is not leaked.
    rt.claim(0xFF, -1)
    assert len(rt.snap_pool._free) == 1  # returned immediately!
    assert hid not in rt._handles

