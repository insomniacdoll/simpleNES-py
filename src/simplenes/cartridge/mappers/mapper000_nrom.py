"""Mapper 0: NROM (No ROM mapper)."""

from simplenes.cartridge.image import CartridgeImage, Mirroring
from simplenes.errors import InvalidRomError


class NROMMapper:
    """Mapper 0: NROM (No ROM mapper).

    - PRG ROM: 16 KiB / 32 KiB at $8000-$FFFF
    - PRG RAM: at $6000-$7FFF (volatile + battery-backed combined, max 8 KiB)
    - CHR ROM: 8 KiB at PPU $0000-$1FFF (read-only)
         or CHR RAM: 8 KiB at PPU $0000-$1FFF (read-write)
    - Fixed mirroring from header
    - No bank switching / IRQ
    """

    __slots__ = (
        "_image",
        "_prg_rom",
        "_prg_ram",           # $6000-$7FFF, max 8 KiB
        "_chr_memory",
        "_chr_is_ram",
        "_mirroring",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._image = image
        self._prg_rom = image.prg_rom  # bytes, semantically read-only
        self._mirroring = image.mirroring
        self._chr_is_ram = image.chr_is_ram

        # Validate PRG ROM size
        if len(self._prg_rom) not in (16384, 32768):
            raise InvalidRomError(
                f"NROM PRG ROM must be 16 KiB or 32 KiB, got {len(self._prg_rom)}"
            )

        # Validate CHR ROM size if not CHR RAM
        if not self._chr_is_ram and len(image.chr_rom) != 8192:
            raise InvalidRomError(
                f"NROM CHR ROM must be 8 KiB, got {len(image.chr_rom)}"
            )

        # PRG RAM: $6000-$7FFF — combine volatile + battery-backed
        prg_memory_size = image.prg_ram_size + image.prg_nvram_size
        # NROM CPU address window for PRG RAM is only 8 KiB
        if prg_memory_size > 8192:
            raise InvalidRomError(
                f"NROM PRG RAM/NVRAM must be <= 8 KiB, got {prg_memory_size}"
            )
        self._prg_ram = bytearray(prg_memory_size or 8192)

        # CHR memory
        if self._chr_is_ram:
            self._chr_memory = bytearray(image.chr_ram_size or 8192)
        else:
            self._chr_memory = bytearray(image.chr_rom)

    # --- PRG ROM mapping ---
    def _prg_offset(self, address: int) -> int:
        """Map $8000-$FFFF to PRG ROM offset."""
        offset = (address - 0x8000) & 0x7FFF
        if len(self._prg_rom) == 16384:
            offset &= 0x3FFF
        return offset

    # --- CPU bus ---
    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if 0x8000 <= address <= 0xFFFF:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF

    # --- PPU bus ---
    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_memory[address & 0x1FFF]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if self._chr_is_ram and 0x0000 <= address <= 0x1FFF:
            self._chr_memory[address & 0x1FFF] = value & 0xFF

    def observe_ppu_address(self, address: int) -> None:
        pass

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
