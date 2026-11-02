# HID provider backed by adafruit_hid (device runtime: CircuitPython)

import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keyboard_layout_us import KeyboardLayoutUS
from adafruit_hid.mouse import Mouse
try:
    from adafruit_hid.consumer_control import ConsumerControl
    _has_consumer = True
except ImportError:
    _has_consumer = False


class AdafruitHIDProvider:
    """Implements the HID provider interface used by lib/macro.py."""

    def __init__(self):
        self.keyboard = Keyboard(usb_hid.devices)
        self.layout = KeyboardLayoutUS(self.keyboard)
        self.mouse = Mouse(usb_hid.devices)
        if _has_consumer:
            self.consumer = ConsumerControl(usb_hid.devices)
        else:
            self.consumer = None

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

    def consumer_press(self, usage_id):
        if self.consumer:
            self.consumer.press(usage_id)

    def consumer_release(self, usage_id=None):
        if self.consumer:
            self.consumer.release()
