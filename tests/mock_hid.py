"""Mock HID provider for CPython unit tests. Records every HID event."""

import keymap


class MockHID:
    def __init__(self):
        self.events = []
        self.unmappable = set()  # chars for which char_keycodes returns None

    # --- keyboard ---
    def key_press(self, keycode):
        self.events.append(("kp", keycode))

    def key_release(self, keycode):
        self.events.append(("kr", keycode))

    def keys_release_all(self):
        self.events.append(("kra",))

    def char_keycodes(self, ch):
        """Minimal US layout: letters, digits, space. None = unmappable."""
        if ch in self.unmappable:
            return None
        o = ord(ch)
        if 0x61 <= o <= 0x7A:  # a-z
            return (keymap.letter_keycode(ch),)
        if 0x41 <= o <= 0x5A:  # A-Z (with shift)
            return (keymap.SHIFT, keymap.letter_keycode(ch))
        if ch.isdigit():
            return (keymap.digit_keycode(ch),)
        if ch == " ":
            return (keymap.SPACE,)
        return None

    # --- mouse ---
    def mouse_press(self, btn):
        self.events.append(("mp", btn))

    def mouse_release(self, btn):
        self.events.append(("mr", btn))

    def mouse_move(self, dx, dy):
        self.events.append(("mm", dx, dy))

    # --- helpers for assertions ---
    def clear(self):
        self.events.clear()
