"""Mapper 4: MMC3 (TxROM)."""

from simplenes.cartridge.image import CartridgeImage, Mirroring
from simplenes.errors import InvalidRomError


class MMC3Mapper:
    """Mapper 4: MMC3 (Nintendo MMC3).

    - 8 KiB PRG banking: two switchable + two fixed banks
    - Fine-grained CHR banking: 2×2 KiB + 4×1 KiB
    - CHR A12 inversion for scanline effects
    - Scanline IRQ counter via PPU A12 observation
    - Mirroring controlled by MMC3 (H/V)
    - PRG RAM (WRAM) with battery support
    """

    has_ppu_observer = True

    __slots__ = (
        "_prg_rom", "_prg_banks",
        "_prg_bank0", "_prg_bank1",
        "_chr_memory", "_chr_is_ram",
        "_chr_banks",
        "_bank_select", "_prg_mode", "_chr_invert",
        "_mirroring", "_prg_ram",
        "_irq_latch", "_irq_counter",
        "_irq_reload_flag", "_irq_enabled", "_irq_pending",
        "_a12_prev", "_interrupts",
    )

    def __init__(self, image: CartridgeImage, interrupts):
        self._prg_rom = image.prg_rom
        self._prg_banks = len(image.prg_rom) // 8192
        self._interrupts = interrupts

        self._chr_is_ram = image.chr_is_ram

        if len(self._prg_rom) < 0x8000 or len(self._prg_rom) % 0x2000 != 0:
            raise InvalidRomError(
                "MMC3 PRG ROM must be >= 32 KiB and 8 KiB aligned"
            )
        if not self._chr_is_ram and (
            len(image.chr_rom) == 0 or len(image.chr_rom) % 0x400 != 0
        ):
            raise InvalidRomError("MMC3 CHR ROM must be 1 KiB aligned")
        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(
                f"MMC3 PRG RAM/NVRAM must be <= 8 KiB, got {prg_ram_total}"
            )

        self._chr_memory = bytearray(
            image.chr_rom if not self._chr_is_ram else 8192
        )

        self._chr_banks = [0] * 8
        self._prg_bank0 = 0
        self._prg_bank1 = 1
        self._bank_select = 0
        self._prg_mode = False
        self._chr_invert = False
        self._mirroring = Mirroring.HORIZONTAL
        self._prg_ram = bytearray(8192)

        self._irq_latch = 0
        self._irq_counter = 0
        self._irq_reload_flag = False
        self._irq_enabled = False
        self._irq_pending = False
        self._a12_prev = False

    # ----------------------------------------------------------------
    # CPU register interface
    # ----------------------------------------------------------------

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
            return

        reg = address & 0xE001
        if reg == 0x8000:
            self._bank_select = value & 0x07
            self._prg_mode = bool(value & 0x40)
            self._chr_invert = bool(value & 0x80)
        elif reg == 0x8001:
            self._write_bank_data(value)
        elif reg == 0xA000:
            self._mirroring = (
                Mirroring.HORIZONTAL if (value & 1) else Mirroring.VERTICAL
            )
        elif reg == 0xA001:
            pass  # PRG RAM protect — ignored in Phase 8
        elif reg == 0xC000:
            self._irq_latch = value
        elif reg == 0xC001:
            self._irq_reload_flag = True
        elif reg == 0xE000:
            self._irq_enabled = False
            self._irq_pending = False
            self._interrupts.irq_mapper = False
        elif reg == 0xE001:
            self._irq_enabled = True

    def _write_bank_data(self, value: int) -> None:
        target = self._bank_select
        if target <= 1:
            self._chr_banks[target] = value & 0xFE
        elif target <= 5:
            self._chr_banks[target] = value
        elif target == 6:
            self._prg_bank0 = value & 0x3F
        elif target == 7:
            self._prg_bank1 = value & 0x3F

    # ----------------------------------------------------------------
    # CPU read (PRG)
    # ----------------------------------------------------------------

    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if address >= 0x8000:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    def _prg_offset(self, address: int) -> int:
        if address < 0xA000:
            bank = self._prg_banks - 2 if self._prg_mode else self._prg_bank0
        elif address < 0xC000:
            bank = self._prg_bank1
        elif address < 0xE000:
            bank = self._prg_bank0 if self._prg_mode else self._prg_banks - 2
        else:
            bank = self._prg_banks - 1
        return (bank * 0x2000 + (address & 0x1FFF)) % len(self._prg_rom)

    # ----------------------------------------------------------------
    # PPU read/write (CHR)
    # ----------------------------------------------------------------

    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_memory[self._chr_offset(address) % len(self._chr_memory)]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if self._chr_is_ram and 0x0000 <= address <= 0x1FFF:
            self._chr_memory[self._chr_offset(address) % len(self._chr_memory)] = value & 0xFF

    def _chr_offset(self, address: int) -> int:
        addr = address & 0x1FFF
        if not self._chr_invert:
            if addr < 0x0800:
                return self._chr_banks[0] * 0x400 + (addr & 0x7FF)
            elif addr < 0x1000:
                return self._chr_banks[1] * 0x400 + (addr & 0x7FF)
            elif addr < 0x1400:
                return self._chr_banks[2] * 0x400 + (addr - 0x1000)
            elif addr < 0x1800:
                return self._chr_banks[3] * 0x400 + (addr - 0x1400)
            elif addr < 0x1C00:
                return self._chr_banks[4] * 0x400 + (addr - 0x1800)
            else:
                return self._chr_banks[5] * 0x400 + (addr - 0x1C00)
        else:
            if addr < 0x0400:
                return self._chr_banks[2] * 0x400 + addr
            elif addr < 0x0800:
                return self._chr_banks[3] * 0x400 + (addr - 0x0400)
            elif addr < 0x0C00:
                return self._chr_banks[4] * 0x400 + (addr - 0x0800)
            elif addr < 0x1000:
                return self._chr_banks[5] * 0x400 + (addr - 0x0C00)
            elif addr < 0x1800:
                return self._chr_banks[0] * 0x400 + (addr - 0x1000)
            else:
                return self._chr_banks[1] * 0x400 + (addr - 0x1800)

    # ----------------------------------------------------------------
    # PPU address observation (A12 rising edge → IRQ clock)
    # ----------------------------------------------------------------

    def observe_ppu_address(self, address: int) -> None:
        a12 = bool(address & 0x1000)
        if not self._a12_prev and a12:
            self._clock_irq()
        self._a12_prev = a12

    def _clock_irq(self) -> None:
        if self._irq_counter == 0 or self._irq_reload_flag:
            self._irq_counter = self._irq_latch
            self._irq_reload_flag = False
        else:
            self._irq_counter -= 1
        if self._irq_counter == 0 and self._irq_enabled:
            self._irq_pending = True
            self._interrupts.irq_mapper = True

    # ----------------------------------------------------------------
    # Mirroring
    # ----------------------------------------------------------------

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
