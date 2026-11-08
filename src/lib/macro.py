# Macro Interpreter for HID simulation — unified facade for compiler + async VM
# Hardware access goes through a HID provider object with this interface:
#   key_press(keycode) / key_release(keycode) / keys_release_all()
#   char_keycodes(ch) -> tuple of keycodes (incl. modifiers) or None
#   mouse_press(btn) / mouse_release(btn) / mouse_move(dx, dy)
# Implementations: lib/hid_adafruit.py (device), tests/mock_hid.py (tests).

import keymap
from keymap import (
    BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_BACK, BTN_FORWARD, BTN_ALL,
)
from bytecode import (
    MAGIC, VERSION,
    OP_CHAR, OP_KEY, OP_COMBO, OP_SLEEP, OP_MCLICK, OP_MMOVE, OP_PACE,
    OP_LOOP, OP_LOOP_END, OP_KEY_DOWN, OP_KEY_UP, OP_MDOWN, OP_MUP,
    OP_SET, OP_ADD, OP_BRA, OP_JMP, OP_SUB, OP_GUARD_ENTER, OP_GUARD_EXIT,
    OP_READSLOT, OP_WAIT,
    OP_CALL_EXT, OP_TYPEREG, OP_END,
    TAG_IMM, TAG_REG, TAG_STR,
    MAX_REGS, RECT_MAX_EDGE, REG_ERR, REG_SCRATCH, REG_TIMEOUT, REG_RET,
    CC_OPS, CC_NAMES, CONVERSE_CC, INVERT_CC,
    MAX_CODE_BYTES, MAX_BYTECODE, MAX_STRINGS, MAX_STRING_BYTES,
    MAX_RUNTIME_NEST, MAX_MACRO_EXPAND, MAX_PRED_NEST, MAX_GUARD_NEST,
    MAX_COMBO_KEYS, MAX_SLOTS, DELAY_SLICE_QUANTUM, DEFAULT_WAIT_TIMEOUT_MS, DEFAULT_CLICK_HOLD_MS, DEFAULT_PACE_MS, _PRINTABLE_LONE_KEYS,
    GUARD_TAG_IMM, GUARD_TAG_REG, GUARD_TAG_SLOT,
    ERR_OK, ERR_GENERIC, ERR_INVALID_ARG, ERR_NO_PROVIDER, ERR_OUT_OF_BOUNDS,
    ERR_HANDLE_INVALID, ERR_RESOURCE_EXHAUSTED, ERR_TIMEOUT, ERR_HANDLE_MISUSE,
    _is_alpha, _is_alnum,
    KEYS, MODS, MOD_KEYS, MOUSE_BTNS, BTN_NAMES,
    keycode_name, mod_names, read_header,
    MacroError,
)
from compiler import Compiler
from vm import VM


class Macro:
    """Unified Facade aggregating Compiler and VM execution engine."""

    KEYS = KEYS
    MODS = MODS
    MOD_KEYS = MOD_KEYS
    MOUSE_BTNS = MOUSE_BTNS
    _BTN_NAMES = BTN_NAMES

    BTN_LEFT = BTN_LEFT
    BTN_RIGHT = BTN_RIGHT
    BTN_MIDDLE = BTN_MIDDLE
    BTN_BACK = BTN_BACK
    BTN_FORWARD = BTN_FORWARD
    BTN_ALL = BTN_ALL

    _read_header = staticmethod(read_header)

    def __init__(self, hid=None, runtime=None, click_hold_ms=DEFAULT_CLICK_HOLD_MS):
        self.hid = hid
        self.runtime = runtime
        self.click_hold_ms = click_hold_ms
        self.compiler = Compiler(runtime=runtime, click_hold_ms=click_hold_ms)
        self.vm = VM(hid=hid, runtime=runtime)

    def compile(self, text, config=None, click_hold_ms=None):
        """Compile macro text to bytecode. Returns (bytecode, error_msg)."""
        return self.compiler.compile(text, config=config, click_hold_ms=click_hold_ms)

    async def execute(self, bytecode, default_delay_ms=DEFAULT_PACE_MS, click_hold_ms=10):
        """Execute compiled bytecode asynchronously on the VM."""
        return await self.vm.execute(
            bytecode, default_delay_ms=default_delay_ms, click_hold_ms=click_hold_ms
        )

    def disassemble(self, bytecode):
        """Disassemble bytecode to human-readable format."""
        return self.vm.disassemble(bytecode)

    @property
    def last_regs(self):
        """Latest VM register state after execution."""
        return self.vm.last_regs
