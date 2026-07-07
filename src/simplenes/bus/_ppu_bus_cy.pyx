# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""PPUBus — Cython accelerated implementation.

Line-for-line port of simplenes.bus.ppu_bus with:
  - cdef class and cpdef hot-path methods
  - Construction-time Mirroring enum int cache for fast C comparisons
  - Same safety semantics: dynamic mirroring reads, observer unchanged
"""


cdef class PPUBusCy:
    cdef:
        object _mapper
        bytearray _nametables, _palette_ram, _palette_cache
        object _observe_ppu  # None or mapper.observe_ppu_address
        int _mir_h, _mir_v, _mir_sl, _mir_su, _mir_4s

    def __init__(self, mapper):
        from simplenes.cartridge.image import Mirroring

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

        # Cache Mirroring enum int values for fast C comparison
        self._mir_h  = int(Mirroring.HORIZONTAL.value)
        self._mir_v  = int(Mirroring.VERTICAL.value)
        self._mir_4s = int(Mirroring.FOUR_SCREEN.value)
        self._mir_sl = int(Mirroring.SINGLE_SCREEN_LOWER.value)
        self._mir_su = int(Mirroring.SINGLE_SCREEN_UPPER.value)

    def get_palette_cache(self):
        """Return the shared palette cache bytearray."""
        return self._palette_cache

    # ----------------------------------------------------------------
    # Public read/write - cpdef for fast C call path
    # ----------------------------------------------------------------

    cpdef int read(self, int address):
        """Read one byte from PPU address space."""
        address &= 0x3FFF
        if self._observe_ppu is not None:
            self._observe_ppu(address)

        if address < 0x2000:
            return self._mapper.ppu_read(address)
        if address < 0x3F00:
            return self._read_nametable(address)
        return self._read_palette(address)

    cpdef void write(self, int address, int value):
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

    cpdef int peek_palette(self, int index):
        """Return palette value at *index* (0–31), applying mirror rules.

        Does NOT trigger ``observe_ppu_address()``.
        """
        cdef int idx = index & 0x1F
        if idx >= 0x10 and (idx & 3) == 0:
            idx &= 0x0F
        return self._palette_cache[idx] & 0x3F

    # ----------------------------------------------------------------
    # Nametable mirroring — dynamic, using cached enum ints
    # ----------------------------------------------------------------

    cdef int _nametable_index(self, int address):
        """Map address to nametable byte index (0-2047) based on current mirroring."""
        cdef int mirroring = int(self._mapper.mirroring.value)
        cdef int nt_select = (address >> 10) & 3

        if mirroring == self._mir_h:
            nt_select = nt_select >> 1  # NT0/NT1→0, NT2/NT3→1
        elif mirroring == self._mir_v:
            nt_select = nt_select & 1   # NT0/NT2→0, NT1/NT3→1
        elif mirroring == self._mir_4s:
            from simplenes.errors import PPUBusError
            raise PPUBusError(
                "Four-screen mirroring is not supported in Phase 1"
            )
        elif mirroring == self._mir_sl:
            nt_select = 0
        elif mirroring == self._mir_su:
            nt_select = 1

        return nt_select * 1024 + (address & 0x3FF)

    cdef int _read_nametable(self, int address):
        return self._nametables[self._nametable_index(address)]

    cdef void _write_nametable(self, int address, int value):
        self._nametables[self._nametable_index(address)] = value & 0xFF

    # ----------------------------------------------------------------
    # Palette (with cache sync)
    # ----------------------------------------------------------------

    cdef int _palette_index(self, int address):
        cdef int idx = address & 0x1F
        if idx >= 0x10 and (idx & 3) == 0:
            idx &= 0x0F
        return idx

    cdef int _read_palette(self, int address):
        return self._palette_ram[self._palette_index(address)]

    cdef void _write_palette(self, int address, int value):
        cdef int idx = self._palette_index(address)
        self._palette_ram[idx] = value & 0xFF
        self._palette_cache[idx] = value & 0x3F  # keep cache in sync

    cdef void _sync_palette_cache(self):
        """Full sync from _palette_ram to _palette_cache.

        Called once during construction.
        """
        cdef int i, idx
        for i in range(32):
            idx = i
            if idx >= 0x10 and (idx & 3) == 0:
                idx &= 0x0F
            self._palette_cache[i] = self._palette_ram[idx] & 0x3F
