"""Unit tests for UxROMMapper (Mapper 2)."""

import pytest

from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.cartridge.ines import RomParser
from simplenes.cartridge.mappers.mapper002_uxrom import UxROMMapper
from simplenes.errors import InvalidRomError
from tests.fixtures.nrom_sample import build_nrom_ines


# ---------------------------------------------------------------------------
# Helper — build a CartridgeImage directly for unit tests
# ---------------------------------------------------------------------------

def _uxrom_image(prg_banks=4, prg_ram_size=0, prg_nvram_size=0,
                 chr_rom=b"", mirroring=Mirroring.HORIZONTAL):
    """Build a CartridgeImage suitable for UxROM testing.

    Each 16 KiB PRG bank is filled with its bank index repeated.
    """
    prg_parts = [bytes([bank] * 16384) for bank in range(prg_banks)]
    return CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=2,
        submapper_id=0,
        prg_rom=b"".join(prg_parts),
        chr_rom=chr_rom,
        prg_ram_size=prg_ram_size,
        prg_nvram_size=prg_nvram_size,
        chr_ram_size=8192,
        chr_nvram_size=0,
        mirroring=mirroring,
        has_battery=False,
        has_trainer=False,
    )


# ======================================================================
# Construction & ROM validation (5)
# ======================================================================

def test_uxrom_construction_valid_4banks():
    """64 KiB PRG → 4 banks, bank_mask=3, initial bank=0, CHR-RAM 8 KiB."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    assert mapper._prg_banks == 4
    assert mapper._bank_mask == 3
    assert mapper._prg_bank == 0
    assert len(mapper._chr_ram) == 8192


def test_uxrom_construction_valid_8banks():
    """128 KiB PRG → 8 banks, bank_mask=7."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=8))
    assert mapper._prg_banks == 8
    assert mapper._bank_mask == 7


def test_uxrom_construction_rejects_chr_rom():
    """CHR-ROM UxROM → InvalidRomError."""
    with pytest.raises(InvalidRomError, match="CHR ROM"):
        UxROMMapper(_uxrom_image(prg_banks=4, chr_rom=b"\x00" * 8192))


def test_uxrom_construction_rejects_non_power_of_two_banks():
    """48 KiB PRG (3 banks) → InvalidRomError."""
    with pytest.raises(InvalidRomError, match="power of 2"):
        UxROMMapper(_uxrom_image(prg_banks=3))


def test_uxrom_construction_rejects_prg_ram_over_8k():
    """PRG RAM/NVRAM > 8 KiB → InvalidRomError."""
    with pytest.raises(InvalidRomError, match="PRG RAM"):
        UxROMMapper(_uxrom_image(prg_banks=4, prg_ram_size=16384))


# ======================================================================
# PRG Bank Switch (3)
# ======================================================================

def test_uxrom_bank_switch_within_range():
    """Write bank=2 → $8000 reads from bank 2."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    mapper.cpu_write(0x8000, 2)
    # bank 2 is filled with 0x02 bytes
    assert mapper.cpu_read(0x8000) == 0x02


def test_uxrom_bank_switch_masked_by_bank_mask():
    """4 bank ROM, write bank=7 → (7 & 3) = 3, $8000 maps to bank 3."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    mapper.cpu_write(0x8000, 7)
    # bank 3 is filled with 0x03 bytes
    assert mapper.cpu_read(0x8000) == 0x03


def test_uxrom_fixed_bank_unchanged():
    """$C000-$FFFF always maps to the last bank, regardless of bank register."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    # Last bank (index 3) is filled with 0x03
    assert mapper.cpu_read(0xC000) == 0x03
    # Switch bank → fixed bank still reads last bank
    mapper.cpu_write(0x8000, 1)
    assert mapper.cpu_read(0x8000) == 0x01  # switchable changed
    assert mapper.cpu_read(0xC000) == 0x03  # fixed unchanged


# ======================================================================
# CHR-RAM (2)
# ======================================================================

def test_uxrom_chr_ram_read_write():
    """PPU write to CHR-RAM reads back correctly."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    mapper.ppu_write(0x0123, 0xAB)
    assert mapper.ppu_read(0x0123) == 0xAB


def test_uxrom_chr_ram_initial_zero():
    """CHR-RAM initialised to all zeros."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    assert mapper.ppu_read(0x0000) == 0
    assert mapper.ppu_read(0x1FFF) == 0


# ======================================================================
# PRG RAM (1)
# ======================================================================

def test_uxrom_prg_ram_read_write():
    """$6000-$7FFF PRG RAM is read-write."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    mapper.cpu_write(0x6000, 0xCD)
    assert mapper.cpu_read(0x6000) == 0xCD
    # Verify write does NOT set bank register (only $8000+ does)
    assert mapper._prg_bank == 0


# ======================================================================
# Bank register write (2)
# ======================================================================

def test_uxrom_bank_register_write_any_address():
    """Any $8000-$FFFF write updates the bank register."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    mapper.cpu_write(0x8000, 1)
    assert mapper._prg_bank == 1
    mapper.cpu_write(0xFFFF, 2)
    assert mapper._prg_bank == 2


def test_uxrom_bank_register_masks_low_4_bits_and_bank_mask():
    """value=0x9F → (0x0F & bank_mask=3) = 3."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4))
    mapper.cpu_write(0x8000, 0x9F)
    assert mapper._prg_bank == 3
    assert mapper.cpu_read(0x8000) == 0x03


# ======================================================================
# Integration / end-to-end (2)
# ======================================================================

def test_uxrom_integration_cpu_bus_routing():
    """NESMachine routes CPU reads/writes through UxROMMapper correctly."""
    from simplenes.machine import NESMachine
    rom_bytes = build_nrom_ines(
        prg_banks=4, chr_banks=0,  # CHR RAM
        mapper_id=2, mirroring=0,
    )
    cart = RomParser.parse(bytes(rom_bytes))
    machine = NESMachine(cart)
    # PRG bank switch via CPU bus
    machine._cpu_bus.write(0x8000, 1)
    # Fixed bank (last) should be accessible
    val = machine._cpu_bus.read(0xC000)
    assert isinstance(val, int)
    # CHR-RAM write/read via PPU bus
    machine._ppu_bus.write(0x0100, 0x42)
    assert machine._ppu_bus.read(0x0100) == 0x42


def test_uxrom_mirroring_from_header():
    """Mirroring property comes from ROM header."""
    mapper = UxROMMapper(_uxrom_image(prg_banks=4, mirroring=Mirroring.VERTICAL))
    assert mapper.mirroring == Mirroring.VERTICAL
