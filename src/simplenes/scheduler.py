"""Master scheduler for CPU/PPU/APU timing + OAM DMA.

For each CPU cycle, advance PPU by 3 dots and APU by 1 CPU cycle.
After each instruction, process pending OAM DMA.
"""

from simplenes.timing import NTSC_TIMING

_DMA_CYCLES = 513


class Scheduler:
    __slots__ = ("_cpu", "_ppu", "_apu", "_timing", "_oam_dma", "_cpu_bus")

    def __init__(self, cpu, ppu, apu, timing=None, oam_dma_state=None, cpu_bus=None):
        self._cpu = cpu
        self._ppu = ppu
        self._apu = apu
        self._timing = timing if timing is not None else NTSC_TIMING
        self._oam_dma = oam_dma_state
        self._cpu_bus = cpu_bus

    def step_instruction(self) -> int:
        """Execute one complete CPU instruction.
        Returns CPU cycles consumed (including DMA stall cycles)."""
        cycles = self._cpu.step_instruction()
        ppu = self._ppu
        apu = self._apu
        for _ in range(cycles):
            ppu.advance_dots(3)
            apu.clock_cpu_cycle()

        dma_cycles = 0
        if self._oam_dma is not None and self._oam_dma.active:
            dma_cycles = self._execute_dma()

        return cycles + dma_cycles

    def run_frame(self) -> None:
        """Execute until the current PPU frame completes."""
        current = self._ppu.frame
        while self._ppu.frame == current:
            self.step_instruction()

    # ------------------------------------------------------------------
    # OAM DMA (atomic)
    # ------------------------------------------------------------------

    def _execute_dma(self) -> int:
        """Execute full OAM DMA atomically. Returns DMA cycle count (513)."""
        dma = self._oam_dma
        dma.active = False
        page = dma.page

        # Dummy read
        self._cpu_bus.read(page << 8)

        # 256 reads + writes → PPU OAMDATA
        for addr in range(256):
            data = self._cpu_bus.read((page << 8) | addr)
            self._ppu.write_register(0x2004, data)

        # Tick PPU/APU for DMA duration
        for _ in range(_DMA_CYCLES):
            self._ppu.advance_dots(self._timing.ppu_dots_per_cpu_cycle)
            self._apu.clock_cpu_cycle()

        return _DMA_CYCLES
