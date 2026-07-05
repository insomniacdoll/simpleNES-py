"""APU envelope generator — shared by Pulse and Noise channels."""


class Envelope:
    __slots__ = ("_divider", "_decay", "_volume", "_loop", "_constant", "_start")

    def __init__(self) -> None:
        self._divider = 0
        self._decay = 0       # 4-bit counter
        self._volume = 0      # period / constant volume
        self._loop = False
        self._constant = False
        self._start = False

    def write_control(self, value: int) -> None:
        """$4000/$4004/$400C write — does NOT set start flag."""
        self._loop = bool(value & 0x20)
        self._constant = bool(value & 0x10)
        self._volume = value & 0x0F

    def restart(self) -> None:
        """$4003/$4007/$400F write — sets start flag."""
        self._start = True

    def tick(self) -> None:
        if self._start:
            self._start = False
            self._decay = 15
            self._divider = self._volume
            return
        if self._divider > 0:
            self._divider -= 1
        else:
            self._divider = self._volume
            if self._decay > 0:
                self._decay -= 1
            elif self._loop:
                self._decay = 15

    def output(self) -> int:
        return self._volume if self._constant else self._decay
