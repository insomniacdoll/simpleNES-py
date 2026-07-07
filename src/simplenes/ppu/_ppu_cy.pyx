# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True
"""PPU (Ricoh 2C02) — Cython accelerated implementation.

This is a line-for-line port of simplenes.ppu.ppu, with:
  - cdef public for externally accessed attributes
  - cdef internal attributes for hot-path fields
  - cached bus callables (_bus_read, _bus_write, _peek_palette)
  - _clock_one_dot() as cdef with inlined dot logic
"""


cdef class PPUCy:
    # ==================================================================
    # Public attributes — cdef public for Python access
    # ==================================================================
    cdef public int control, mask, status, oam_address
    cdef public int v, t, fine_x
    cdef public bint write_toggle, odd_frame
    cdef public int read_buffer, scanline, dot, frame
    cdef public bytearray framebuffer, oam

    # ==================================================================
    # Internal attributes (cdef — not Python-visible)
    # ==================================================================
    cdef:
        object bus, interrupts
        object _bus_read, _bus_write, _peek_palette
        bytearray _palette_cache
        bint _nmi_prev, _rendering, _bg_enabled, _sprite_zero_possible
        int _bg_shift_lo, _bg_shift_hi, _bg_attr_lo, _bg_attr_hi
        int _nt_latch, _at_latch, _pt_lo_latch, _pt_hi_latch
        int _sprite_count, _last_bg_pixel
        bytearray _secondary_oam

    # ==================================================================
    # __init__ / reset
    # ==================================================================

    def __init__(self, bus, interrupts, region=None, palette_cache=None):
        self.bus = bus
        self.interrupts = interrupts

        # Cache hot-path bus callable references
        self._bus_read = bus.read
        self._bus_write = bus.write
        self._peek_palette = bus.peek_palette
        self._palette_cache = palette_cache  # bytearray(32) for inline peek

        self.control = 0
        self.mask = 0
        self.status = 0
        self.oam_address = 0
        self.v = 0
        self.t = 0
        self.fine_x = 0
        self.write_toggle = False
        self.read_buffer = 0
        self.scanline = 0
        self.dot = 0
        self.frame = 0
        self.odd_frame = False
        self.framebuffer = bytearray(256 * 240)
        self.oam = bytearray(256)
        self._nmi_prev = False
        self._bg_shift_lo = 0
        self._bg_shift_hi = 0
        self._bg_attr_lo = 0
        self._bg_attr_hi = 0
        self._nt_latch = 0
        self._at_latch = 0
        self._pt_lo_latch = 0
        self._pt_hi_latch = 0
        self._rendering = False
        self._bg_enabled = False
        self._secondary_oam = bytearray(32)
        self._sprite_count = 0
        self._sprite_zero_possible = False
        self._last_bg_pixel = 0

    cpdef void reset(self):
        self.control = 0
        self.mask = 0
        self.status = 0
        self.oam_address = 0
        self.v = 0
        self.t = 0
        self.fine_x = 0
        self.write_toggle = False
        self.read_buffer = 0
        self.scanline = 0
        self.dot = 0
        self.frame = 0
        self.odd_frame = False
        self._nmi_prev = False
        self._bg_shift_lo = 0
        self._bg_shift_hi = 0
        self._bg_attr_lo = 0
        self._bg_attr_hi = 0
        self._nt_latch = 0
        self._at_latch = 0
        self._pt_lo_latch = 0
        self._pt_hi_latch = 0
        self._rendering = False
        self._bg_enabled = False
        self._secondary_oam = bytearray(32)
        self._sprite_count = 0
        self._sprite_zero_possible = False
        self._last_bg_pixel = 0

    # ==================================================================
    # Register read/write
    # ==================================================================

    cpdef int read_register(self, int address):
        cdef int reg = address & 7
        cdef int result
        if reg == 2:  # PPUSTATUS
            result = self.status & 0xE0
            self.status &= 0x7F
            self.write_toggle = False
            self._update_nmi()
            return result
        elif reg == 4:  # OAMDATA
            return self.oam[self.oam_address]
        elif reg == 7:  # PPUDATA
            return self._read_ppudata()
        return 0

    cpdef void write_register(self, int address, int value):
        cdef int reg = address & 7
        value &= 0xFF
        if reg == 0:
            self._write_ppuctrl(value)
        elif reg == 1:
            self.mask = value & 0xFF
            self._update_rendering_flags()
        elif reg == 3:
            self.oam_address = value & 0xFF
        elif reg == 4:
            self.oam[self.oam_address] = value & 0xFF
            self.oam_address = (self.oam_address + 1) & 0xFF
        elif reg == 5:
            self._write_ppuscroll(value)
        elif reg == 6:
            self._write_ppuaddr(value)
        elif reg == 7:
            self._write_ppudata(value)

    # ==================================================================
    # Clock / advance_dots
    # ==================================================================

    cpdef void advance_dots(self, int n):
        cdef int i
        for i in range(n):
            self._clock_one_dot()

    cpdef void clock(self):
        self._clock_one_dot()

    cdef void _clock_one_dot(self):
        """Inlined dot logic — uses cached bus callables."""
        cdef int dot = self.dot
        cdef int scanline = self.scanline

        # Step 1: visible framebuffer output
        if scanline <= 239 and 1 <= dot <= 256:
            if self._rendering and self._bg_enabled:
                self._output_background_pixel(dot - 1)
            else:
                self._output_backdrop_pixel(dot - 1)
            # Phase 5: overlay sprites
            if self._rendering and (self.mask & 0x10) and self._sprite_count:
                self._composite_sprite_pixel(dot - 1)

        # Step 2: background pipeline
        if self._rendering:
            if scanline <= 239 or scanline == 261:
                self._tick_background()

        # Step 3: odd frame skip
        if (scanline == 261 and dot == 339
                and self.odd_frame and self._rendering):
            self.dot = 0
            self.scanline = 0
            self.frame += 1
            self.odd_frame = not self.odd_frame
            return

        # Step 4: advance counters
        self.dot = dot + 1
        if (dot + 1) >= 341:
            self.dot = 0
            self.scanline = scanline + 1
            if (scanline + 1) >= 262:
                self.scanline = 0
                self.frame += 1
                self.odd_frame = not self.odd_frame

        # Step 5: VBlank boundary (re-read scanline after possible change)
        if self.scanline == 241 and self.dot == 1:
            self.status |= 0x80
            self._update_nmi()
        elif self.scanline == 261 and self.dot == 1:
            self.status &= 0x1F
            self._update_nmi()

    # ==================================================================
    # NMI generation
    # ==================================================================

    cdef bint _nmi_output(self):
        return (self.control & 0x80) != 0 and (self.status & 0x80) != 0

    cdef void _update_nmi(self):
        cdef bint current = self._nmi_output()
        if current and not self._nmi_prev:
            self.interrupts.nmi_pending = True
        self._nmi_prev = current

    # ==================================================================
    # Register write helpers
    # ==================================================================

    cdef void _write_ppuctrl(self, int value):
        self.control = value & 0xFF
        self.t = (self.t & 0xF3FF) | ((value & 0x03) << 10)
        self._update_nmi()

    cdef void _write_ppuscroll(self, int value):
        if not self.write_toggle:
            self.t = (self.t & 0x7FE0) | ((value >> 3) & 0x1F)
            self.fine_x = value & 0x07
        else:
            self.t = (self.t & 0x0C1F) | ((value & 0x07) << 12)
            self.t = (self.t & 0x7C1F) | ((value & 0xF8) << 2)
        self.write_toggle = not self.write_toggle

    cdef void _write_ppuaddr(self, int value):
        if not self.write_toggle:
            self.t = (self.t & 0x00FF) | ((value & 0x3F) << 8)
        else:
            self.t = (self.t & 0x7F00) | value
            self.v = self.t
        self.write_toggle = not self.write_toggle

    # ==================================================================
    # PPUDATA
    # ==================================================================

    cdef int _increment(self):
        return 32 if (self.control & 0x04) else 1

    cdef int _read_ppudata(self):
        cdef int addr = self.v & 0x3FFF
        cdef int result
        self.v = (self.v + self._increment()) & 0x7FFF
        if addr < 0x3F00:
            result = self.read_buffer
            self.read_buffer = self._bus_read(addr)
            return result
        else:
            result = self._bus_read(addr)
            self.read_buffer = self._bus_read(addr - 0x1000)
            return result

    cdef void _write_ppudata(self, int value):
        cdef int addr = self.v & 0x3FFF
        self._bus_write(addr, value)
        self.v = (self.v + self._increment()) & 0x7FFF

    # ==================================================================
    # Phase 4: Rendering control
    # ==================================================================

    cdef void _update_rendering_flags(self):
        self._rendering = (self.mask & 0x18) != 0
        self._bg_enabled = (self.mask & 0x08) != 0

    # ==================================================================
    # Phase 4: Background pipeline
    # ==================================================================

    cdef void _tick_background(self):
        cdef bint active = (1 <= self.dot <= 256) or (321 <= self.dot <= 336)
        if active:
            self._shift_registers()
            self._fetch_and_shift()
        if self.dot == 256:
            self._increment_y()
        if self.dot == 257:
            self._evaluate_sprites()
            self._reload_horizontal()
        if self.scanline == 261 and 280 <= self.dot <= 304:
            self._reload_vertical()

    # ==================================================================
    # Phase 4: Shift registers
    # ==================================================================

    cdef void _shift_registers(self):
        self._bg_shift_lo = (self._bg_shift_lo << 1) & 0xFFFF
        self._bg_shift_hi = (self._bg_shift_hi << 1) & 0xFFFF
        self._bg_attr_lo = (self._bg_attr_lo << 1) & 0xFFFF
        self._bg_attr_hi = (self._bg_attr_hi << 1) & 0xFFFF

    # ==================================================================
    # Phase 4: Tile fetch pipeline
    # ==================================================================

    cdef void _fetch_and_shift(self):
        cdef int phase = self.dot & 7
        if phase == 0:
            self._load_shift_registers()
            self._increment_x()
        elif phase == 1:
            self._nt_latch = self._bus_read(self._nt_address())
        elif phase == 3:
            self._at_latch = self._bus_read(self._at_address())
        elif phase == 5:
            self._pt_lo_latch = self._bus_read(self._pt_lo_address())
        elif phase == 7:
            self._pt_hi_latch = self._bus_read(self._pt_hi_address())

    # ==================================================================
    # Phase 4: Tile fetch addresses
    # ==================================================================

    cdef int _nt_address(self):
        return 0x2000 | (self.v & 0x0FFF)

    cdef int _at_address(self):
        return (0x23C0 | (self.v & 0x0C00)
                | ((self.v >> 4) & 0x38) | ((self.v >> 2) & 0x07))

    cdef int _pt_lo_address(self):
        cdef int base = 0x1000 if (self.control & 0x10) else 0x0000
        cdef int fine_y = (self.v >> 12) & 0x07
        return base | (self._nt_latch << 4) | fine_y

    cdef int _pt_hi_address(self):
        return self._pt_lo_address() | 8

    cdef void _load_shift_registers(self):
        self._bg_shift_lo = (self._bg_shift_lo & 0xFF00) | self._pt_lo_latch
        self._bg_shift_hi = (self._bg_shift_hi & 0xFF00) | self._pt_hi_latch
        cdef int coarse_x = self.v & 0x1F
        cdef int coarse_y = (self.v >> 5) & 0x1F
        cdef int shift = (coarse_x & 2) | ((coarse_y & 2) << 1)
        cdef int pal = (self._at_latch >> shift) & 3
        cdef int attr_lo = 0xFF if (pal & 1) else 0x00
        cdef int attr_hi = 0xFF if (pal & 2) else 0x00
        self._bg_attr_lo = (self._bg_attr_lo & 0xFF00) | attr_lo
        self._bg_attr_hi = (self._bg_attr_hi & 0xFF00) | attr_hi

    # ==================================================================
    # Phase 4/5: Fast inline palette peek
    # ==================================================================

    cdef int _inline_peek(self, int index):
        """C-level palette bytearray lookup, bypassing Python bound method."""
        cdef int idx
        if self._palette_cache is not None:
            idx = index & 0x1F
            if idx >= 0x10 and (idx & 3) == 0:
                idx &= 0x0F
            return self._palette_cache[idx] & 0x3F
        return self._peek_palette(index)

    # ==================================================================
    # Phase 4/5: Pixel output (using inline palette peek)
    # ==================================================================

    cdef void _output_background_pixel(self, int fb_x):
        cdef int mux = 0x8000 >> self.fine_x
        cdef int pixel = (
            ((1 if (self._bg_shift_hi & mux) else 0) << 1)
            | (1 if (self._bg_shift_lo & mux) else 0)
        )
        cdef int attr = (
            ((1 if (self._bg_attr_hi & mux) else 0) << 1)
            | (1 if (self._bg_attr_lo & mux) else 0)
        )
        cdef bint bg_left_on = fb_x >= 8 or (self.mask & 0x02)
        cdef int palette_idx
        if not bg_left_on or pixel == 0:
            palette_idx = self._inline_peek(0)
            self._last_bg_pixel = 0
        else:
            palette_idx = self._inline_peek((attr << 2) | pixel)
            self._last_bg_pixel = pixel
        self.framebuffer[self.scanline * 256 + fb_x] = palette_idx

    cdef void _output_backdrop_pixel(self, int fb_x):
        self.framebuffer[self.scanline * 256 + fb_x] = (
            self._inline_peek(0)
        )
        self._last_bg_pixel = 0

    # ==================================================================
    # Phase 4: Scroll updates
    # ==================================================================

    cdef void _increment_x(self):
        if (self.v & 0x001F) == 31:
            self.v &= ~0x001F
            self.v ^= 0x0400
        else:
            self.v += 1

    cdef void _increment_y(self):
        cdef int fine_y = (self.v >> 12) & 7
        cdef int coarse_y
        if fine_y < 7:
            self.v += 0x1000
        else:
            self.v &= ~0x7000
            coarse_y = (self.v >> 5) & 0x1F
            if coarse_y == 29:
                self.v &= ~(0x1F << 5)
                self.v ^= 0x0800
            elif coarse_y == 31:
                self.v &= ~(0x1F << 5)
            else:
                self.v += 0x0020

    cdef void _reload_horizontal(self):
        self.v = (self.v & ~0x041F) | (self.t & 0x041F)

    cdef void _reload_vertical(self):
        self.v = (self.v & ~0x7BE0) | (self.t & 0x7BE0)

    # ==================================================================
    # Phase 5: Sprite evaluation (dot 257)
    # ==================================================================

    cdef void _evaluate_sprites(self):
        cdef int next_sl = self.scanline + 1 if self.scanline < 261 else 0
        cdef int n, sprite_y, row, idx
        cdef int height
        if next_sl >= 240:
            self._sprite_count = 0
            self._sprite_zero_possible = False
            return

        self._sprite_count = 0
        self._sprite_zero_possible = False

        height = 16 if (self.control & 0x20) else 8
        for n in range(64):
            sprite_y = self.oam[n * 4]
            row = next_sl - (sprite_y + 1)
            if 0 <= row < height:
                if n == 0:
                    self._sprite_zero_possible = True
                if self._sprite_count < 8:
                    idx = self._sprite_count * 4
                    self._secondary_oam[idx:idx + 4] = self.oam[n * 4:n * 4 + 4]
                    self._sprite_count += 1
                else:
                    self.status |= 0x20  # sprite overflow
                    break

    # ==================================================================
    # Phase 5: Sprite pixel fetch
    # ==================================================================

    cdef int _fetch_sprite_pixel(self, int scanline, int sprite_y,
                                  int tile_idx, int attr, int column):
        cdef int height = 16 if (self.control & 0x20) else 8
        cdef int row = scanline - (sprite_y + 1)
        cdef int table, tile, addr, pt_lo, pt_hi, bit
        if row < 0 or row >= height:
            return 0
        if attr & 0x80:  # vertical flip
            row = (height - 1) - row
        if height == 16:
            table = 0x1000 if (tile_idx & 1) else 0x0000
            if row < 8:
                tile = tile_idx & 0xFE
            else:
                tile = (tile_idx & 0xFE) | 1
                row -= 8
        else:
            table = 0x1000 if (self.control & 0x08) else 0x0000
            tile = tile_idx
        addr = table | (tile << 4) | row
        pt_lo = self._bus_read(addr)
        pt_hi = self._bus_read(addr | 8)
        bit = 7 - column
        return ((pt_hi >> bit) & 1) << 1 | ((pt_lo >> bit) & 1)

    # ==================================================================
    # Phase 5: Sprite compositing
    # ==================================================================

    cdef void _composite_sprite_pixel(self, int fb_x):
        cdef int n, base, sprite_y, tile_idx, attr_v, sprite_x
        cdef int offset, column, pixel
        cdef bint bg_opaque, behind_bg, left_ok
        cdef int palette_idx

        if fb_x < 8 and not (self.mask & 0x04):
            return

        for n in range(self._sprite_count):
            base = n * 4
            sprite_y = self._secondary_oam[base]
            tile_idx = self._secondary_oam[base + 1]
            attr_v = self._secondary_oam[base + 2]
            sprite_x = self._secondary_oam[base + 3]

            offset = fb_x - sprite_x
            if offset < 0 or offset >= 8:
                continue

            column = offset
            if attr_v & 0x40:  # horizontal flip
                column = 7 - offset

            pixel = self._fetch_sprite_pixel(
                self.scanline, sprite_y, tile_idx, attr_v, column
            )
            if pixel == 0:
                continue

            bg_opaque = self._last_bg_pixel != 0
            behind_bg = (attr_v & 0x20) != 0

            # Sprite 0 hit — BEFORE priority return
            if n == 0 and self._sprite_zero_possible:
                if pixel != 0 and bg_opaque:
                    if fb_x != 255:
                        left_ok = fb_x >= 8 or (
                            (self.mask & 0x02) and (self.mask & 0x04)
                        )
                        if left_ok:
                            self.status |= 0x40

            if behind_bg and bg_opaque:
                return

            palette_idx = self._inline_peek(
                0x10 | ((attr_v & 3) << 2) | pixel
            )
            self.framebuffer[self.scanline * 256 + fb_x] = palette_idx
            return
