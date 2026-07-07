"""PPU (Ricoh 2C02). Phase 5: background + sprite rendering pipeline."""


class PPU:
    """PPU with register layer, per-dot bg + sprite rendering pipeline.

    Registers:
        PPUCTRL   ($2000 W) — NMI enable, name table, increment, sprite size/table
        PPUMASK   ($2001 W) — rendering mask
        PPUSTATUS ($2002 R) — VBlank, sprite 0 hit, overflow
        OAMADDR   ($2003 W) — OAM address pointer
        OAMDATA   ($2004 RW) — OAM data
        PPUSCROLL ($2005 W) — scroll via two writes
        PPUADDR   ($2006 W) — VRAM address
        PPUDATA   ($2007 RW) — VRAM data
    """

    __slots__ = (
        "bus", "interrupts",
        "control", "mask", "status",
        "oam_address",
        "v", "t", "fine_x", "write_toggle", "read_buffer",
        "scanline", "dot", "frame", "odd_frame",
        "framebuffer", "oam",
        "_nmi_prev",
        # Phase 4
        "_bg_shift_lo", "_bg_shift_hi",
        "_bg_attr_lo", "_bg_attr_hi",
        "_nt_latch", "_at_latch",
        "_pt_lo_latch", "_pt_hi_latch",
        "_rendering", "_bg_enabled",
        # Phase 5
        "_secondary_oam", "_sprite_count",
        "_sprite_zero_possible", "_last_bg_pixel",
    )

    def __init__(self, bus, interrupts, *, region=None, palette_cache=None):
        self.bus = bus
        self.interrupts = interrupts
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
        # Phase 4
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
        # Phase 5
        self._secondary_oam = bytearray(32)
        self._sprite_count = 0
        self._sprite_zero_possible = False
        self._last_bg_pixel = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
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

    def read_register(self, address: int) -> int:
        reg = address & 7
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

    def write_register(self, address: int, value: int) -> None:
        reg = address & 7
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

    def advance_dots(self, n: int) -> None:
        """Batch-advance n dots. Equivalent to calling clock() n times."""
        for _ in range(n):
            self.clock()

    def clock(self) -> None:
        """Advance one PPU dot.  Phase 5: bg + sprite rendering."""

        # Step 1: visible framebuffer output
        if self.scanline <= 239 and 1 <= self.dot <= 256:
            if self._rendering and self._bg_enabled:
                self._output_background_pixel(self.dot - 1)
            else:
                self._output_backdrop_pixel(self.dot - 1)
            # Phase 5: overlay sprites
            if self._rendering and (self.mask & 0x10) and self._sprite_count:
                self._composite_sprite_pixel(self.dot - 1)

        # Step 2: background pipeline
        if self._rendering:
            if self.scanline <= 239 or self.scanline == 261:
                self._tick_background()

        # Step 3: odd frame skip
        if (self.scanline == 261 and self.dot == 339
                and self.odd_frame and self._rendering):
            self.dot = 0
            self.scanline = 0
            self.frame += 1
            self.odd_frame = not self.odd_frame
            return

        # Step 4: advance counters
        self.dot += 1
        if self.dot >= 341:
            self.dot = 0
            self.scanline += 1
            if self.scanline >= 262:
                self.scanline = 0
                self.frame += 1
                self.odd_frame = not self.odd_frame

        # Step 5: VBlank boundary
        if self.scanline == 241 and self.dot == 1:
            self.status |= 0x80
            self._update_nmi()
        elif self.scanline == 261 and self.dot == 1:
            self.status &= 0x1F
            self._update_nmi()

    # ------------------------------------------------------------------
    # NMI generation
    # ------------------------------------------------------------------

    def _nmi_output(self) -> bool:
        return bool((self.control & 0x80) and (self.status & 0x80))

    def _update_nmi(self) -> None:
        current = self._nmi_output()
        if current and not self._nmi_prev:
            self.interrupts.nmi_pending = True
        self._nmi_prev = current

    # ------------------------------------------------------------------
    # Register write helpers
    # ------------------------------------------------------------------

    def _write_ppuctrl(self, value: int) -> None:
        self.control = value & 0xFF
        self.t = (self.t & 0xF3FF) | ((value & 0x03) << 10)
        self._update_nmi()

    def _write_ppuscroll(self, value: int) -> None:
        if not self.write_toggle:
            self.t = (self.t & 0x7FE0) | ((value >> 3) & 0x1F)
            self.fine_x = value & 0x07
        else:
            self.t = (self.t & 0x0C1F) | ((value & 0x07) << 12)
            self.t = (self.t & 0x7C1F) | ((value & 0xF8) << 2)
        self.write_toggle = not self.write_toggle

    def _write_ppuaddr(self, value: int) -> None:
        if not self.write_toggle:
            self.t = (self.t & 0x00FF) | ((value & 0x3F) << 8)
        else:
            self.t = (self.t & 0x7F00) | value
            self.v = self.t
        self.write_toggle = not self.write_toggle

    # ------------------------------------------------------------------
    # PPUDATA
    # ------------------------------------------------------------------

    def _increment(self) -> int:
        return 32 if (self.control & 0x04) else 1

    def _read_ppudata(self) -> int:
        addr = self.v & 0x3FFF
        self.v = (self.v + self._increment()) & 0x7FFF
        if addr < 0x3F00:
            result = self.read_buffer
            self.read_buffer = self.bus.read(addr)
            return result
        else:
            result = self.bus.read(addr)
            self.read_buffer = self.bus.read(addr - 0x1000)
            return result

    def _write_ppudata(self, value: int) -> None:
        addr = self.v & 0x3FFF
        self.bus.write(addr, value)
        self.v = (self.v + self._increment()) & 0x7FFF

    # ------------------------------------------------------------------
    # Phase 4: Rendering control
    # ------------------------------------------------------------------

    def _update_rendering_flags(self) -> None:
        self._rendering = (self.mask & 0x18) != 0
        self._bg_enabled = (self.mask & 0x08) != 0

    # ------------------------------------------------------------------
    # Phase 4: Background pipeline
    # ------------------------------------------------------------------

    def _tick_background(self) -> None:
        active = (1 <= self.dot <= 256) or (321 <= self.dot <= 336)
        if active:
            self._shift_registers()
            self._fetch_and_shift()
        if self.dot == 256:
            self._increment_y()
        if self.dot == 257:
            self._evaluate_sprites()     # Phase 5: sprite eval for next line
            self._reload_horizontal()
        if self.scanline == 261 and 280 <= self.dot <= 304:
            self._reload_vertical()

    # ------------------------------------------------------------------
    # Phase 4: Shift registers
    # ------------------------------------------------------------------

    def _shift_registers(self) -> None:
        self._bg_shift_lo = (self._bg_shift_lo << 1) & 0xFFFF
        self._bg_shift_hi = (self._bg_shift_hi << 1) & 0xFFFF
        self._bg_attr_lo = (self._bg_attr_lo << 1) & 0xFFFF
        self._bg_attr_hi = (self._bg_attr_hi << 1) & 0xFFFF

    # ------------------------------------------------------------------
    # Phase 4: Tile fetch pipeline
    # ------------------------------------------------------------------

    def _fetch_and_shift(self) -> None:
        phase = self.dot & 7
        if phase == 0:
            self._load_shift_registers()
            self._increment_x()
        elif phase == 1:
            self._nt_latch = self.bus.read(self._nt_address())
        elif phase == 3:
            self._at_latch = self.bus.read(self._at_address())
        elif phase == 5:
            self._pt_lo_latch = self.bus.read(self._pt_lo_address())
        elif phase == 7:
            self._pt_hi_latch = self.bus.read(self._pt_hi_address())

    # ------------------------------------------------------------------
    # Phase 4: Tile fetch addresses
    # ------------------------------------------------------------------

    def _nt_address(self) -> int:
        return 0x2000 | (self.v & 0x0FFF)

    def _at_address(self) -> int:
        return (0x23C0 | (self.v & 0x0C00)
                | ((self.v >> 4) & 0x38) | ((self.v >> 2) & 0x07))

    def _pt_lo_address(self) -> int:
        base = 0x1000 if (self.control & 0x10) else 0x0000
        fine_y = (self.v >> 12) & 0x07
        return base | (self._nt_latch << 4) | fine_y

    def _pt_hi_address(self) -> int:
        return self._pt_lo_address() | 8

    def _load_shift_registers(self) -> None:
        self._bg_shift_lo = (self._bg_shift_lo & 0xFF00) | self._pt_lo_latch
        self._bg_shift_hi = (self._bg_shift_hi & 0xFF00) | self._pt_hi_latch
        coarse_x = self.v & 0x1F
        coarse_y = (self.v >> 5) & 0x1F
        shift = (coarse_x & 2) | ((coarse_y & 2) << 1)
        pal = (self._at_latch >> shift) & 3
        attr_lo = 0xFF if (pal & 1) else 0x00
        attr_hi = 0xFF if (pal & 2) else 0x00
        self._bg_attr_lo = (self._bg_attr_lo & 0xFF00) | attr_lo
        self._bg_attr_hi = (self._bg_attr_hi & 0xFF00) | attr_hi

    # ------------------------------------------------------------------
    # Phase 4/5: Pixel output
    # ------------------------------------------------------------------

    def _output_background_pixel(self, fb_x: int) -> None:
        mux = 0x8000 >> self.fine_x
        pixel = (
            ((1 if (self._bg_shift_hi & mux) else 0) << 1)
            | (1 if (self._bg_shift_lo & mux) else 0)
        )
        attr = (
            ((1 if (self._bg_attr_hi & mux) else 0) << 1)
            | (1 if (self._bg_attr_lo & mux) else 0)
        )

        bg_left_on = fb_x >= 8 or (self.mask & 0x02)
        if not bg_left_on or pixel == 0:
            palette_idx = self.bus.peek_palette(0)
            self._last_bg_pixel = 0
        else:
            palette_idx = self.bus.peek_palette((attr << 2) | pixel)
            self._last_bg_pixel = pixel

        self.framebuffer[self.scanline * 256 + fb_x] = palette_idx

    def _output_backdrop_pixel(self, fb_x: int) -> None:
        self.framebuffer[self.scanline * 256 + fb_x] = (
            self.bus.peek_palette(0)
        )
        self._last_bg_pixel = 0

    # ------------------------------------------------------------------
    # Phase 4: Scroll updates
    # ------------------------------------------------------------------

    def _increment_x(self) -> None:
        if (self.v & 0x001F) == 31:
            self.v &= ~0x001F
            self.v ^= 0x0400
        else:
            self.v += 1

    def _increment_y(self) -> None:
        fine_y = (self.v >> 12) & 7
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

    def _reload_horizontal(self) -> None:
        self.v = (self.v & ~0x041F) | (self.t & 0x041F)

    def _reload_vertical(self) -> None:
        self.v = (self.v & ~0x7BE0) | (self.t & 0x7BE0)

    # ------------------------------------------------------------------
    # Phase 5: Sprite evaluation (dot 257)
    # ------------------------------------------------------------------

    def _evaluate_sprites(self) -> None:
        next_sl = self.scanline + 1 if self.scanline < 261 else 0
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

    # ------------------------------------------------------------------
    # Phase 5: Sprite pixel fetch
    # ------------------------------------------------------------------

    def _fetch_sprite_pixel(self, scanline: int, sprite_y: int,
                            tile_idx: int, attr: int, column: int) -> int:
        height = 16 if (self.control & 0x20) else 8

        row = scanline - (sprite_y + 1)
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
        pt_lo = self.bus.read(addr)
        pt_hi = self.bus.read(addr | 8)

        bit = 7 - column
        return ((pt_hi >> bit) & 1) << 1 | ((pt_lo >> bit) & 1)

    # ------------------------------------------------------------------
    # Phase 5: Sprite compositing
    # ------------------------------------------------------------------

    def _composite_sprite_pixel(self, fb_x: int) -> None:
        if fb_x < 8 and not (self.mask & 0x04):
            return

        for n in range(self._sprite_count):
            base = n * 4
            sprite_y = self._secondary_oam[base]
            tile_idx = self._secondary_oam[base + 1]
            attr = self._secondary_oam[base + 2]
            sprite_x = self._secondary_oam[base + 3]

            offset = fb_x - sprite_x
            if offset < 0 or offset >= 8:
                continue

            column = offset
            if attr & 0x40:  # horizontal flip
                column = 7 - offset

            pixel = self._fetch_sprite_pixel(
                self.scanline, sprite_y, tile_idx, attr, column
            )
            if pixel == 0:
                continue

            bg_opaque = self._last_bg_pixel != 0
            behind_bg = bool(attr & 0x20)

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

            palette_idx = self.bus.peek_palette(
                0x10 | ((attr & 3) << 2) | pixel
            )
            self.framebuffer[self.scanline * 256 + fb_x] = palette_idx
            return
