"""Cartridge image — immutable parsed ROM data."""

from dataclasses import dataclass
from enum import Enum, auto


class Mirroring(Enum):
    HORIZONTAL = auto()
    VERTICAL = auto()
    FOUR_SCREEN = auto()
    SINGLE_SCREEN_LOWER = auto()
    SINGLE_SCREEN_UPPER = auto()


class RomFormat(Enum):
    INES_1_0 = auto()
    NES_2_0 = auto()


@dataclass(frozen=True, slots=True)
class CartridgeImage:
    """Immutable parsed ROM image. No runtime bank state."""

    format: RomFormat
    mapper_id: int
    submapper_id: int

    prg_rom: bytes
    chr_rom: bytes

    prg_ram_size: int       # bytes of volatile PRG RAM
    prg_nvram_size: int     # bytes of battery-backed PRG RAM
    chr_ram_size: int       # bytes of CHR RAM (8192 if CHR RAM, 0 if CHR ROM)
    chr_nvram_size: int     # battery-backed CHR (0 for NROM)

    mirroring: Mirroring
    has_battery: bool
    has_trainer: bool

    @property
    def prg_rom_banks(self) -> int:
        """Number of 16 KiB PRG ROM banks."""
        return len(self.prg_rom) // 16384

    @property
    def chr_is_ram(self) -> bool:
        """True if cartridge uses CHR RAM instead of CHR ROM."""
        return len(self.chr_rom) == 0 and self.chr_ram_size > 0
