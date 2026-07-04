"""Standard NES controller with serial shift register.

Button bits:
    bit 0: A
    bit 1: B
    bit 2: Select
    bit 3: Start
    bit 4: Up
    bit 5: Down
    bit 6: Left
    bit 7: Right
"""


class Controller:
    __slots__ = ("_buttons", "_shift_register", "_strobe")

    def __init__(self) -> None:
        self._buttons = 0
        self._shift_register = 0
        self._strobe = False

    def set_buttons(self, buttons: int) -> None:
        """Set all 8 buttons at once."""
        self._buttons = buttons & 0xFF

    def write_strobe(self, value: int) -> None:
        """Write to $4016/$4017 strobe bit."""
        new_strobe = bool(value & 1)
        if new_strobe:
            self._shift_register = self._buttons
        self._strobe = new_strobe

    def read(self) -> int:
        """Read controller state (bit 0 = current button)."""
        if self._strobe:
            return self._buttons & 1
        value = self._shift_register & 1
        self._shift_register = (self._shift_register >> 1) | 0x80
        return value
