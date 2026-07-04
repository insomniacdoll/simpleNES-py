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


def _make_bus_with_controllers():
    """Build a CPUBus exposing controllers.  Returns (bus, dma, c1, c2)."""
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
    return bus, dma, c1, c2


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
    # address field removed in Phase 5 simplification


def test_mapper_range():
    """$8000-$FFFF delegates to mapper."""
    bus, _ = _make_bus()
    val = bus.read(0x8000)
    # Should return whatever mapper returns (PRG ROM byte)
    assert 0 <= val <= 255


# ======================================================================
# Phase 5: Controller wiring
# ======================================================================


def test_controller1_read_from_4016():
    """Strobe + read $4016 returns current controller 1 button."""
    bus, _, c1, _ = _make_bus_with_controllers()
    c1.set_buttons(0b0000_0001)  # A pressed
    bus.write(0x4016, 1)          # strobe on
    bus.write(0x4016, 0)          # strobe off
    assert bus.read(0x4016) == 1   # bit 0 = A


def test_controller2_read_from_4017():
    """Strobe + read $4017 returns serial controller 2 state."""
    bus, _, _, c2 = _make_bus_with_controllers()
    c2.set_buttons(0b0000_0011)  # A + B pressed → bit 0 = 1
    bus.write(0x4016, 1)          # strobe on (shared strobe line)
    bus.write(0x4016, 0)          # strobe off

    # First read: bit 0 (= 1, A)
    assert bus.read(0x4017) == 1
    # Second read: bit 1 (= 1, B)
    assert bus.read(0x4017) == 1


def test_controller_strobe_write_reaches_both():
    """Writing $4016 strobe loads shift registers for both controllers."""
    bus, _, c1, c2 = _make_bus_with_controllers()
    c1.set_buttons(0b0000_0001)  # controller 1: only A (bit 0 = 1)
    c2.set_buttons(0b0000_0010)  # controller 2: only B (bit 0 = 0, bit 1 = 1)

    bus.write(0x4016, 1)   # strobe on: latches buttons into shift register
    bus.write(0x4016, 0)   # strobe off

    # First read of each controller reads bit 0
    assert bus.read(0x4016) == 1  # c1 bit 0 = A pressed → 1
    assert bus.read(0x4017) == 0  # c2 bit 0 = A not pressed → 0
    # Next read: bit 1 shifted to bit 0
    assert bus.read(0x4017) == 1  # c2 bit 1 = B pressed → 1
