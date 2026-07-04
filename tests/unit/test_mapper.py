"""Unit tests for NROMMapper."""

import pytest

from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.cartridge.ines import RomParser
from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
from simplenes.errors import InvalidRomError
from tests.fixtures.nrom_sample import build_nrom_ines


def _make_image(
    prg_size=16384, chr_size=8192, mirroring=Mirroring.HORIZONTAL,
    has_battery=False, prg_ram_banks=0, chr_is_rom=True,
):
    """Build a CartridgeImage with simple parameters."""
    prg_banks = prg_size // 16384
    # Use distinct values per bank so 16K/32K tests work
    prg_parts = []
    for bank in range(prg_banks):
        # bank 0: 0x00-filled, bank 1: 0xFF-filled
        fill = 0x00 if bank == 0 else 0xFF
        prg_parts.append(bytes([fill] * 16384))
    prg = b"".join(prg_parts)

    if chr_size > 0 and chr_is_rom:
        chr_ = bytes((i + 0x80) & 0xFF for i in range(chr_size))
        chr_banks = chr_size // 8192
    else:
        chr_ = b""
        chr_banks = 0

    rom = build_nrom_ines(
        prg_rom=prg,
        chr_rom=chr_,
        prg_banks=prg_banks,
        chr_banks=chr_banks,
        mirroring=0 if mirroring == Mirroring.HORIZONTAL else 1,
        has_battery=has_battery,
        prg_ram_banks=prg_ram_banks,
    )
    return RomParser.parse(bytes(rom))


# --- PRG ROM mapping ---

def test_nrom_16k_prg_read():
    """16 KiB PRG: $C000 mirrors $8000."""
    image = _make_image(prg_size=16384)
    mapper = NROMMapper(image)
    assert mapper.cpu_read(0x8000) == mapper.cpu_read(0xC000)
    assert mapper.cpu_read(0x8000) == image.prg_rom[0]


def test_nrom_32k_prg_read():
    """32 KiB PRG: $C000 reads second 16K."""
    image = _make_image(prg_size=32768)
    mapper = NROMMapper(image)
    # First 16K starts at offset 0 (0x00), second at offset 16384 (0xFF)
    assert mapper.cpu_read(0x8000) == 0x00
    assert mapper.cpu_read(0xC000) == 0xFF
    assert mapper.cpu_read(0x8000) != mapper.cpu_read(0xC000)


# --- CHR ROM/RAM ---

def test_nrom_chr_rom_read():
    """CHR ROM reads return correct bytes."""
    image = _make_image(chr_size=8192)
    mapper = NROMMapper(image)
    assert mapper.ppu_read(0x0000) == image.chr_rom[0]
    assert mapper.ppu_read(0x1FFF) == image.chr_rom[8191]


def test_nrom_chr_rom_write_ignored():
    """Writing to CHR ROM does not change data."""
    image = _make_image(chr_size=8192)
    mapper = NROMMapper(image)
    original = mapper.ppu_read(0x0000)
    mapper.ppu_write(0x0000, 0xFF)
    assert mapper.ppu_read(0x0000) == original


def test_nrom_chr_ram_read_write():
    """CHR RAM is read-write."""
    image = _make_image(chr_size=0, chr_is_rom=False)  # CHR RAM
    mapper = NROMMapper(image)
    assert image.chr_is_ram is True
    mapper.ppu_write(0x0000, 0x42)
    assert mapper.ppu_read(0x0000) == 0x42


# --- PRG RAM ---

def test_nrom_prg_ram_read_write():
    """$6000-$7FFF PRG RAM read/write."""
    image = _make_image()
    mapper = NROMMapper(image)
    mapper.cpu_write(0x6000, 0xAB)
    assert mapper.cpu_read(0x6000) == 0xAB
    mapper.cpu_write(0x7FFF, 0xCD)
    assert mapper.cpu_read(0x7FFF) == 0xCD


def test_nrom_prg_nvram_read_write():
    """Battery-backed PRG RAM at $6000-$7FFF."""
    image = _make_image(has_battery=True)
    mapper = NROMMapper(image)
    mapper.cpu_write(0x6000, 0x55)
    assert mapper.cpu_read(0x6000) == 0x55


def test_nrom_cpu_write_ignored():
    """CPU writes to $8000+ are ignored (NROM)."""
    image = _make_image()
    mapper = NROMMapper(image)
    original = mapper.cpu_read(0x8000)
    mapper.cpu_write(0x8000, 0xFF)
    assert mapper.cpu_read(0x8000) == original


def test_nrom_mirroring():
    """Mirroring property returns the header value."""
    image = _make_image(mirroring=Mirroring.VERTICAL)
    mapper = NROMMapper(image)
    assert mapper.mirroring == Mirroring.VERTICAL


# --- Validation ---

def test_nrom_invalid_prg_size():
    """PRG ROM not 16/32 KiB raises InvalidRomError."""
    # Build an image with non-standard PRG ROM size (3 banks = 48 KiB)
    rom = build_nrom_ines(prg_banks=3)
    image = RomParser.parse(bytes(rom))
    with pytest.raises(InvalidRomError, match="PRG ROM"):
        NROMMapper(image)


def test_nrom_invalid_chr_rom_size():
    """CHR ROM != 8 KiB raises InvalidRomError.

    Construct a CartridgeImage directly with invalid CHR ROM size,
    bypassing the parser's length validation.
    """
    image = CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=0,
        submapper_id=0,
        prg_rom=b"\x00" * 16384,
        chr_rom=b"\x00" * 4096,  # 4 KiB — invalid for NROM
        prg_ram_size=8192,
        prg_nvram_size=0,
        chr_ram_size=0,
        chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL,
        has_battery=False,
        has_trainer=False,
    )
    with pytest.raises(InvalidRomError, match="CHR ROM"):
        NROMMapper(image)


def test_nrom_invalid_prg_ram_size():
    """PRG RAM/NVRAM > 8 KiB raises InvalidRomError."""
    image = _make_image(prg_ram_banks=4)  # 32 KiB PRG RAM
    with pytest.raises(InvalidRomError, match="PRG RAM"):
        NROMMapper(image)
