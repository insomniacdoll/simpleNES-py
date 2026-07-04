"""PPU (Ricoh 2C02). Phase 4: background rendering pipeline."""


class PPU:
    """PPU with register layer + per-dot background rendering pipeline.

    Registers:
        PPUCTRL   ($2000 W) — NMI enable, name table, increment, sprite/table select
        PPUMASK   ($2001 W) — rendering mask
        PPUSTATUS ($2002 R) — VBlank, sprite 0 hit, overflow (read clears VBlank + toggle)
        OAMADDR   ($2003 W) — OAM address pointer
        OAMDATA   ($2004 RW) — OAM data
        PPUSCROLL ($2005 W) — scroll via two writes
        PPUADDR   ($2006 W) — VRAM address via two writes (second copies t→v)
        PPUDATA   ($2007 RW) — VRAM data with read buffer, auto-increment v
    """

    __slots__ = (
        "bus", "interrupts",
        "control", "mask", "status",
        "oam_address",
        "v", "t", "fine_x", "write_toggle", "read_buffer",
        "scanline", "dot", "frame", "odd_frame",
        "framebuffer", "oam",
        "_nmi_prev",
        # --- Phase 4: background rendering pipeline ---
        "_bg_shift_lo",
        "_bg_shift_hi",
        "_bg_attr_lo",
        "_bg_attr_hi",
        # Tile fetch latches
        "_nt_latch",
        "_at_latch",
        "_pt_lo_latch",
        "_pt_hi_latch",
        # Rendering control
        "_rendering",
        "_bg_enabled",
    )

    def __init__(self, bus, interrupts, *, region=None):
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
        # Phase 4 init
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset PPU to power-on state."""
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
        # Phase 4 reset
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

    def read_register(self, address: int) -> int:
        """Read PPU register (called by CPUBus at $2000-$2007)."""
        reg = address & 7
        if reg == 2:  # PPUSTATUS
            result = self.status & 0xE0
            self.status &= 0x7F           # clear VBlank
            self.write_toggle = False
            self._update_nmi()
            return result
        elif reg == 4:  # OAMDATA
            return self.oam[self.oam_address]
        elif reg == 7:  # PPUDATA
            return self._read_ppudata()
        return 0  # write-only registers return open bus (0 for now)

    def write_register(self, address: int, value: int) -> None:
        """Write PPU register (called by CPUBus at $2000-$2007)."""
        reg = address & 7
        value &= 0xFF
        if reg == 0:  # PPUCTRL
            self._write_ppuctrl(value)
        elif reg == 1:  # PPUMASK
            self.mask = value & 0xFF
            self._update_rendering_flags()
        elif reg == 3:  # OAMADDR
            self.oam_address = value & 0xFF
        elif reg == 4:  # OAMDATA
            self.oam[self.oam_address] = value & 0xFF
            self.oam_address = (self.oam_address + 1) & 0xFF
        elif reg == 5:  # PPUSCROLL
            self._write_ppuscroll(value)
        elif reg == 6:  # PPUADDR
            self._write_ppuaddr(value)
        elif reg == 7:  # PPUDATA
            self._write_ppudata(value)

    def clock(self) -> None:
        """Advance one PPU dot. Phase 4: includes background rendering pipeline."""
        # Step 0: update rendering flags
        self._update_rendering_flags()

        # Step 1: visible framebuffer output (always, even when rendering off)
        if self.scanline <= 239 and 1 <= self.dot <= 256:
            if self._rendering and self._bg_enabled:
                self._output_background_pixel(self.dot - 1)
            else:
                self._output_backdrop_pixel(self.dot - 1)

        # Step 2: background pipeline — shift + fetch + scroll updates
        if self._rendering:
            if self.scanline <= 239 or self.scanline == 261:
                self._tick_background()

        # Step 3: odd frame skip (jump from pre-render dot 339 to visible dot 0)
        if (self.scanline == 261 and self.dot == 339
                and self.odd_frame and self._rendering):
            self.dot = 0
            self.scanline = 0
            self.frame += 1
            self.odd_frame = not self.odd_frame
            return

        # Step 4: advance dot/scanline/frame counters
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
        """Internal NMI signal: true when VBlank and NMI-enable are both set."""
        return bool((self.control & 0x80) and (self.status & 0x80))

    def _update_nmi(self) -> None:
        """On rising edge of NMI output, set nmi_pending."""
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
        """VRAM address increment: 1 (across) or 32 (down)."""
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
        """Update _rendering / _bg_enabled from mask register."""
        self._rendering = (self.mask & 0x18) != 0  # bg (bit 3) or sprites (bit 4)
        self._bg_enabled = (self.mask & 0x08) != 0

    # ------------------------------------------------------------------
    # Phase 4: Background pipeline
    # ------------------------------------------------------------------

    def _tick_background(self) -> None:
        """Shift + fetch + scroll updates.  Pixel output is in clock() Step 1."""
        active = (1 <= self.dot <= 256) or (321 <= self.dot <= 336)

        if active:
            self._shift_registers()
            self._fetch_and_shift()

        if self.dot == 256:
            self._increment_y()
        if self.dot == 257:
            self._reload_horizontal()

        if self.scanline == 261 and 280 <= self.dot <= 304:
            self._reload_vertical()

    # ------------------------------------------------------------------
    # Phase 4: Shift registers
    # ------------------------------------------------------------------

    def _shift_registers(self) -> None:
        """Left-shift all four 16-bit background shifters by 1."""
        self._bg_shift_lo = (self._bg_shift_lo << 1) & 0xFFFF
        self._bg_shift_hi = (self._bg_shift_hi << 1) & 0xFFFF
        self._bg_attr_lo = (self._bg_attr_lo << 1) & 0xFFFF
        self._bg_attr_hi = (self._bg_attr_hi << 1) & 0xFFFF

    # ------------------------------------------------------------------
    # Phase 4: Tile fetch pipeline
    # ------------------------------------------------------------------

    def _fetch_and_shift(self) -> None:
        """8-dot tile fetch: NT→AT→PT_lo→PT_hi, load at phase 0."""
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
        # phases 2, 4, 6: idle

    # ------------------------------------------------------------------
    # Phase 4: Tile fetch addresses
    # ------------------------------------------------------------------

    def _nt_address(self) -> int:
        """Nametable address: $2000 | (v & 0x0FFF)."""
        return 0x2000 | (self.v & 0x0FFF)

    def _at_address(self) -> int:
        """Attribute table address within current nametable."""
        return (0x23C0 | (self.v & 0x0C00)
                | ((self.v >> 4) & 0x38) | ((self.v >> 2) & 0x07))

    def _pt_lo_address(self) -> int:
        """Pattern table low byte: base | (nt_latch << 4) | fine_y."""
        base = 0x1000 if (self.control & 0x10) else 0x0000
        fine_y = (self.v >> 12) & 0x07
        return base | (self._nt_latch << 4) | fine_y

    def _pt_hi_address(self) -> int:
        """Pattern table high byte = low + 8."""
        return self._pt_lo_address() | 8

    def _load_shift_registers(self) -> None:
        """Load latches into shifter low byte + attribute bits."""
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
    # Phase 4: Pixel output
    # ------------------------------------------------------------------

    def _output_background_pixel(self, fb_x: int) -> None:
        """Output one background pixel using fine_x-muxed shifters."""
        mux = 0x8000 >> self.fine_x

        pixel = (
            ((1 if (self._bg_shift_hi & mux) else 0) << 1)
            | (1 if (self._bg_shift_lo & mux) else 0)
        )
        attr = (
            ((1 if (self._bg_attr_hi & mux) else 0) << 1)
            | (1 if (self._bg_attr_lo & mux) else 0)
        )

        if fb_x < 8 and not (self.mask & 0x02):
            # Left 8-pixel clipping → backdrop
            palette_idx = self.bus.read(0x3F00) & 0x3F
        elif pixel == 0:
            palette_idx = self.bus.read(0x3F00) & 0x3F
        else:
            palette_idx = self.bus.read(0x3F00 | ((attr << 2) | pixel)) & 0x3F

        self.framebuffer[self.scanline * 256 + fb_x] = palette_idx

    def _output_backdrop_pixel(self, fb_x: int) -> None:
        """Backdrop color — used when bg disabled or rendering off."""
        self.framebuffer[self.scanline * 256 + fb_x] = (
            self.bus.read(0x3F00) & 0x3F
        )

    # ------------------------------------------------------------------
    # Phase 4: Scroll updates during rendering
    # ------------------------------------------------------------------

    def _increment_x(self) -> None:
        """Increment coarse X; on wrap toggle horizontal nametable."""
        if (self.v & 0x001F) == 31:
            self.v &= ~0x001F
            self.v ^= 0x0400
        else:
            self.v += 1

    def _increment_y(self) -> None:
        """Increment fine Y; on wrap increment coarse Y; handle nt toggle."""
        fine_y = (self.v >> 12) & 7
        if fine_y < 7:
            self.v += 0x1000
        else:
            self.v &= ~0x7000  # fine_y = 0
            coarse_y = (self.v >> 5) & 0x1F
            if coarse_y == 29:
                self.v &= ~(0x1F << 5)
                self.v ^= 0x0800
            elif coarse_y == 31:
                self.v &= ~(0x1F << 5)
            else:
                self.v += 0x0020

    def _reload_horizontal(self) -> None:
        """Copy horizontal bits (coarse_x + horizontal nt) from t to v."""
        self.v = (self.v & ~0x041F) | (self.t & 0x041F)

    def _reload_vertical(self) -> None:
        """Copy vertical bits (fine_y + coarse_y + vertical nt) from t to v."""
        self.v = (self.v & ~0x7BE0) | (self.t & 0x7BE0)
