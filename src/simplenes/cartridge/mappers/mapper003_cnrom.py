"""Mapper 3: CNROM."""

from simplenes.cartridge.image import CartridgeImage, Mirroring
from simplenes.errors import InvalidRomError


class CNROMMapper:
    """Mapper 3: CNROM.

    - PRG ROM: 16 KiB or 32 KiB, no banking
    - CHR-ROM: 8 KiB bank switching, write $8000-$FFFF selects bank
    - Mirroring: fixed from header
    - No IRQ
    """

    __slots__ = (
        "_prg_rom", "_prg_ram",
        "_chr_rom", "_chr_banks",
        "_chr_bank", "_mirroring",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._prg_rom = image.prg_rom
        self._mirroring = image.mirroring

        if len(self._prg_rom) not in (16384, 32768):
            raise InvalidRomError(
                f"CNROM PRG ROM must be 16 KiB or 32 KiB, got {len(self._prg_rom)}"
            )

        chr_size = len(image.chr_rom)
        if chr_size == 0 or chr_size % 8192 != 0:
            raise InvalidRomError(
                "CNROM requires CHR-ROM in 8 KiB multiples"
            )
        self._chr_banks = chr_size // 8192
        if self._chr_banks & (self._chr_banks - 1):
            raise InvalidRomError(
                "CNROM CHR bank count must be power of 2"
            )
        self._chr_rom = image.chr_rom
        self._chr_bank = 0

        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(
                f"CNROM PRG RAM/NVRAM must be <= 8 KiB, got {prg_ram_total}"
            )
        self._prg_ram = bytearray(8192)

    def _prg_offset(self, address: int) -> int:
        offset = (address - 0x8000) & 0x7FFF
        if len(self._prg_rom) == 16384:
            offset &= 0x3FFF
        return offset

    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if address >= 0x8000:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
        elif address >= 0x8000:
            self._chr_bank = value & (self._chr_banks - 1)

    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_rom[self._chr_bank * 8192 + (address & 0x1FFF)]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        pass  # CHR-ROM is read-only

    def observe_ppu_address(self, address: int) -> None:
        pass

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
