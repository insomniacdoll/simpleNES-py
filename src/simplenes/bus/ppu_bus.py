"""PPU address space (16 KiB, mirrored)."""

from simplenes.cartridge.image import Mirroring
from simplenes.errors import PPUBusError


class PPUBus:
    """
    $0000-$1FFF  : CHR via Mapper
    $2000-$2FFF  : Nametables (2 KiB)
    $3000-$3EFF  : Nametable mirror
    $3F00-$3FFF  : Palette RAM
    """

    __slots__ = ("_mapper", "_nametables", "_palette_ram")
    PALETTE_MIRRORS = {0x10: 0x00, 0x14: 0x04, 0x18: 0x08, 0x1C: 0x0C}

    def __init__(self, mapper):
        self._mapper = mapper
        self._nametables = bytearray(2048)
        self._palette_ram = bytearray(32)

    def read(self, address: int) -> int:
        """Read one byte from PPU address space."""
        address &= 0x3FFF
        self._mapper.observe_ppu_address(address)

        if address < 0x2000:
            return self._mapper.ppu_read(address)

        if address < 0x3F00:
            return self._read_nametable(address)

        return self._read_palette(address)

    def write(self, address: int, value: int) -> None:
        """Write one byte to PPU address space."""
        address &= 0x3FFF
        self._mapper.observe_ppu_address(address)

        if address < 0x2000:
            self._mapper.ppu_write(address, value)
            return

        if address < 0x3F00:
            self._write_nametable(address, value)
            return

        self._write_palette(address, value)

    # --- Nametable mirroring ---
    # Horizontal: NT0/NT1 → 0,  NT2/NT3 → 1  → nt_select >>= 1
    # Vertical:   NT0/NT2 → 0,  NT1/NT3 → 1  → nt_select &= 1
    def _nametable_index(self, address: int) -> int:
        """Map address to nametable byte index (0-2047) based on current mirroring."""
        mirroring = self._mapper.mirroring
        nt_select = (address >> 10) & 3

        if mirroring == Mirroring.HORIZONTAL:
            nt_select = nt_select >> 1  # NT0/NT1→0, NT2/NT3→1
        elif mirroring == Mirroring.VERTICAL:
            nt_select = nt_select & 1   # NT0/NT2→0, NT1/NT3→1
        elif mirroring == Mirroring.FOUR_SCREEN:
            raise PPUBusError("Four-screen mirroring is not supported in Phase 1")
        elif mirroring == Mirroring.SINGLE_SCREEN_LOWER:
            nt_select = 0
        elif mirroring == Mirroring.SINGLE_SCREEN_UPPER:
            nt_select = 1

        return nt_select * 1024 + (address & 0x3FF)

    def _read_nametable(self, address: int) -> int:
        return self._nametables[self._nametable_index(address)]

    def _write_nametable(self, address: int, value: int) -> None:
        self._nametables[self._nametable_index(address)] = value & 0xFF

    # --- Palette ---
    def _palette_index(self, address: int) -> int:
        idx = address & 0x1F
        if idx in self.PALETTE_MIRRORS:
            idx = self.PALETTE_MIRRORS[idx]
        return idx

    def _read_palette(self, address: int) -> int:
        return self._palette_ram[self._palette_index(address)]

    def _write_palette(self, address: int, value: int) -> None:
        self._palette_ram[self._palette_index(address)] = value & 0xFF
