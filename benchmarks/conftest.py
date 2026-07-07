"""Shared fixtures for benchmarks."""

import pytest

from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.interrupts import InterruptLines
from simplenes.ppu import PPU


def _make_nrom_prg() -> bytes:
    """Build a 32 KiB PRG ROM with a NOP-loop at the RESET vector.

    Program at $8000 (PRG offset 0):
        NOP        ; EA
        JMP $8000  ; 4C 00 80
    """
    prg = bytearray([0xEA] * 32768)

    # $8000: NOP
    prg[0x0000] = 0xEA
    # $8001-$8003: JMP $8000
    prg[0x0001] = 0x4C
    prg[0x0002] = 0x00
    prg[0x0003] = 0x80

    # Vectors at CPU $FFFA-$FFFF → PRG offsets $7FFA-$7FFF
    prg[0x7FFA] = 0x00  # NMI (unused, but valid)
    prg[0x7FFB] = 0x80
    prg[0x7FFC] = 0x00  # RESET → $8000
    prg[0x7FFD] = 0x80
    prg[0x7FFE] = 0x00  # IRQ/BRK (unused)
    prg[0x7FFF] = 0x80

    return bytes(prg)


@pytest.fixture
def nrom_image():
    """32 KiB PRG (valid NOP loop) + 8 KiB CHR NROM CartridgeImage."""
    return CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=0,
        submapper_id=0,
        prg_rom=_make_nrom_prg(),
        chr_rom=b"\x00" * 8192,
        prg_ram_size=0,
        prg_nvram_size=0,
        chr_ram_size=0,
        chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL,
        has_battery=False,
        has_trainer=False,
    )


@pytest.fixture
def ppu(nrom_image):
    """Standalone PPU + PPUBus + NROMMapper for micro-benchmarks."""
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
    mapper = NROMMapper(nrom_image)
    bus = PPUBus(mapper)
    interrupts = InterruptLines()
    return PPU(bus=bus, interrupts=interrupts)


@pytest.fixture
def nes_machine(nrom_image):
    """Full NESMachine for end-to-end benchmarks."""
    from simplenes.machine import NESMachine
    return NESMachine(nrom_image)
