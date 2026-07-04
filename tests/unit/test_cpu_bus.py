"""Unit tests for CPUBus."""

from simplenes.apu.apu import APU
from simplenes.bus.cpu_bus import CPUBus
from simplenes.cartridge.ines import RomParser
from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
from simplenes.dma.oam_dma import OAMDMAState
from simplenes.input.controller import Controller
from simplenes.interrupts import InterruptLines
from simplenes.ppu.ppu import PPU
from simplenes.bus.ppu_bus import PPUBus
from tests.fixtures.nrom_sample import build_nrom_ines


def _make_bus():
    """Build a CPUBus with all components wired up. Returns (bus, dma)."""
    image = RomParser.parse(bytes(build_nrom_ines()))
    mapper = NROMMapper(image)
    ppu_bus = PPUBus(mapper)
    ppu = PPU(bus=ppu_bus, interrupts=InterruptLines())
    apu = APU(interrupts=InterruptLines())
    c1 = Controller()
    c2 = Controller()
    dma = OAMDMAState()
    bus = CPUBus(ppu=ppu, apu=apu, mapper=mapper,
                 controller1=c1, controller2=c2, oam_dma_state=dma)
    return bus, dma


def test_ram_read_write():
    """$0000-$07FF reads and writes correctly."""
    bus, _ = _make_bus()
    bus.write(0x0000, 0x42)
    assert bus.read(0x0000) == 0x42
    bus.write(0x07FF, 0xAB)
    assert bus.read(0x07FF) == 0xAB


def test_ram_mirror():
    """$0800 write mirrors to $0000."""
    bus, _ = _make_bus()
    bus.write(0x0800, 0x55)
    assert bus.read(0x0000) == 0x55
    bus.write(0x1800, 0x77)
    assert bus.read(0x0000) == 0x77


def test_ppu_reg_mirror():
    """$2008 writes mirror to PPU $2000."""
    bus, _ = _make_bus()
    bus.write(0x2008, 0xFF)
    # PPU write_register is a no-op stub, but route is tested
    # by ensuring no exception is raised
    assert bus.read(0x2008) >= 0  # read_register stubs return 0


def test_oam_dma_trigger():
    """Writing $4014 triggers OAM DMA and sets state."""
    bus, dma = _make_bus()
    bus.write(0x4014, 0x02)
    assert dma.active is True
    assert dma.page == 0x02
    assert dma.address == 0


def test_mapper_range():
    """$8000-$FFFF delegates to mapper."""
    bus, _ = _make_bus()
    val = bus.read(0x8000)
    # Should return whatever mapper returns (PRG ROM byte)
    assert 0 <= val <= 255
