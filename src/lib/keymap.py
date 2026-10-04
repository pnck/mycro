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

CONTROL = 0xE0
SHIFT = 0xE1
ALT = 0xE2
GUI = 0xE3

# Mouse button bit masks (default CP mouse descriptor declares 5 buttons)
BTN_LEFT = 1
BTN_RIGHT = 2
BTN_MIDDLE = 4
BTN_BACK = 8
BTN_FORWARD = 16
BTN_ALL = BTN_LEFT | BTN_RIGHT | BTN_MIDDLE | BTN_BACK | BTN_FORWARD
