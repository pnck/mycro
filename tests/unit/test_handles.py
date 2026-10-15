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
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\set{a}{0}\\call{probe.n}", rt)
    assert seen == [0]


def test_move_semantics_runtime():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {
        "n": ([], "int", lambda: seen.append(len(rt._handles)) or 0),
    })
    # use{$b} after the move works; the id stays singular
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\set{b}{$a}"
        "\\call{img.use}{$b}\\call{probe.n}", rt)
    assert seen == [1]


def test_moved_source_is_dead_compile_time():
    rt = make_runtime()
    _, err = Macro(MockHID(), rt).compile(
        "\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\set{b}{$a}\\call{img.use}{$a}")
    assert err and "must hold a snap handle" in err


def test_stale_ret_is_dead_compile_time():
    rt = make_runtime()
    _, err = Macro(MockHID(), rt).compile(
        "\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\call{img.use}{$ret}")
    assert err and "must hold a snap handle" in err


def test_non_handle_return_does_not_claim():
    rt = make_runtime()
    run("\\call{img.snap}{1,2,3,4}\\set{a}{$ret}\\call{img.use}{$a}", rt)
    # $ret after use{} holds int 1 — no new claim; ownership stays with a
    assert rt._fresh is None


# --- \rect literal (compile-time pixel constant -> const store -> handle) ---

RECT2 = "\\rect{\\row{0xFF0000,0x00FF00},\\row{0x0000FF,0xFFFFFF}}"


def test_rect_literal_goes_to_const_store_not_bytecode():
    rt = make_runtime()
    bc = compile_ok("\\set{t}{" + RECT2 + "}", rt)
    assert len(rt._consts) == 1
    e = rt._consts[0]
    assert e["kind"] == "snap"
    assert e["data"]["w"] == 2 and e["data"]["h"] == 2
    assert e["data"]["rgb"] == bytes([255, 0, 0, 0, 255, 0,
                                      0, 0, 255, 255, 255, 255])
    # bytecode carries only the const id, no pixel data
    assert bytes([255, 0, 0, 0, 255, 0]) not in bytes(bc)


def test_rect_literal_executes_and_interops():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int", lambda: seen.append(1) or 0)})
    run("\\set{t}{" + RECT2 + "}"
        "\\call{img.at}{$t}{1,0}\\ifnum{$ret}{=}{0x00FF00}{\\call{probe.n}}"
        "\\call{img.diff}{$t}{$t}{0}\\ifnum{$ret}{=}{0}{\\call{probe.n}}", rt)
    assert seen == [1, 1]
    assert rt._handles == {}  # released at exit


def test_rect_literal_as_call_arg():
    rt = make_runtime()
    seen = []
    rt.register_ns("probe", {"n": ([], "int", lambda: seen.append(1) or 0)})
    # rect literal directly as a handle-typed argument
    run("\\call{img.diff}{" + RECT2 + "}{" + RECT2 + "}{0}"
        "\\ifnum{$ret}{=}{0}{\\call{probe.n}}", rt)
    assert seen == [1]


def test_rect_type_environment():
    rt = make_runtime()
    # the variable records type snap: handle-arg check works...
    compile_ok("\\set{t}{" + RECT2 + "}\\call{img.use}{$t}", rt)
    # ...and \add rejects it
    assert "cannot modify a snap handle" in compile_err(
        "\\set{t}{" + RECT2 + "}\\add{t}{1}", rt)


def test_rect_literal_errors():
    rt = make_runtime()
    assert "ragged" in compile_err(
        "\\set{t}{\\rect{\\row{0xFF0000},\\row{0x1,0x2}}}", rt)
    compile_err("\\set{t}{\\rect{\\row{zz}}}", rt)          # bad hex
    compile_err("\\set{t}{\\rect{\\row{0x1000000}}}", rt)   # color range
    compile_err("\\set{t}{\\rect{}}", rt)                   # empty
    assert "value-position" in compile_err("\\rect{\\row{0xFF0000}}", rt)
    _, err = Macro(MockHID()).compile("\\set{t}{\\rect{\\row{0xFF0000}}}")
    assert "runtime registry" in err                        # no runtime
