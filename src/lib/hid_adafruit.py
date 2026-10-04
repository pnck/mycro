# HID provider backed by adafruit_hid (device runtime: CircuitPython)

import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.mouse import Mouse


class AdafruitHIDProvider:
    """Implements the HID provider interface used by lib/macro.py."""

    def __init__(self):
        self.keyboard = Keyboard(usb_hid.devices)
        self.layout = KeyboardLayoutUS(self.keyboard)
        self.mouse = Mouse(usb_hid.devices)

    def key_press(self, keycode):
        self.keyboard.press(keycode)

    def key_release(self, keycode):
        self.keyboard.release(keycode)

    def keys_release_all(self):
        self.keyboard.release_all()

    def char_keycodes(self, ch):
        """Tuple of keycodes (incl. shift) for ch, or None if unmappable."""
        try:
            return self.layout.keycodes(ch)
        except Exception:
            return None

    def mouse_press(self, btn):
        self.mouse.press(btn)

    def mouse_release(self, btn):
        self.mouse.release(btn)

    def mouse_move(self, dx, dy):
        # adafruit_hid chunks >±127 into multiple HID reports internally
        self.mouse.move(x=dx, y=dy)
