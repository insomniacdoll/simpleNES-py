"""Mapper 2: UxROM (UNROM)."""

from simplenes.cartridge.image import CartridgeImage, Mirroring
from simplenes.errors import InvalidRomError


class UxROMMapper:
    """Mapper 2: UxROM (UNROM).

    - PRG ROM: 16 KiB switchable bank at $8000-$BFFF
               + 16 KiB fixed bank at $C000-$FFFF (last bank)
    - CHR: 8 KiB CHR-RAM (always read-write)
    - Mirroring: fixed from header
    - No IRQ
    """

    __slots__ = (
        "_prg_rom", "_prg_banks", "_bank_mask",
        "_prg_bank",            # 0..bank_mask, selects bank at $8000-$BFFF
        "_fixed_bank_offset",   # offset to last bank ($C000-$FFFF)
        "_prg_ram",             # $6000-$7FFF, up to 8 KiB
        "_chr_ram",             # 8 KiB CHR-RAM
        "_mirroring",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._prg_rom = image.prg_rom
        self._prg_banks = len(image.prg_rom) // 16384
        self._mirroring = image.mirroring

        # Validate PRG bank count
        if self._prg_banks < 2 or self._prg_banks > 16:
            raise InvalidRomError(
                f"UxROM PRG banks must be 2-16, got {self._prg_banks}"
            )
        if self._prg_banks & (self._prg_banks - 1):
            raise InvalidRomError(
                f"UxROM PRG bank count must be power of 2, got {self._prg_banks}"
            )

        self._bank_mask = self._prg_banks - 1
        self._prg_bank = 0
        self._fixed_bank_offset = (self._prg_banks - 1) * 0x4000

        # Reject CHR-ROM UxROM in Phase 8
        if len(image.chr_rom) != 0:
            raise InvalidRomError(
                "UxROM with CHR ROM is not supported in Phase 8"
            )

        # PRG RAM: $6000-$7FFF, window is 8 KiB
        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(
                f"UxROM PRG RAM/NVRAM must be <= 8 KiB, got {prg_ram_total}"
            )
        self._prg_ram = bytearray(8192)

        # CHR-RAM: 8 KiB, initialised to zero
        self._chr_ram = bytearray(8192)

    # --- PRG ROM mapping ---

    def _switchable_offset(self, address: int) -> int:
        return self._prg_bank * 0x4000 + (address - 0x8000)

    def _fixed_offset(self, address: int) -> int:
        return self._fixed_bank_offset + (address - 0xC000)

    # --- CPU bus ---

    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if 0x8000 <= address <= 0xBFFF:
            return self._prg_rom[self._switchable_offset(address)]
        if 0xC000 <= address <= 0xFFFF:
            return self._prg_rom[self._fixed_offset(address)]
        return 0

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
        elif address >= 0x8000:
            # Any write to $8000-$FFFF sets bank register
            self._prg_bank = (value & 0x0F) & self._bank_mask

    # --- PPU bus ---

    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_ram[address & 0x1FFF]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if 0x0000 <= address <= 0x1FFF:
            self._chr_ram[address & 0x1FFF] = value & 0xFF

    def observe_ppu_address(self, address: int) -> None:
        pass  # UxROM has no PPU address observer requirement

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
