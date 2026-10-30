# Bytecode and Instruction Set Architecture definitions for MYCRO stack VM.
# Normative reference: spec/vm.md (container format, opcodes, operand encoding)

import keymap
from keymap import (
    ENTER, ESCAPE, BACKSPACE, TAB, SPACE, MINUS, EQUALS, DELETE,
    RIGHT_ARROW, LEFT_ARROW, DOWN_ARROW, UP_ARROW,
    PAGE_UP, PAGE_DOWN, HOME, END, INSERT, CAPS_LOCK,
    PRINT_SCREEN, SCROLL_LOCK, PAUSE, APPLICATION,
    F1, F2, F3, F4, F5, F6, F7, F8, F9, F10, F11, F12,
    F13, F14, F15, F16, F17, F18, F19, F20, F21, F22, F23, F24,
    CONTROL, SHIFT, ALT, GUI, RCONTROL, RSHIFT, RALT, RGUI,
    NUMLOCK, KP_SLASH, KP_STAR, KP_MINUS, KP_PLUS, KP_ENTER,
    KP_1, KP_2, KP_3, KP_4, KP_5, KP_6, KP_7, KP_8, KP_9, KP_0,
    KP_DOT, KP_EQUAL,
    BTN_LEFT, BTN_RIGHT, BTN_MIDDLE, BTN_BACK, BTN_FORWARD, BTN_ALL,
)

# Container magic and version
MAGIC = 0xA6
VERSION = 4

# Opcodes
OP_CHAR = 0x01      # <ascii>
OP_KEY = 0x02       # <keycode>
OP_COMBO = 0x03     # <mod_mask> <keycode> — deprecated NOP in v4
OP_SLEEP = 0x04     # typed(u16) — one-shot sleep (DSL \delay)
OP_MCLICK = 0x05    # typed(u8) btn, typed(u16) count
OP_MMOVE = 0x06     # typed(i16) dx, typed(i16) dy
OP_PACE = 0x07      # typed(u16) — set inter-action delay (DSL \pace)
OP_LOOP = 0x10      # typed(u8) count, u16 len (body bytes incl. LOOP_END)
OP_LOOP_END = 0x11
OP_KEY_DOWN = 0x12  # typed(u8) keycode
OP_KEY_UP = 0x13    # typed(u8) keycode (0 = release all)
OP_MDOWN = 0x14     # typed(u8) btn
OP_MUP = 0x15       # typed(u8) btn
OP_SET = 0x20       # reg, typed(i32)
OP_ADD = 0x21       # reg, typed(i32)
OP_BRA = 0x22       # cc, reg, typed(i32) rhs, u16 off — jump if cond FALSE
OP_JMP = 0x23       # u16 off
OP_SUB = 0x24       # reg, typed(i32)
OP_GUARD_ENTER = 0x25  # u16 abort_off, u8 header, [u16 throttle], pred_descriptor
OP_GUARD_EXIT = 0x26   # pop active guard slot
OP_READSLOT = 0x28  # reg, sig_str, field_str — read signal slot into register
OP_WAIT = 0x29      # sig_str, field_str, typed(i32) timeout_ms
OP_CALL_EXT = 0x2A  # ns_str, fn_str, argc, typed args...
OP_TYPEREG = 0x2B   # reg — render register as decimal keystrokes (DSL \typenum)
OP_END = 0xFF

# Typed-operand tags (physical arg encodings; semantic types never reach bytecode)
TAG_IMM = 0
TAG_REG = 1
TAG_STR = 2  # string table index (ext args only)

# Register space
MAX_REGS = 32         # user registers 0x00-0x1F (spec/vm.md: profile constant, u8 ceiling 256)
RECT_MAX_EDGE = 128   # \rect literal / snapshot edge cap
REG_ERR = 0xFD        # $err
REG_SCRATCH = 0xFD    # reserved (special-register guard boundary)
REG_TIMEOUT = 0xFE    # $timeout
REG_RET = 0xFF        # $ret

# Condition codes and branch helpers
CC_OPS = {"=": 0, "!=": 1, "<": 2, "<=": 3, ">": 4, ">=": 5}
CC_NAMES = ["=", "!=", "<", "<=", ">", ">="]
CONVERSE_CC = {0: 0, 1: 1, 2: 4, 3: 5, 4: 2, 5: 3}
INVERT_CC = {0: 1, 1: 0, 2: 5, 3: 4, 4: 3, 5: 2}

# Limits and configuration
MAX_BYTECODE = 4096
MAX_RUNTIME_NEST = 8
MAX_MACRO_EXPAND = 16
MAX_PRED_NEST = 4
MAX_GUARD_NEST = 4
MAX_COMBO_KEYS = 8
MAX_SLOTS = 256
DELAY_SLICE_QUANTUM = 20
DEFAULT_WAIT_TIMEOUT_MS = 2147483647
DEFAULT_CLICK_HOLD_MS = 10

# Guard operand tags (spec/vm.md §4.1)
GUARD_TAG_IMM = 0
GUARD_TAG_REG = 1
GUARD_TAG_SLOT = 2

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

# Printable lone keys lowering table
_PRINTABLE_LONE_KEYS = {
    "plus": ord("+"),
    "minus": ord("-"),
    "equal": ord("="),
    "space": ord(" "),
}

# Character set helpers (CircuitPython str lacks isalnum/isalpha)
_ALPHA = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ALNUM = _ALPHA + "0123456789"


def _is_alpha(ch):
    return ch in _ALPHA


def _is_alnum(ch):
    return ch in _ALNUM


# Canonical mapping tables for key / button name resolution
KEYS = {
    # Editing
    "enter": ENTER,
    "return": ENTER,
    "esc": ESCAPE,
    "escape": ESCAPE,
    "tab": TAB,
    "space": SPACE,
    "bs": BACKSPACE,
    "backspace": BACKSPACE,
    "del": DELETE,
    "delete": DELETE,
    "minus": MINUS,
    "equal": EQUALS,
    # Navigation
    "up": UP_ARROW,
    "down": DOWN_ARROW,
    "left": LEFT_ARROW,
    "right": RIGHT_ARROW,
    "home": HOME,
    "end": END,
    "pgup": PAGE_UP,
    "pageup": PAGE_UP,
    "pgdn": PAGE_DOWN,
    "pagedown": PAGE_DOWN,
    "ins": INSERT,
    "insert": INSERT,
    # Locks
    "caps": CAPS_LOCK,
    "capslock": CAPS_LOCK,
    "numlock": NUMLOCK,
    "scrlk": SCROLL_LOCK,
    "scrolllock": SCROLL_LOCK,
    # System
    "prtscr": PRINT_SCREEN,
    "printscreen": PRINT_SCREEN,
    "snapshot": PRINT_SCREEN,
    "pause": PAUSE,
    "break": PAUSE,
    "menu": APPLICATION,
    "app": APPLICATION,
    "contextmenu": APPLICATION,
    # Function keys F1-F12
    "f1": F1, "f2": F2, "f3": F3, "f4": F4, "f5": F5, "f6": F6,
    "f7": F7, "f8": F8, "f9": F9, "f10": F10, "f11": F11, "f12": F12,
    # Extended function keys F13-F24
    "f13": F13, "f14": F14, "f15": F15, "f16": F16, "f17": F17, "f18": F18,
    "f19": F19, "f20": F20, "f21": F21, "f22": F22, "f23": F23, "f24": F24,
    # Keypad
    "kp_0": KP_0, "kp_1": KP_1, "kp_2": KP_2, "kp_3": KP_3, "kp_4": KP_4,
    "kp_5": KP_5, "kp_6": KP_6, "kp_7": KP_7, "kp_8": KP_8, "kp_9": KP_9,
    "kp_dot": KP_DOT, "kp_period": KP_DOT,
    "kp_enter": KP_ENTER,
    "kp_plus": KP_PLUS, "kp_minus": KP_MINUS,
    "kp_multiply": KP_STAR, "kp_star": KP_STAR,
    "kp_divide": KP_SLASH, "kp_slash": KP_SLASH,
    "kp_equal": KP_EQUAL,
    # Modifiers
    "ctrl": CONTROL, "lctrl": CONTROL, "control": CONTROL, "lcontrol": CONTROL,
    "rctrl": RCONTROL, "rcontrol": RCONTROL,
    "shift": SHIFT, "lshift": SHIFT,
    "rshift": RSHIFT,
    "alt": ALT, "lalt": ALT, "opt": ALT, "option": ALT, "lopt": ALT, "loption": ALT,
    "ralt": RALT, "ropt": RALT, "roption": RALT, "altgr": RALT,
    "gui": GUI, "lgui": GUI, "win": GUI, "lwin": GUI, "cmd": GUI, "command": GUI, "lcmd": GUI, "lcommand": GUI,
    "rgui": RGUI, "rwin": RGUI, "rcmd": RGUI, "rcommand": RGUI,
}

MODS = {
    "ctrl": 0x01,
    "control": 0x01,
    "shift": 0x02,
    "alt": 0x04,
    "option": 0x04,
    "opt": 0x04,
    "win": 0x08,
    "gui": 0x08,
    "cmd": 0x08,
}

MOD_KEYS = [CONTROL, SHIFT, ALT, GUI]

MOUSE_BTNS = {
    "l": BTN_LEFT,
    "left": BTN_LEFT,
    "r": BTN_RIGHT,
    "right": BTN_RIGHT,
    "m": BTN_MIDDLE,
    "middle": BTN_MIDDLE,
    "b": BTN_BACK,
    "back": BTN_BACK,
    "x1": BTN_BACK,
    "f": BTN_FORWARD,
    "forward": BTN_FORWARD,
    "fwd": BTN_FORWARD,
    "x2": BTN_FORWARD,
}

BTN_NAMES = {1: "L", 2: "R", 4: "M", 8: "B", 16: "F"}


def keycode_name(code):
    """Reverse lookup keycode name."""
    for name in sorted(KEYS):
        if KEYS[name] == code:
            return name.upper()
    if 0x04 <= code <= 0x1D:  # A-Z
        return chr(ord("A") + code - 0x04)
    if 0x1E <= code <= 0x26:  # 1-9
        return chr(ord("1") + code - 0x1E)
    if code == 0x27:
        return "0"
    return f"0x{code:02X}"


def mod_names(mask):
    """Convert modifier mask to names."""
    mods = []
    if mask & 0x01:
        mods.append("CTRL")
    if mask & 0x02:
        mods.append("SHIFT")
    if mask & 0x04:
        mods.append("ALT")
    if mask & 0x08:
        mods.append("GUI")
    return "+".join(mods) if mods else "NONE"


def read_header(bc):
    """Validate header and return (strings, code_start_offset)."""
    if len(bc) < 3 or bc[0] != MAGIC or bc[1] != VERSION:
        raise ValueError(
            f"Bytecode version mismatch (want magic=0x{MAGIC:02X} v{VERSION})"
        )
    n = bc[2]
    i = 3
    strs = []
    for _ in range(n):
        ln = bc[i]
        i += 1
        strs.append(bytes(bc[i : i + ln]).decode("utf-8"))
        i += ln
    return strs, i


class MacroError(Exception):
    def __init__(self, msg, pos):
        self.msg = msg
        self.pos = pos
        super().__init__(f"@{pos}: {msg}")

