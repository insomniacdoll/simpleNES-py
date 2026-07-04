"""Unit tests for PPU background rendering pipeline (Phase 4)."""

from simplenes.ppu.ppu import PPU
from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import Mirroring
from simplenes.interrupts import InterruptLines


class _FakeMapper:
    mirroring = Mirroring.HORIZONTAL

    def observe_ppu_address(self, address):
        pass

    def ppu_read(self, address):
        return 0

    def ppu_write(self, address, value):
        pass


def _make_ppu():
    """Create a PPU wired to a PPUBus for testing."""
    mapper = _FakeMapper()
    bus = PPUBus(mapper)
    interrupts = InterruptLines()
    ppu = PPU(bus=bus, interrupts=interrupts)
    # Default: rendering off
    ppu.mask = 0x00
    ppu._update_rendering_flags()
    return ppu


# ======================================================================
# Rendering control
# ======================================================================

def test_rendering_disabled_outputs_backdrop():
    """When mask & 0x18 == 0, visible dots output backdrop and v stays put."""
    ppu = _make_ppu()
    ppu.mask = 0x00  # all off
    ppu._update_rendering_flags()
    v_before = ppu.v

    # Set palette[0] to a known value so we can verify backdrop
    ppu.bus.write(0x3F00, 0x2A)
    backdrop = ppu.bus.read(0x3F00) & 0x3F

    # Clock through one visible dot (scanline 0, dot 1)
    ppu.scanline = 0
    ppu.dot = 1
    ppu.clock()

    assert ppu.framebuffer[0] == backdrop
    assert ppu.v == v_before  # v must NOT change


def test_rendering_disabled_no_v_increment():
    """_rendering=False → v register does not change after clock()."""
    ppu = _make_ppu()
    ppu.mask = 0x00
    ppu._update_rendering_flags()
    ppu.v = 0x1234
    v_before = ppu.v

    ppu.scanline = 0
    ppu.dot = 10
    ppu.clock()

    assert ppu.v == v_before


def test_bg_disabled_outputs_backdrop():
    """sprites enabled + bg disabled → visible dots output backdrop."""
    ppu = _make_ppu()
    ppu.mask = 0x10  # sprites on, bg off → _rendering=True, _bg_enabled=False
    ppu._update_rendering_flags()
    ppu.bus.write(0x3F00, 0x2A)
    backdrop = ppu.bus.read(0x3F00) & 0x3F

    ppu.scanline = 0
    ppu.dot = 10

    ppu.clock()

    assert ppu.framebuffer[9] == backdrop  # dot-1 = fb_x


def test_bg_disabled_v_still_increments():
    """sprites enabled + bg disabled → v increments at phase 0 dots."""
    ppu = _make_ppu()
    ppu.mask = 0x10  # sprites on, bg off → _rendering=True
    ppu._update_rendering_flags()
    ppu.v = 0x0000
    v_before = ppu.v

    ppu.scanline = 0
    ppu.dot = 8  # phase 0 → load + increment_x

    ppu.clock()   # _tick_background runs, phase 0 triggers _increment_x

    assert ppu.v != v_before  # v changed because pipeline runs
    assert ppu.v == 0x0001     # coarse_x incremented


# ======================================================================
# Coarse X
# ======================================================================

def test_coarse_x_increment():
    """_increment_x adds 1 to coarse_x when < 31."""
    ppu = _make_ppu()
    ppu.v = 0x0000  # coarse_x = 0
    ppu._increment_x()
    assert ppu.v == 0x0001  # coarse_x = 1


def test_coarse_x_wrap():
    """coarse_x 31 → 0, toggle horizontal nametable bit."""
    ppu = _make_ppu()
    ppu.v = 0x001F  # coarse_x = 31, other bits 0
    ppu._increment_x()
    # coarse_x → 0, horizontal nt (bit 10) toggles
    assert ppu.v == 0x0400  # bit 10 set, coarse_x = 0


# ======================================================================
# Y increment
# ======================================================================

def test_increment_y_fine():
    """fine_y 0→1: _increment_y adds 0x1000."""
    ppu = _make_ppu()
    ppu.v = 0x0000  # fine_y = 0
    ppu._increment_y()
    assert ppu.v == 0x1000  # fine_y = 1


def test_increment_y_coarse_wrap():
    """fine_y=7 + coarse_y=0 → fine_y=0, coarse_y=1."""
    ppu = _make_ppu()
    ppu.v = 0x7000  # fine_y = 7, coarse_y = 0
    ppu._increment_y()
    assert ppu.v == 0x0020  # fine_y = 0, coarse_y = 1


def test_increment_y_coarse_29_toggle():
    """fine_y=7 + coarse_y=29 → fine_y=0, coarse_y=0, toggle vertical nt."""
    ppu = _make_ppu()
    ppu.v = 0x73A0  # fine_y=7, coarse_y=29 (=0x1D<<5), others=0
    ppu._increment_y()
    expected = 0x0800  # fine_y=0, coarse_y=0, vertical nt (bit 11) toggled
    assert ppu.v == expected


# ======================================================================
# Reloads
# ======================================================================

def test_horizontal_reload():
    """_reload_horizontal copies coarse_x + horizontal nt from t to v."""
    ppu = _make_ppu()
    ppu.v = 0x7FFF  # all bits set in v
    ppu.t = 0x0015  # coarse_x=0x15, horizontal nt=0
    ppu._reload_horizontal()
    # Only bits 4-0 (coarse_x) and bit 10 (horizontal nt) from t are copied
    expected = (ppu.v & ~0x041F) | (ppu.t & 0x041F)
    assert ppu.v == expected
    assert (ppu.v & 0x001F) == 0x15   # coarse_x from t
    assert (ppu.v & 0x0400) == 0      # horizontal nt from t


def test_vertical_reload():
    """_reload_vertical copies fine_y + coarse_y + vertical nt from t."""
    ppu = _make_ppu()
    ppu.v = 0x0000
    ppu.t = 0x2000 | 0x0800 | 0x0020  # fine_y=2, vertical nt=1, coarse_y=1
    ppu._reload_vertical()
    # bits 14-12, 11, 9-5 from t
    assert (ppu.v >> 12) & 7 == 2      # fine_y = 2
    assert ppu.v & 0x0800 == 0x0800   # vertical nt = 1
    assert (ppu.v >> 5) & 0x1F == 1   # coarse_y = 1


# ======================================================================
# Tile fetch addresses
# ======================================================================

def test_tile_fetch_addresses():
    """Address calculations match known NT/AT/PT layout."""
    ppu = _make_ppu()
    # v: nametable 1 ($2400), coarse_y=3, coarse_x=10
    ppu.v = 0x046A  # nt=1<<10, coarse_y=3<<5=0x60, coarse_x=10=0x0A → 0x046A
    ppu._nt_latch = 0x42

    # NT address
    nt = ppu._nt_address()
    assert nt == 0x246A
    assert (nt & 0x3C00) == 0x2400  # nametable 1

    # AT address
    at_addr = ppu._at_address()
    # coarse_y>>2 = 0, coarse_x>>2 = 2 → byte offset = 0*8 + 2 = 2
    assert at_addr & 0x3FF == 0x3C0 + 2

    # PT address — control bit 4 = 0 → base $0000
    pt_lo = ppu._pt_lo_address()
    fine_y = (ppu.v >> 12) & 7  # 0
    assert pt_lo == (0x42 << 4) | fine_y  # $0420

    pt_hi = ppu._pt_hi_address()
    assert pt_hi == pt_lo | 8


# ======================================================================
# Shift register
# ======================================================================

def test_shifter_load_and_shift():
    """Load pattern into low byte, shift left N times, verify position."""
    ppu = _make_ppu()
    ppu._pt_lo_latch = 0xAA  # 10101010
    ppu._pt_hi_latch = 0x55  # 01010101

    ppu._load_shift_registers()

    # After load: data in bits 7-0
    assert ppu._bg_shift_lo == 0x00AA
    assert ppu._bg_shift_hi == 0x0055

    # Shift left 8 times
    for _ in range(8):
        ppu._shift_registers()

    # After 8 shifts: data in bits 15-8
    assert ppu._bg_shift_lo == 0xAA00
    assert ppu._bg_shift_hi == 0x5500


# ======================================================================
# Pixel output
# ======================================================================

def test_pixel_output_with_fine_x():
    """fine_x selects correct mux bit from shifters (valid range 0-7)."""
    ppu = _make_ppu()
    ppu.fine_x = 3   # mux = 0x8000 >> 3 = 0x1000
    ppu.scanline = 10
    ppu.mask = 0x0E  # bg on, no left clipping

    # Position data at bit 12 and 13 of shifters (mux = 0x1000 checks bit 12)
    # To hit bit 12, load data into low byte and shift left 4 times
    ppu._pt_lo_latch = 0x10  # bit 4 set → after 4 shifts → bit 8, but
    ppu._pt_hi_latch = 0x20  # bit 5 set

    # Actually, let's just set shifters directly:
    # mux = 0x1000, so check bits 12 of each shifter
    ppu._bg_shift_lo = 0x1000  # bit 12 = 1
    ppu._bg_shift_hi = 0x2000  # bit 13 = 1
    ppu._bg_attr_lo = 0x4000  # bit 14 = 1
    ppu._bg_attr_hi = 0x0000  # bit 15 = 0

    # Set palette entries
    ppu.bus.write(0x3F00, 0x0F)  # backdrop
    ppu.bus.write(0x3F01, 0x11)  # palette[1]
    ppu.bus.write(0x3F02, 0x22)  # palette[2]
    ppu.bus.write(0x3F03, 0x33)  # palette[3]

    # mux=0x1000 (bit 12):
    #   _bg_shift_lo & 0x1000 → non-zero → pixel bit 0 = 1
    #   _bg_shift_hi & 0x1000 → 0x2000 & 0x1000 = 0 → pixel bit 1 = 0 → pixel = 1
    #   _bg_attr_lo & 0x1000 → 0x4000 & 0x1000 = 0 → attr bit 0 = 0
    #   _bg_attr_hi & 0x1000 → 0 → attr bit 1 = 0 → attr = 0
    #   palette_idx = attr*4 + pixel = 1 → bus.read(0x3F01) = 0x11 & 0x3F = 0x11
    ppu._output_background_pixel(20)
    assert ppu.framebuffer[10 * 256 + 20] == 0x11


def test_left_clipping():
    """mask bit 1 == 0 → left 8 pixels use backdrop even with non-zero pixel."""
    ppu = _make_ppu()
    ppu.fine_x = 15  # mux = 0x0001
    ppu.mask = 0x08  # bg on, LEFT CLIPPING OFF (bit 1=0)
    ppu.scanline = 5

    # Put non-zero pixel in shifters so that we'd normally output it
    ppu._bg_shift_lo = 0x0001  # pixel > 0
    ppu._bg_shift_hi = 0x0000
    ppu._bg_attr_lo = 0x0000
    ppu._bg_attr_hi = 0x0000

    ppu.bus.write(0x3F00, 0x2A)
    backdrop = ppu.bus.read(0x3F00) & 0x3F

    ppu._output_background_pixel(3)  # fb_x = 3 < 8
    assert ppu.framebuffer[5 * 256 + 3] == backdrop


# ======================================================================
# Odd frame skip
# ======================================================================

def test_odd_frame_skip():
    """On odd frame, pre-render dot 339 jumps to visible scanline 0 dot 0."""
    ppu = _make_ppu()
    ppu.mask = 0x08  # rendering enabled
    ppu._update_rendering_flags()
    ppu.odd_frame = True
    ppu.scanline = 261
    ppu.dot = 339
    frame_before = ppu.frame

    ppu.clock()

    # After skip: dot=0, scanline=0, frame incremented, odd_frame toggled
    assert ppu.dot == 0
    assert ppu.scanline == 0
    assert ppu.frame == frame_before + 1
    assert ppu.odd_frame is False
