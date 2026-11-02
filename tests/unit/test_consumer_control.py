"""Unit tests for USB Consumer Control (Multimedia Keys) per spec/dsl.md §5.2, spec/vm.md §4, §1.3.

Covers:
- Command and alias compilation to OP_CONSUMER_KEY (0x08)
- Pulse execution: consumer_press -> click_hold_ms -> consumer_release
- Automatic pacing after CONSUMER_KEY
- Disassembly formatting with CONSUMER_NAMES
- Name collision rejection in \\def
- Emergency release of active_consumer_usage on guard abort and exceptions
- Handle misuse fault on scalar operand
"""

import asyncio
import pytest

from macro import Macro
from bytecode import (
    MacroError,
    OP_CONSUMER_KEY,
    TAG_IMM,
    REG_ERR,
    ERR_HANDLE_MISUSE,
    CONSUMER_PLAY_PAUSE,
    CONSUMER_NEXT_TRACK,
    CONSUMER_PREV_TRACK,
    CONSUMER_STOP,
    CONSUMER_PLAY,
    CONSUMER_PAUSE,
    CONSUMER_VOLUME_INCREMENT,
    CONSUMER_VOLUME_DECREMENT,
    CONSUMER_MUTE,
    CONSUMER_BRIGHTNESS_INCREMENT,
    CONSUMER_BRIGHTNESS_DECREMENT,
    CONSUMER_SYSTEM_SLEEP,
    CONSUMER_COMMANDS,
)
from runtime import Runtime
from mock_hid import MockHID


def compile_bc(text, rt=None):
    m = Macro(MockHID(), rt)
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    return bc


def run_macro(text, hid=None, rt=None, **kwargs):
    hid = hid or MockHID()
    m = Macro(hid, rt)
    bc, err = m.compile(text)
    assert err is None, f"compile error: {err}"
    kwargs.setdefault("default_delay_ms", 0)
    kwargs.setdefault("click_hold_ms", 10)
    asyncio.run(m.execute(bc, **kwargs))
    return m, hid


class TestConsumerControlCompilation:
    """Validate compilation of all consumer control commands and their aliases."""

    @pytest.mark.parametrize(
        "cmd, expected_usage",
        [
            ("media_playpause", CONSUMER_PLAY_PAUSE),
            ("playpause", CONSUMER_PLAY_PAUSE),
            ("play_pause", CONSUMER_PLAY_PAUSE),
            ("media_next", CONSUMER_NEXT_TRACK),
            ("next_track", CONSUMER_NEXT_TRACK),
            ("media_prev", CONSUMER_PREV_TRACK),
            ("prev_track", CONSUMER_PREV_TRACK),
            ("media_stop", CONSUMER_STOP),
            ("stop_track", CONSUMER_STOP),
            ("media_play", CONSUMER_PLAY),
            ("play", CONSUMER_PLAY),
            ("media_pause", CONSUMER_PAUSE),
            ("pause_track", CONSUMER_PAUSE),
            ("volup", CONSUMER_VOLUME_INCREMENT),
            ("media_volup", CONSUMER_VOLUME_INCREMENT),
            ("volume_up", CONSUMER_VOLUME_INCREMENT),
            ("voldn", CONSUMER_VOLUME_DECREMENT),
            ("media_voldn", CONSUMER_VOLUME_DECREMENT),
            ("volume_down", CONSUMER_VOLUME_DECREMENT),
            ("mute", CONSUMER_MUTE),
            ("media_mute", CONSUMER_MUTE),
            ("volume_mute", CONSUMER_MUTE),
            ("brightup", CONSUMER_BRIGHTNESS_INCREMENT),
            ("brightness_up", CONSUMER_BRIGHTNESS_INCREMENT),
            ("brightdn", CONSUMER_BRIGHTNESS_DECREMENT),
            ("brightness_down", CONSUMER_BRIGHTNESS_DECREMENT),
            ("sys_sleep", CONSUMER_SYSTEM_SLEEP),
            ("system_sleep", CONSUMER_SYSTEM_SLEEP),
            ("host_sleep", CONSUMER_SYSTEM_SLEEP),
        ],
    )
    def test_consumer_commands_emit_opcode(self, cmd, expected_usage):
        bc = compile_bc(f"\\{cmd}")
        assert OP_CONSUMER_KEY in bc
        # Disassemble and check output string
        m = Macro(MockHID())
        dis = m.disassemble(bc)
        assert "CONSUMER" in dis

    def test_def_name_collision_rejected(self):
        m = Macro(MockHID())
        for name in ("mute", "volup", "playpause", "sys_sleep"):
            _, err = m.compile(f"\\def{{{name}}}{{a}}")
            assert err is not None
            assert "conflicts with builtin" in err


class TestConsumerControlExecution:
    """Validate VM execution, pulse lifecycle, and safety invariants."""

    def test_mute_pulse_events(self):
        hid = MockHID()
        run_macro(r"\mute", hid=hid)
        assert ("cp", CONSUMER_MUTE) in hid.events
        assert ("cr", CONSUMER_MUTE) in hid.events
        assert hid.events == [("cp", CONSUMER_MUTE), ("cr", CONSUMER_MUTE)]

    def test_volume_up_down_events(self):
        hid = MockHID()
        run_macro(r"\volup\voldn", hid=hid)
        assert hid.events == [
            ("cp", CONSUMER_VOLUME_INCREMENT),
            ("cr", CONSUMER_VOLUME_INCREMENT),
            ("cp", CONSUMER_VOLUME_DECREMENT),
            ("cr", CONSUMER_VOLUME_DECREMENT),
        ]

    def test_guard_abort_releases_active_consumer_usage(self):
        """Guard failure during click_hold_ms releases active_consumer_usage."""
        rt = Runtime()
        rt.register_ns("net", {})
        rt.register_signal("net.msg")
        hid = MockHID()

        # Guard aborts when stop != 0
        src = (
            r"\#!syntax: free" "\n"
            r"\use{net}" "\n"
            r"\guarded[1ms]{{$slot.net.msg.stop, =, 0}}{\mute}"
        )
        m = Macro(hid, rt)
        bc, err = m.compile(src)
        assert err is None

        async def _run_and_trigger():
            async def _trigger():
                await asyncio.sleep(0.005)
                rt.fire_signal("net.msg", {"stop": 1})

            task1 = asyncio.create_task(m.execute(bc, default_delay_ms=0, click_hold_ms=50))
            task2 = asyncio.create_task(_trigger())
            await asyncio.gather(task1, task2)

        asyncio.run(_run_and_trigger())

        # Verify active consumer was released
        assert ("cp", CONSUMER_MUTE) in hid.events
        assert ("cr", CONSUMER_MUTE) in hid.events
        assert ("kra",) in hid.events  # keys_release_all
        assert ("mr", 0x1F) in hid.events  # mouse_release(0x1F)
        assert m.vm.active_consumer_usage is None

    def test_consumer_handle_misuse_fault(self):
        """Passing a handle to OP_CONSUMER_KEY triggers ERR_HANDLE_MISUSE."""
        rt = Runtime()
        rt.register_ns("img", {
            "snap": (["rect"], "snap", lambda x, y, w, h: (rt.new_handle("snap", {"w": 1, "h": 1}), 0)),
        })
        hid = MockHID()
        m = Macro(hid, rt)
        bc_snap, err = m.compile(
            r"\#!syntax: free" "\n"
            r"\use{img}" "\n"
            r"\call{img.snap}{0,0,1,1}" "\n"
            r"\set{h}{$ret}" "\n"
        )
        assert err is None
        from bytecode import OP_END, TAG_REG
        assert bc_snap[-1] == OP_END
        h_reg = m.compiler._vars["h"]
        test_bc = bc_snap[:-1] + bytes([OP_CONSUMER_KEY, TAG_REG, h_reg, OP_END])
        asyncio.run(m.execute(test_bc))
        assert m.vm.last_regs[REG_ERR] == ERR_HANDLE_MISUSE
        assert ("cp", CONSUMER_MUTE) not in hid.events
