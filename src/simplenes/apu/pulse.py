"""APU Pulse channel (square wave 1 / 2)."""

from simplenes.apu.envelope import Envelope
from simplenes.apu.length_counter import LengthCounter

DUTY_TABLE = [0b01000000, 0b01100000, 0b01111000, 0b10011111]


class PulseChannel:
    __slots__ = (
        "_envelope", "_length",
        "_duty", "_seq",
        "_timer", "_timer_reload",
        "_sweep_enabled", "_sweep_period", "_sweep_negate",
        "_sweep_shift", "_sweep_divider", "_sweep_reload",
        "_silenced",
    )

    def __init__(self) -> None:
        self._envelope = Envelope()
        self._length = LengthCounter()
        self._duty = 0
        self._seq = 0
        self._timer = 0
        self._timer_reload = 0
        self._sweep_enabled = False
        self._sweep_period = 0
        self._sweep_negate = False
        self._sweep_shift = 0
        self._sweep_divider = 0
        self._sweep_reload = False
        self._silenced = False

    # ----------------------------------------------------------------
    # Public (called by APU)
    # ----------------------------------------------------------------

    @property
    def output(self) -> int:
        if self._length.value == 0 or self._silenced:
            return 0
        if (DUTY_TABLE[self._duty] >> self._seq) & 1:
            return self._envelope.output()
        return 0

    @property
    def length_active(self) -> bool:
        return self._length.value > 0

    def set_enabled(self, enabled: bool) -> None:
        self._length.set_enabled(enabled)

    def write(self, reg: int, value: int) -> None:
        if reg == 0:      # $4000/$4004 — duty + envelope + halt
            self._duty = (value >> 6) & 3
            self._length.set_halt(bool(value & 0x20))
            self._envelope.write_control(value)
        elif reg == 1:    # $4001/$4005 — sweep
            self._sweep_enabled = bool(value & 0x80)
            self._sweep_period = (value >> 4) & 7
            self._sweep_negate = bool(value & 0x08)
            self._sweep_shift = value & 7
            self._sweep_reload = True
            self._recalc_silenced()
        elif reg == 2:    # $4002/$4006 — timer low
            self._timer_reload = (self._timer_reload & 0x700) | value
            self._recalc_silenced()
        elif reg == 3:    # $4003/$4007 — length + timer high
            self._length.write(value >> 3)
            self._timer_reload = (self._timer_reload & 0xFF) | ((value & 7) << 8)
            self._envelope.restart()
            self._seq = 0
            self._recalc_silenced()

    def tick_envelope(self) -> None:
        """Quarter-frame: clock envelope."""
        self._envelope.tick()

    def tick_length_sweep(self) -> None:
        """Half-frame: clock length counter + sweep."""
        self._length.tick()
        self._tick_sweep()

    def tick_timer(self) -> None:
        """Every CPU cycle: clock waveform timer."""
        if self._timer > 0:
            self._timer -= 1
            return
        self._timer = self._timer_reload
        self._seq = (self._seq + 1) & 7

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------

    def _sweep_target_period(self) -> int:
        """Compute the next timer period that the sweep unit would target."""
        delta = self._timer_reload >> self._sweep_shift
        if self._sweep_negate:
            return self._timer_reload - delta - 1
        return self._timer_reload + delta

    def _tick_sweep(self) -> None:
        if not self._sweep_enabled:
            return
        if self._sweep_divider > 0:
            self._sweep_divider -= 1
        else:
            self._sweep_divider = self._sweep_period
            if self._sweep_shift > 0 and self._timer_reload >= 8:
                target = self._sweep_target_period()
                if 0 <= target < 0x800:
                    self._timer_reload = target
        if self._sweep_reload:
            self._sweep_reload = False
            self._sweep_divider = self._sweep_period
        self._recalc_silenced()

    def _recalc_silenced(self) -> None:
        """Re-evaluate whether channel should be silenced based on current state."""
        if self._timer_reload < 8:
            self._silenced = True
            return
        if self._sweep_enabled and self._sweep_shift:
            target = self._sweep_target_period()
            self._silenced = target < 0 or target >= 0x800
        else:
            self._silenced = False

    def reset(self) -> None:
        self._envelope = Envelope()
        self._length = LengthCounter()
        self._duty = 0
        self._seq = 0
        self._timer = 0
        self._timer_reload = 0
        self._sweep_enabled = False
        self._sweep_period = 0
        self._sweep_negate = False
        self._sweep_shift = 0
        self._sweep_divider = 0
        self._sweep_reload = False
        self._silenced = False
