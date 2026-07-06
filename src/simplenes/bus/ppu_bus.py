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

    __slots__ = (
        "_mapper", "_nametables", "_palette_ram",
        "_palette_cache",     # bytearray(32), synced with _palette_ram
        "_observe_ppu",        # Optional[Callable] — None if mapper has no observer
    )

    def __init__(self, mapper):
        self._mapper = mapper
        self._nametables = bytearray(2048)
        self._palette_ram = bytearray(32)
        self._palette_cache = bytearray(32)
        self._sync_palette_cache()

        self._observe_ppu = (
            self._mapper.observe_ppu_address
            if getattr(self._mapper, "has_ppu_observer", False)
            else None
        )

    # ----------------------------------------------------------------
    # Public read/write
    # ----------------------------------------------------------------

    def read(self, address: int) -> int:
        """Read one byte from PPU address space."""
        address &= 0x3FFF
        if self._observe_ppu is not None:
            self._observe_ppu(address)

        if address < 0x2000:
            return self._mapper.ppu_read(address)

        if address < 0x3F00:
            return self._read_nametable(address)

        return self._read_palette(address)

    def write(self, address: int, value: int) -> None:
        """Write one byte to PPU address space."""
        address &= 0x3FFF
        if self._observe_ppu is not None:
            self._observe_ppu(address)

        if address < 0x2000:
            self._mapper.ppu_write(address, value)
            return

        if address < 0x3F00:
            self._write_nametable(address, value)
            return

        self._write_palette(address, value)

    # ----------------------------------------------------------------
    # Fast palette read — no mapper observer, no nametable mirror eval
    # ----------------------------------------------------------------

    def peek_palette(self, index: int) -> int:
        """Return palette value at *index* (0–31), applying mirror rules.

        Used by PPU pixel output.  Does NOT trigger
        ``observe_ppu_address()``.
        """
        idx = index & 0x1F
        if idx >= 0x10 and (idx & 3) == 0:
            idx &= 0x0F
        return self._palette_cache[idx] & 0x3F

    # ----------------------------------------------------------------
    # Nametable mirroring
    # ----------------------------------------------------------------

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

    # ----------------------------------------------------------------
    # Palette (with cache sync)
    # ----------------------------------------------------------------

    def _palette_index(self, address: int) -> int:
        idx = address & 0x1F
        if idx >= 0x10 and (idx & 3) == 0:
            idx &= 0x0F
        return idx

    def _read_palette(self, address: int) -> int:
        return self._palette_ram[self._palette_index(address)]

    def _write_palette(self, address: int, value: int) -> None:
        idx = self._palette_index(address)
        self._palette_ram[idx] = value & 0xFF
        self._palette_cache[idx] = value & 0x3F  # keep cache in sync

    def _sync_palette_cache(self) -> None:
        """Full sync from _palette_ram to _palette_cache.

        Called once during construction (and reset, if PPUBus ever gains one).
        """
        for i in range(32):
            idx = i
            if idx >= 0x10 and (idx & 3) == 0:
                idx &= 0x0F
            self._palette_cache[i] = self._palette_ram[idx] & 0x3F
