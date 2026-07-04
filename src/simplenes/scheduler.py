"""Master scheduler for CPU/PPU/APU timing.

For each CPU cycle, advance PPU by 3 dots and APU by 1 CPU cycle.
"""

from simplenes.timing import NTSC_TIMING


class Scheduler:
    __slots__ = ("_cpu", "_ppu", "_apu", "_timing")

    def __init__(self, cpu, ppu, apu, timing=None):
        self._cpu = cpu
        self._ppu = ppu
        self._apu = apu
        self._timing = timing if timing is not None else NTSC_TIMING

    def step_instruction(self) -> int:
        """Execute one complete CPU instruction. Returns CPU cycles consumed."""
        cycles = self._cpu.step_instruction()
        for _ in range(cycles):
            for _ in range(self._timing.ppu_dots_per_cpu_cycle):
                self._ppu.clock()
            self._apu.clock_cpu_cycle()
        return cycles

    def run_frame(self) -> None:
        """Execute until the current PPU frame completes."""
        current = self._ppu.frame
        while self._ppu.frame == current:
            self.step_instruction()
