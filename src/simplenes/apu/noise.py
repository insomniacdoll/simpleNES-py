"""APU Noise channel — LFSR-based pseudo-random noise."""

from simplenes.apu.envelope import Envelope
from simplenes.apu.length_counter import LengthCounter

_NOISE_PERIODS = [4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068]


class NoiseChannel:
    __slots__ = (
        "_envelope", "_length",
        "_mode", "_lfsr",
        "_timer", "_timer_period",
    )

    def __init__(self) -> None:
        self._envelope = Envelope()
        self._length = LengthCounter()
        self._mode = False      # False = mode 0 (bit1), True = mode 1 (bit6)
        self._lfsr = 0x4000     # power-on value: bit14 = 1
        self._timer = 0
        self._timer_period = _NOISE_PERIODS[0]

    # ----------------------------------------------------------------
    # Public
    # ----------------------------------------------------------------

    @property
    def output(self) -> int:
        if self._length.value == 0:
            return 0
        if (self._lfsr & 1) == 0:
            return self._envelope.output()
        return 0

    @property
    def length_active(self) -> bool:
        return self._length.value > 0

    def set_enabled(self, enabled: bool) -> None:
        self._length.set_enabled(enabled)

    def write(self, reg: int, value: int) -> None:
        if reg == 0:      # $400C — envelope + halt
            self._length.set_halt(bool(value & 0x20))
            self._envelope.write_control(value)
        elif reg == 2:    # $400E — mode + period
            self._mode = bool(value & 0x80)
            self._timer_period = _NOISE_PERIODS[value & 0x0F]
        elif reg == 3:    # $400F — length
            self._length.write(value >> 3)
            self._envelope.restart()

    def tick_envelope(self) -> None:
        """Quarter-frame: clock envelope."""
        self._envelope.tick()

    def tick_length(self) -> None:
        """Half-frame: clock length counter."""
        self._length.tick()

    def tick_timer(self) -> None:
        """Every CPU cycle: clock LFSR timer."""
        if self._timer > 0:
            self._timer -= 1
            return
        self._timer = self._timer_period

        # Clock LFSR
        feedback = (self._lfsr & 1) ^ ((self._lfsr >> (6 if self._mode else 1)) & 1)
        self._lfsr = (self._lfsr >> 1) | (feedback << 14)

    def reset(self) -> None:
        self._envelope = Envelope()
        self._length = LengthCounter()
        self._mode = False
        self._lfsr = 0x4000
        self._timer = 0
        self._timer_period = _NOISE_PERIODS[0]
