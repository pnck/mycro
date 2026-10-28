"""USB HID keyboard usage IDs (HID Usage Tables, page 0x07).

Runtime-agnostic constants — same values as adafruit_hid.keycode.Keycode,
defined here so the macro compiler/VM has zero dependency on adafruit_hid.
"""

# Letters A-Z: 0x04-0x1D
def letter_keycode(ch):
    """'a'-'z' / 'A'-'Z' -> usage ID 0x04-0x1D."""
    return 0x04 + (ord(ch.lower()) - ord("a"))


def digit_keycode(ch):
    """'1'-'9' -> 0x1E-0x26, '0' -> 0x27."""
    return 0x27 if ch == "0" else 0x1E + (ord(ch) - ord("1"))


ENTER = 0x28
ESCAPE = 0x29
BACKSPACE = 0x2A
TAB = 0x2B
SPACE = 0x2C
MINUS = 0x2D
EQUALS = 0x2E

CAPS_LOCK = 0x39

F1 = 0x3A
F2 = 0x3B
F3 = 0x3C
F4 = 0x3D
F5 = 0x3E
F6 = 0x3F
F7 = 0x40
F8 = 0x41
F9 = 0x42
F10 = 0x43
F11 = 0x44
F12 = 0x45

PRINT_SCREEN = 0x46
SCROLL_LOCK = 0x47
PAUSE = 0x48

INSERT = 0x49
HOME = 0x4A
PAGE_UP = 0x4B
DELETE = 0x4C
END = 0x4D
PAGE_DOWN = 0x4E
RIGHT_ARROW = 0x4F
LEFT_ARROW = 0x50
DOWN_ARROW = 0x51
UP_ARROW = 0x52

APPLICATION = 0x65

F13 = 0x68
F14 = 0x69
F15 = 0x6A
F16 = 0x6B
F17 = 0x6C
F18 = 0x6D
F19 = 0x6E
F20 = 0x6F
F21 = 0x70
F22 = 0x71
F23 = 0x72
F24 = 0x73

CONTROL = 0xE0
SHIFT = 0xE1
ALT = 0xE2
GUI = 0xE3
RCONTROL = 0xE4
RSHIFT = 0xE5
RALT = 0xE6
RGUI = 0xE7

NUMLOCK = 0x53
KP_SLASH = 0x54
KP_STAR = 0x55
KP_MINUS = 0x56
KP_PLUS = 0x57
KP_ENTER = 0x58
KP_1 = 0x59
KP_2 = 0x5A
KP_3 = 0x5B
KP_4 = 0x5C
KP_5 = 0x5D
KP_6 = 0x5E
KP_7 = 0x5F
KP_8 = 0x60
KP_9 = 0x61
KP_0 = 0x62
KP_DOT = 0x63
KP_EQUAL = 0x67

# Mouse button bit masks (default CP mouse descriptor declares 5 buttons)
BTN_LEFT = 1
BTN_RIGHT = 2
BTN_MIDDLE = 4
BTN_BACK = 8
BTN_FORWARD = 16
BTN_ALL = BTN_LEFT | BTN_RIGHT | BTN_MIDDLE | BTN_BACK | BTN_FORWARD
