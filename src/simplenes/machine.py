"""NES emulation machine — the single composition root.

Creates and connects all components. Frontends interact only
through NESMachine's public API.
"""

from simplenes.apu.apu import APU
from simplenes.bus.cpu_bus import CPUBus
from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import Mirroring
from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
from simplenes.cpu.cpu import CPU
from simplenes.dma.oam_dma import OAMDMAState
from simplenes.errors import InvalidRomError, UnsupportedMapperError
from simplenes.input.controller import Controller
from simplenes.interrupts import InterruptLines
from simplenes.ppu.ppu import PPU
from simplenes.scheduler import Scheduler
from simplenes.timing import NTSC_TIMING, Region


class NESMachine:
    """NES emulation machine — the single composition root."""

    __slots__ = (
        "_interrupts", "_mapper", "_ppu_bus", "_ppu",
        "_apu", "_controller1", "_controller2",
        "_oam_dma", "_cpu_bus", "_cpu", "_scheduler",
    )

    def __init__(self, cartridge, *, region=Region.NTSC):
        if region is not Region.NTSC:
            raise ValueError(
                f"Only NTSC is supported in Phase 1, got {region}"
            )

        if cartridge.mapper_id != 0:
            raise UnsupportedMapperError(cartridge.mapper_id)

        if cartridge.mirroring == Mirroring.FOUR_SCREEN:
            raise InvalidRomError(
                "Four-screen mirroring is not supported in Phase 1"
            )

        self._interrupts = InterruptLines()
        self._mapper = NROMMapper(cartridge)
        self._ppu_bus = PPUBus(self._mapper)
        self._ppu = PPU(bus=self._ppu_bus, interrupts=self._interrupts)
        self._apu = APU(interrupts=self._interrupts)
        self._controller1 = Controller()
        self._controller2 = Controller()
        self._oam_dma = OAMDMAState()
        self._cpu_bus = CPUBus(
            ppu=self._ppu, apu=self._apu, mapper=self._mapper,
            controller1=self._controller1,
            controller2=self._controller2,
            oam_dma_state=self._oam_dma,
        )
        self._cpu = CPU(bus=self._cpu_bus, interrupts=self._interrupts)
        self._scheduler = Scheduler(
            cpu=self._cpu, ppu=self._ppu, apu=self._apu, timing=NTSC_TIMING,
            oam_dma_state=self._oam_dma,
            cpu_bus=self._cpu_bus,
        )

        self.reset()

    def reset(self) -> None:
        """Reset all components to power-on state."""
        self._ppu.reset()
        self._apu.reset()
        self._oam_dma.reset()
        self._cpu.reset()

    def step_instruction(self) -> int:
        """Execute one complete CPU instruction. Returns CPU cycles consumed."""
        return self._scheduler.step_instruction()

    def run_frame(self) -> None:
        """Execute until the current PPU frame completes."""
        self._scheduler.run_frame()

    def set_controller_state(self, port: int, state: int) -> None:
        """Set controller button state. 1-based: 1=controller1, 2=controller2.

        Raises:
            ValueError: if port is not 1 or 2.
        """
        if port == 1:
            self._controller1.set_buttons(state)
        elif port == 2:
            self._controller2.set_buttons(state)
        else:
            raise ValueError(f"Controller port must be 1 or 2, got {port}")

    @property
    def framebuffer(self) -> memoryview:
        """Get the PPU framebuffer as a memoryview (256x240 palette indices)."""
        return memoryview(self._ppu.framebuffer)

    @property
    def audio_sample_rate(self) -> int:
        """Audio output sample rate in Hz (fixed: 44100)."""
        return 44_100

    def read_audio_samples(self, max_count: int = 4096) -> list[float]:
        """Read up to max_count mono float samples [0.0, 1.0] from the APU."""
        return self._apu.read_samples(max_count)
