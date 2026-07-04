"""PPU (Ricoh 2C02). Phase 3: full register behaviour + NMI generation."""


class PPU:
    """PPU register layer: all 8 PPU registers, v/t/fine_x/toggle, NMI edge detection.

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
        """Advance one PPU dot. Handles VBlank flag + NMI edge detection."""
        self.dot += 1
        if self.dot >= 341:
            self.dot = 0
            self.scanline += 1
            if self.scanline >= 262:
                self.scanline = 0
                self.frame += 1
                self.odd_frame = not self.odd_frame

        # VBlank boundary
        if self.scanline == 241 and self.dot == 1:
            self.status |= 0x80
            self._update_nmi()
        elif self.scanline == 261 and self.dot == 1:
            self.status &= 0x1F   # clear VBlank, sprite 0, sprite overflow
            self._update_nmi()

    # ------------------------------------------------------------------
    # NMI generation
    # ------------------------------------------------------------------

    def _nmi_output(self) -> bool:
        """Internal NMI signal: true when VBlank and NMI-enable are both set."""
        return bool((self.control & 0x80) and (self.status & 0x80))

    def _update_nmi(self) -> None:
        """On rising edge (0→1) of NMI output, set nmi_pending on the shared InterruptLines."""
        current = self._nmi_output()
        if current and not self._nmi_prev:
            self.interrupts.nmi_pending = True
        self._nmi_prev = current

    # ------------------------------------------------------------------
    # Register write helpers
    # ------------------------------------------------------------------

    def _write_ppuctrl(self, value: int) -> None:
        self.control = value & 0xFF
        # Update name table bits 1-0 into t bits 11-10
        self.t = (self.t & 0xF3FF) | ((value & 0x03) << 10)
        self._update_nmi()

    def _write_ppuscroll(self, value: int) -> None:
        if not self.write_toggle:
            # First write: horizontal scroll
            self.t = (self.t & 0x7FE0) | ((value >> 3) & 0x1F)  # coarse X
            self.fine_x = value & 0x07                            # fine X
        else:
            # Second write: vertical scroll
            self.t = (self.t & 0x0C1F) | ((value & 0x07) << 12)  # fine Y
            self.t = (self.t & 0x7C1F) | ((value & 0xF8) << 2)   # coarse Y
        self.write_toggle = not self.write_toggle

    def _write_ppuaddr(self, value: int) -> None:
        if not self.write_toggle:
            # First write: high byte → t bits 14-8
            self.t = (self.t & 0x00FF) | ((value & 0x3F) << 8)
        else:
            # Second write: low byte → t bits 7-0, then copy t → v
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
            # Palette area: return immediately, read_buffer filled from
            # nametable mirror beneath ($3Fxx → $2Fxx)
            result = self.bus.read(addr)
            self.read_buffer = self.bus.read(addr - 0x1000)
            return result

    def _write_ppudata(self, value: int) -> None:
        addr = self.v & 0x3FFF
        self.bus.write(addr, value)
        self.v = (self.v + self._increment()) & 0x7FFF
