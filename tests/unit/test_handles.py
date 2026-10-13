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
    })
    return rt


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
