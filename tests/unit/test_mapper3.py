"""Unit tests for CNROMMapper (Mapper 3)."""

import pytest

from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.cartridge.mappers.mapper003_cnrom import CNROMMapper
from simplenes.errors import InvalidRomError


def _cnrom_image(prg_size=16384, chr_banks=2, prg_ram_size=0):
    """Build a CartridgeImage for CNROM testing."""
    prg = bytes(range(256)) * (prg_size // 256)
    chr_rom_size = chr_banks * 8192
    # Each 8 KiB CHR bank filled with its index
    chr_parts = [bytes([bank] * 8192) for bank in range(chr_banks)]
    return CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=3,
        submapper_id=0,
        prg_rom=prg[:prg_size],
        chr_rom=b"".join(chr_parts)[:chr_rom_size],
        prg_ram_size=prg_ram_size,
        prg_nvram_size=0,
        chr_ram_size=0,
        chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL,
        has_battery=False,
        has_trainer=False,
    )


# ======================================================================
# Construction & validation
# ======================================================================

def test_cnrom_construction_16k_prg():
    mapper = CNROMMapper(_cnrom_image(prg_size=16384, chr_banks=2))
    assert mapper._chr_banks == 2
    assert mapper._chr_bank == 0
    assert len(mapper._prg_ram) == 8192


def test_cnrom_construction_32k_prg():
    mapper = CNROMMapper(_cnrom_image(prg_size=32768, chr_banks=4))
    assert mapper._chr_banks == 4


def test_cnrom_construction_rejects_no_chr_rom():
    with pytest.raises(InvalidRomError, match="CHR-ROM"):
        CNROMMapper(_cnrom_image(chr_banks=0))


def test_cnrom_construction_rejects_non_power_of_two_chr_banks():
    with pytest.raises(InvalidRomError, match="power of 2"):
        CNROMMapper(_cnrom_image(chr_banks=3))


# ======================================================================
# CHR bank switch
# ======================================================================

def test_cnrom_chr_bank_switch_within_range():
    mapper = CNROMMapper(_cnrom_image(chr_banks=4))
    mapper.cpu_write(0x8000, 2)
    assert mapper._chr_bank == 2
    assert mapper.ppu_read(0x0000) == 2  # bank 2 filled with 0x02


def test_cnrom_chr_bank_switch_masked():
    mapper = CNROMMapper(_cnrom_image(chr_banks=4))
    mapper.cpu_write(0x8000, 7)
    assert mapper._chr_bank == 3  # 7 & 3 = 3
    assert mapper.ppu_read(0x0000) == 3


def test_cnrom_default_bank_zero():
    mapper = CNROMMapper(_cnrom_image(chr_banks=4))
    assert mapper._chr_bank == 0
    assert mapper.ppu_read(0x0000) == 0


# ======================================================================
# PRG
# ======================================================================

def test_cnrom_prg_16k_mirrors_c000():
    mapper = CNROMMapper(_cnrom_image(prg_size=16384))
    val_8000 = mapper.cpu_read(0x8000)
    val_c000 = mapper.cpu_read(0xC000)
    assert val_c000 == val_8000


# ======================================================================
# PRG RAM
# ======================================================================

def test_cnrom_prg_ram_read_write():
    mapper = CNROMMapper(_cnrom_image())
    mapper.cpu_write(0x6000, 0xAB)
    assert mapper.cpu_read(0x6000) == 0xAB


# ======================================================================
# Integration
# ======================================================================

def test_cnrom_integration_cpu_bus_routing():
    from simplenes.cartridge.ines import RomParser
    from simplenes.machine import NESMachine
    from tests.fixtures.nrom_sample import build_nrom_ines
    rom = build_nrom_ines(prg_banks=1, chr_banks=4, mapper_id=3, mirroring=0)
    cart = RomParser.parse(bytes(rom))
    machine = NESMachine(cart)
    val = machine._cpu_bus.read(0x8000)
    assert isinstance(val, int)
