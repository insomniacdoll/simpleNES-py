"""Unit tests for RomParser and CartridgeImage."""

import dataclasses

import pytest

from simplenes.cartridge.image import Mirroring, RomFormat
from simplenes.cartridge.ines import RomParser
from simplenes.errors import InvalidRomError, UnsupportedNES2Error
from tests.fixtures.nrom_sample import build_nes2_rom, build_nrom_ines


# --- Basic parse tests ---

def test_parse_nrom_16k_prg_8k_chr():
    """Parse a standard NROM 16K PRG + 8K CHR ROM."""
    rom = build_nrom_ines(prg_banks=1, chr_banks=1)
    image = RomParser.parse(bytes(rom))
    assert image.format == RomFormat.INES_1_0
    assert image.mapper_id == 0
    assert image.prg_rom_banks == 1
    assert len(image.prg_rom) == 16384
    assert len(image.chr_rom) == 8192
    assert image.chr_is_ram is False
    assert image.mirroring == Mirroring.HORIZONTAL


def test_parse_nrom_32k_prg():
    """Parse NROM 32K PRG ROM."""
    prg = b"\x00" * 32768
    rom = build_nrom_ines(prg_rom=prg, prg_banks=2)
    image = RomParser.parse(bytes(rom))
    assert image.prg_rom_banks == 2
    assert len(image.prg_rom) == 32768


def test_parse_nes2_rejected():
    """NES 2.0 ROM is rejected."""
    rom = build_nes2_rom()
    with pytest.raises(UnsupportedNES2Error):
        RomParser.parse(bytes(rom))


def test_parse_empty_data():
    """Empty data raises InvalidRomError."""
    with pytest.raises(InvalidRomError, match="too small"):
        RomParser.parse(b"")


def test_parse_bad_magic():
    """Bad magic bytes raise InvalidRomError."""
    rom = build_nrom_ines()
    rom[0] = 0x00  # corrupt magic
    with pytest.raises(InvalidRomError, match="Invalid NES header"):
        RomParser.parse(bytes(rom))


# --- CHR RAM ---
def test_parse_chr_ram():
    """CHR ROM banks=0 produces CHR RAM."""
    rom = build_nrom_ines(chr_banks=0)
    image = RomParser.parse(bytes(rom))
    assert image.chr_is_ram is True
    assert len(image.chr_rom) == 0
    assert image.chr_ram_size == 8192


# --- Mirroring tests ---
def test_parse_mirroring_horizontal():
    """bit0=0 → HORIZONTAL."""
    rom = build_nrom_ines(mirroring=0)
    image = RomParser.parse(bytes(rom))
    assert image.mirroring == Mirroring.HORIZONTAL


def test_parse_mirroring_vertical():
    """bit0=1 → VERTICAL."""
    rom = build_nrom_ines(mirroring=1)
    image = RomParser.parse(bytes(rom))
    assert image.mirroring == Mirroring.VERTICAL


def test_parse_four_screen():
    """bit3=1 → FOUR_SCREEN."""
    rom = build_nrom_ines(mirroring=0x08)
    image = RomParser.parse(bytes(rom))
    assert image.mirroring == Mirroring.FOUR_SCREEN


def test_parse_four_screen_overrides_vertical():
    """bit3=1 + bit0=1 → FOUR_SCREEN (four-screen priority)."""
    rom = build_nrom_ines(mirroring=0x09)
    image = RomParser.parse(bytes(rom))
    assert image.mirroring == Mirroring.FOUR_SCREEN


# --- Mapper ID ---
def test_parse_mapper_id():
    """Mapper ID bit layout is correct."""
    # mapper 3: lower nibble from flags6[7:4], upper nibble from flags7[7:4]
    flags7_upper = 0x00
    flags6_lower = 0x30  # lower nibble = 3
    rom = bytearray(build_nrom_ines(mapper_id=3, flags7=flags7_upper))
    # We set flags6 manually to be sure
    rom[6] = flags6_lower
    rom[7] = flags7_upper
    image = RomParser.parse(bytes(rom))
    assert image.mapper_id == 3


# --- Battery / Trainer ---
def test_parse_battery():
    """has_battery flag is parsed correctly."""
    rom = build_nrom_ines(has_battery=True)
    image = RomParser.parse(bytes(rom))
    assert image.has_battery is True
    # Battery ROM: prg_nvram_size > 0, prg_ram_size == 0
    assert image.prg_nvram_size > 0
    assert image.prg_ram_size == 0


def test_parse_trainer():
    """has_trainer flag: trainer data is skipped, ROM content still valid."""
    prg = b"\xEA" * 16384
    chr_ = b"\x00" * 8192
    header = bytearray(16)
    header[0:4] = b"NES\x1a"
    header[4] = 1  # PRG banks
    header[5] = 1  # CHR banks
    header[6] = 0x04  # trainer flag
    header[7] = 0
    header[8] = 0

    data = bytearray()
    data.extend(header)
    data.extend(b"\x00" * 512)  # trainer data
    data.extend(prg)
    data.extend(chr_)

    image = RomParser.parse(bytes(data))
    assert image.has_trainer is True
    assert image.prg_rom == prg


# --- PRG RAM tests ---
def test_parse_prg_ram_default_8k():
    """header[8]==0 → prg_ram_size=8192."""
    rom = build_nrom_ines(prg_ram_banks=0)
    image = RomParser.parse(bytes(rom))
    assert image.prg_ram_size == 8192
    assert image.prg_nvram_size == 0


def test_parse_prg_ram_one_bank():
    """header[8]==1 → prg_ram_size=8192."""
    rom = build_nrom_ines(prg_ram_banks=1)
    image = RomParser.parse(bytes(rom))
    assert image.prg_ram_size == 8192


def test_parse_prg_ram_multiple_banks():
    """header[8]==4 → prg_ram_size=32768."""
    rom = build_nrom_ines(prg_ram_banks=4)
    image = RomParser.parse(bytes(rom))
    assert image.prg_ram_size == 32768


def test_parse_battery_prg_nvram_size():
    """has_battery=True → prg_nvram_size>0, prg_ram_size=0."""
    rom = build_nrom_ines(has_battery=True, prg_ram_banks=2)
    image = RomParser.parse(bytes(rom))
    assert image.prg_ram_size == 0
    assert image.prg_nvram_size == 16384


# --- CartridgeImage properties ---
def test_cartridge_image_immutable():
    """CartridgeImage is frozen — cannot assign attributes."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    with pytest.raises(dataclasses.FrozenInstanceError):
        image.format = RomFormat.NES_2_0  # type: ignore[misc]


def test_parsed_rom_fields_are_bytes():
    """prg_rom and chr_rom are bytes even when parsed from a bytearray."""
    rom = build_nrom_ines()
    image = RomParser.parse(rom)  # pass bytearray, not bytes
    assert isinstance(image.prg_rom, bytes)
    assert isinstance(image.chr_rom, bytes)
    # bytes are immutable — mutation would raise TypeError
    with pytest.raises(TypeError):
        image.prg_rom[0] = 0xFF  # type: ignore[index]


def test_prg_rom_banks():
    """prg_rom_banks: 16K→1, 32K→2."""
    prg1 = b"\x00" * 16384
    rom1 = build_nrom_ines(prg_rom=prg1, prg_banks=1)
    assert RomParser.parse(bytes(rom1)).prg_rom_banks == 1

    prg2 = b"\x00" * 32768
    rom2 = build_nrom_ines(prg_rom=prg2, prg_banks=2)
    assert RomParser.parse(bytes(rom2)).prg_rom_banks == 2


def test_chr_is_ram():
    """chr_is_ram: True when CHR ROM is empty."""
    rom = build_nrom_ines(chr_banks=0)
    assert RomParser.parse(bytes(rom)).chr_is_ram is True

    rom2 = build_nrom_ines(chr_banks=1)
    assert RomParser.parse(bytes(rom2)).chr_is_ram is False
