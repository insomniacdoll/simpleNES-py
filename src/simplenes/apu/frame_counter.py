"""APU Frame Counter — 4-step / 5-step sequencer with IRQ support."""


class FrameCounter:
    _STEPS_4 = (7457, 14913, 22371, 29829)
    _STEPS_5 = (7457, 14913, 22371, 37281)
    _WRAP_4 = 29830
    _WRAP_5 = 37282

    __slots__ = ("_cycle", "_mode_5step", "_irq_inhibit",
                 "quarter_frame", "half_frame", "irq")

    def __init__(self) -> None:
        self._cycle = 0
        self._mode_5step = False   # default: 4-step
        self._irq_inhibit = False
        self.quarter_frame = False
        self.half_frame = False
        self.irq = False

    def tick(self) -> None:
        self.quarter_frame = False
        self.half_frame = False
        self.irq = False
        self._cycle += 1

        steps = self._STEPS_5 if self._mode_5step else self._STEPS_4
        wrap = self._WRAP_5 if self._mode_5step else self._WRAP_4

        if self._cycle in steps:
            self.quarter_frame = True
        if self._cycle in (steps[1], steps[3]):
            self.half_frame = True

        if not self._mode_5step and not self._irq_inhibit:
            if self._cycle == steps[3]:
                self.irq = True

        if self._cycle >= wrap:
            self._cycle = 0

    def write(self, value: int) -> tuple[bool, bool]:
        """Process $4017 write.  Returns (quarter, half) immediate flags."""
        self._mode_5step = bool(value & 0x80)
        self._irq_inhibit = bool(value & 0x40)
        if self._irq_inhibit:
            self.irq = False

        self._cycle = 0
        self.quarter_frame = False
        self.half_frame = False

        if self._mode_5step:
            return True, True        # immediate quarter + half
        return False, False
