"""APU Triangle channel."""

from simplenes.apu.length_counter import LengthCounter

# 32-step waveform: 0→15→0, then 15→0→15 (alternating direction)
_TRI_WAVE = [
    15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0,
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
]


class TriangleChannel:
    __slots__ = (
        "_length",
        "_control_flag", "_linear_reload_value",
        "_linear_reload_flag", "_linear_counter",
        "_timer", "_timer_reload",
        "_seq",
    )

    def __init__(self) -> None:
        self._length = LengthCounter()
        self._control_flag = False
        self._linear_reload_value = 0
        self._linear_reload_flag = False
        self._linear_counter = 0
        self._timer = 0
        self._timer_reload = 0
        self._seq = 0

    # ----------------------------------------------------------------
    # Public
    # ----------------------------------------------------------------

    @property
    def output(self) -> int:
        if self._length.value == 0 or self._linear_counter == 0:
            return 0
        return _TRI_WAVE[self._seq]

    @property
    def length_active(self) -> bool:
        return self._length.value > 0

    def set_enabled(self, enabled: bool) -> None:
        self._length.set_enabled(enabled)

    def write(self, reg: int, value: int) -> None:
        if reg == 0:      # $4008 — linear control
            self._control_flag = bool(value & 0x80)
            self._linear_reload_value = value & 0x7F
            self._length.set_halt(self._control_flag)
        elif reg == 2:    # $400A — timer low
            self._timer_reload = (self._timer_reload & 0x700) | value
        elif reg == 3:    # $400B — length + timer high
            self._length.write(value >> 3)
            self._timer_reload = (self._timer_reload & 0xFF) | ((value & 7) << 8)
            self._linear_reload_flag = True

    def tick_linear_counter(self) -> None:
        """Quarter-frame: clock linear counter."""
        if self._linear_reload_flag:
            self._linear_counter = self._linear_reload_value
        elif self._linear_counter > 0:
            self._linear_counter -= 1
        if not self._control_flag:
            self._linear_reload_flag = False

    def tick_length(self) -> None:
        """Half-frame: clock length counter."""
        self._length.tick()

    def tick_timer(self) -> None:
        """Every CPU cycle: clock waveform timer."""
        if self._timer > 0:
            self._timer -= 1
            return
        self._timer = self._timer_reload
        if self._length.value > 0 and self._linear_counter > 0:
            self._seq = (self._seq + 1) & 31

    def reset(self) -> None:
        self._length = LengthCounter()
        self._control_flag = False
        self._linear_reload_value = 0
        self._linear_reload_flag = False
        self._linear_counter = 0
        self._timer = 0
        self._timer_reload = 0
        self._seq = 0
