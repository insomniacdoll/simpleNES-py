"""Mapper 1: MMC1 (SxROM)."""

from simplenes.cartridge.image import CartridgeImage, Mirroring
from simplenes.errors import InvalidRomError


class MMC1Mapper:
    """Mapper 1: MMC1 (Nintendo MMC1).

    - Serial write protocol with 5-bit shift register
    - 4 internal registers (control, chr0, chr1, prg)
    - PRG: 32 KiB / 16+16 KiB banking with 4 modes
    - CHR: 8 KiB / 4+4 KiB banking
    - Mirroring controlled by MMC1
    - PRG RAM (WRAM) with battery support
    - No IRQ
    """

    __slots__ = (
        "_prg_rom", "_prg_banks",
        "_prg_ram", "_chr_memory", "_chr_is_ram",
        "_shift_reg", "_shift_count",
        "_control", "_chr_bank0", "_chr_bank1", "_prg_bank",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._prg_rom = image.prg_rom
        self._prg_banks = len(image.prg_rom) // 16384

        if len(image.prg_rom) == 0 or len(image.prg_rom) % 0x4000 != 0:
            raise InvalidRomError("MMC1 PRG ROM must be 16 KiB aligned")

        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(
                f"MMC1 PRG RAM/NVRAM must be <= 8 KiB, got {prg_ram_total}"
            )
        self._prg_ram = bytearray(8192)

        self._chr_is_ram = image.chr_is_ram
        if self._chr_is_ram:
            self._chr_memory = bytearray(8192)
        else:
            chr_size = len(image.chr_rom)
            if chr_size == 0 or chr_size % 4096 != 0:
                raise InvalidRomError("MMC1 CHR ROM must be 4 KiB aligned")
            self._chr_memory = bytearray(image.chr_rom)

        # Power-on state
        self._shift_reg = 0x10
        self._shift_count = 0
        self._control = 0x0C
        self._chr_bank0 = 0
        self._chr_bank1 = 0
        self._prg_bank = 0

    # ----------------------------------------------------------------
    # Serial write protocol
    # ----------------------------------------------------------------

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
            return

        if address < 0x8000:
            return

        if value & 0x80:
            self._shift_reg = 0x10
            self._shift_count = 0
            self._control = self._control | 0x0C
            return

        self._shift_reg = ((self._shift_reg >> 1) | ((value & 1) << 4)) & 0x1F
        self._shift_count += 1
        if self._shift_count == 5:
            self._load_register(address, self._shift_reg)
            self._shift_reg = 0x10
            self._shift_count = 0

    def _load_register(self, address: int, value: int) -> None:
        reg = (address >> 13) & 3
        if reg == 0:
            self._control = value
        elif reg == 1:
            self._chr_bank0 = value
        elif reg == 2:
            self._chr_bank1 = value
        elif reg == 3:
            self._prg_bank = value

    # ----------------------------------------------------------------
    # PRG ROM
    # ----------------------------------------------------------------

    def _prg_offset(self, address: int) -> int:
        mode = (self._control >> 2) & 3
        if mode < 2:
            bank = self._prg_bank & 0x0E
            offset = bank * 0x4000 + (address & 0x7FFF)
        elif mode == 2:
            bank = 0 if address < 0xC000 else self._prg_bank & 0x0F
            offset = bank * 0x4000 + (address & 0x3FFF)
        else:  # mode == 3
            bank = (self._prg_bank & 0x0F) if address < 0xC000 else self._prg_banks - 1
            offset = bank * 0x4000 + (address & 0x3FFF)
        return offset % len(self._prg_rom)

    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if address >= 0x8000:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    # ----------------------------------------------------------------
    # CHR
    # ----------------------------------------------------------------

    def _chr_offset(self, address: int) -> int:
        if self._control & 0x10:
            if address < 0x1000:
                return self._chr_bank0 * 0x1000 + (address & 0xFFF)
            return self._chr_bank1 * 0x1000 + (address & 0xFFF)
        else:
            bank = self._chr_bank0 & 0x1E
            return bank * 0x1000 + (address & 0x1FFF)

    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_memory[self._chr_offset(address) % len(self._chr_memory)]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if self._chr_is_ram and 0x0000 <= address <= 0x1FFF:
            self._chr_memory[self._chr_offset(address) % len(self._chr_memory)] = value & 0xFF

    def observe_ppu_address(self, address: int) -> None:
        pass

    @property
    def mirroring(self) -> Mirroring:
        mm = self._control & 3
        if mm == 0:
            return Mirroring.SINGLE_SCREEN_LOWER
        if mm == 1:
            return Mirroring.SINGLE_SCREEN_UPPER
        if mm == 2:
            return Mirroring.VERTICAL
        return Mirroring.HORIZONTAL
