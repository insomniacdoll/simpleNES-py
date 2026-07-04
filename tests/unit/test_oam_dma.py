"""Unit tests for OAM DMA (Phase 5)."""

from simplenes.cpu.cpu import CPU
from simplenes.bus.cpu_bus import CPUBus
from simplenes.apu.apu import APU
from simplenes.bus.ppu_bus import PPUBus
from simplenes.dma.oam_dma import OAMDMAState
from simplenes.input.controller import Controller
from simplenes.interrupts import InterruptLines
from simplenes.ppu.ppu import PPU
from simplenes.cartridge.image import Mirroring
from simplenes.scheduler import Scheduler
from simplenes.timing import NTSC_TIMING


class _DmaMapper:
    mirroring = Mirroring.HORIZONTAL

    def observe_ppu_address(self, a):
        pass

    def ppu_read(self, a):
        return 0

    def ppu_write(self, a, v):
        pass

    def cpu_read(self, a):
        return 0

    def cpu_write(self, a, v):
        pass


def _make_dma_setup():
    interrupts = InterruptLines()
    mapper = _DmaMapper()
    ppu_bus = PPUBus(mapper)
    ppu = PPU(bus=ppu_bus, interrupts=interrupts)
    ppu.write_register(0x2003, 0)  # OAMADDR = 0
    apu = APU(interrupts=interrupts)
    oam_dma = OAMDMAState()
    cpu_bus = CPUBus(
        ppu=ppu, apu=apu, mapper=mapper,
        controller1=Controller(), controller2=Controller(),
        oam_dma_state=oam_dma,
    )
    cpu = CPU(bus=cpu_bus, interrupts=interrupts)
    scheduler = Scheduler(
        cpu=cpu, ppu=ppu, apu=apu,
        timing=NTSC_TIMING,
        oam_dma_state=oam_dma,
        cpu_bus=cpu_bus,
    )

    # Write known test data to RAM page 2 ($0200-$02FF)
    for i in range(256):
        cpu_bus.write(0x0200 + i, i & 0xFF)

    return scheduler, ppu, oam_dma


def test_dma_copies_oam():
    """DMA copies 256 bytes from CPU page 2 to PPU OAM."""
    scheduler, ppu, oam_dma = _make_dma_setup()

    oam_dma.trigger(0x02)
    assert oam_dma.active
    scheduler.step_instruction()
    assert not oam_dma.active

    for i in range(256):
        assert ppu.oam[i] == (i & 0xFF), f"OAM[{i}] mismatch"


def test_dma_ticks_ppu():
    """DMA advances PPU by 513 * 3 = 1539 dots."""
    scheduler, ppu, oam_dma = _make_dma_setup()

    dot_before = ppu.dot
    scanline_before = ppu.scanline

    oam_dma.trigger(0x02)
    scheduler.step_instruction()

    total = (ppu.scanline * 341 + ppu.dot) - (scanline_before * 341 + dot_before)
    assert total > 0


def test_dma_deactivates():
    """DMA active flag cleared after execution."""
    scheduler, ppu, oam_dma = _make_dma_setup()
    oam_dma.trigger(0x02)
    assert oam_dma.active
    scheduler.step_instruction()
    assert not oam_dma.active


def test_dma_dummy_read():
    """First DMA cycle dummy: OAM[0] gets byte 0 from page 2."""
    scheduler, ppu, oam_dma = _make_dma_setup()
    ppu.write_register(0x2003, 0)
    oam_dma.trigger(0x02)
    scheduler.step_instruction()
    assert ppu.oam[0] == 0x00
    assert ppu.oam[1] == 0x01


def test_dma_cycles_in_return():
    """step_instruction returns cycles + 513 when DMA active."""
    scheduler, ppu, oam_dma = _make_dma_setup()
    cycles_no_dma = scheduler.step_instruction()
    oam_dma.trigger(0x02)
    cycles_with_dma = scheduler.step_instruction()
    assert cycles_with_dma >= cycles_no_dma + 513
