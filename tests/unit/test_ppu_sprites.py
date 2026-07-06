"""Unit tests for PPU sprite rendering (Phase 5)."""

from simplenes.ppu.ppu import PPU
from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import Mirroring
from simplenes.interrupts import InterruptLines


class _FakeMapper:
    """Mapper with CHR RAM backing for pattern table tests."""
    mirroring = Mirroring.HORIZONTAL

    def __init__(self):
        self._chr = bytearray(8192)

    def observe_ppu_address(self, a):
        pass

    def ppu_read(self, a):
        return self._chr[a & 0x1FFF]

    def ppu_write(self, a, v):
        self._chr[a & 0x1FFF] = v & 0xFF


def _make_ppu():
    mapper = _FakeMapper()
    bus = PPUBus(mapper)
    interrupts = InterruptLines()
    ppu = PPU(bus=bus, interrupts=interrupts)
    ppu.write_register(0x2001, 0x00)
    ppu._update_rendering_flags()
    return ppu


# ======================================================================
# Background opacity tracking
# ======================================================================

def test_bg_pixel_opacity_tracking():
    """_last_bg_pixel records actual opacity (including left clipping)."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x0E)  # bg on, LEFT CLIPPING enabled (bit 1=1), show sprites
    ppu._update_rendering_flags()
    ppu.fine_x = 15  # mux = 0x0001
    ppu.scanline = 0
    ppu._bg_shift_lo = 0x0001  # pixel = 1
    ppu._bg_shift_hi = 0x0000
    ppu._bg_attr_lo = 0x0000
    ppu._bg_attr_hi = 0x0000

    ppu.bus.write(0x3F00, 0x0F)
    ppu.bus.write(0x3F01, 0x11)

    ppu._output_background_pixel(10)  # x >= 8, visible
    assert ppu._last_bg_pixel == 1

    ppu._output_background_pixel(3)   # x < 8, left clipping ON → visible
    assert ppu._last_bg_pixel == 1


def test_bg_left_clipping_sets_last_bg_pixel_zero():
    """Left 8px bg clipping OFF: _last_bg_pixel = 0 even if raw pixel != 0."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x08)  # bg on, LEFT CLIPPING OFF (bit 1=0)
    ppu._update_rendering_flags()
    ppu.fine_x = 15
    ppu._bg_shift_lo = 0x0001
    ppu._bg_shift_hi = 0x0000
    ppu._bg_attr_lo = 0x0000
    ppu._bg_attr_hi = 0x0000

    ppu.bus.write(0x3F00, 0x2A)

    ppu._output_background_pixel(3)
    assert ppu._last_bg_pixel == 0


def test_sprite_behind_bg_visible_when_bg_left_clipped():
    """Behind-bg sprite visible when bg left-clipped."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1C)  # bg on, sprites on, bg left OFF (bit1=0), sprite left ON
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 0
    ppu.bus.write(0x3F00, 0x0F)
    ppu.bus.write(0x3F13, 0x33)

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0x20  # behind bg
    ppu._secondary_oam[3] = 3
    ppu._sprite_count = 1

    ppu.bus.write(0x0004, 0x80)  # pt_lo row 4
    ppu.bus.write(0x000C, 0x80)  # pt_hi row 4

    ppu._composite_sprite_pixel(3)
    assert ppu.framebuffer[5 * 256 + 3] == 0x33


# ======================================================================
# Sprite Y off-by-one
# ======================================================================

def test_sprite_y_off_by_one():
    """Sprite Y=0 starts at scanline 1 (OAM Y = top - 1)."""
    ppu = _make_ppu()
    ppu.control = 0
    ppu.bus.write(0x0000, 0x80)
    ppu.bus.write(0x0008, 0x00)
    # scanline 1 → row = 1 - (0+1) = 0 → visible
    pixel = ppu._fetch_sprite_pixel(1, 0, 0, 0, 0)
    assert pixel != 0

    # scanline 0 → row = 0 - 1 = -1 → not visible
    pixel = ppu._fetch_sprite_pixel(0, 0, 0, 0, 0)
    assert pixel == 0


# ======================================================================
# Sprite evaluation
# ======================================================================

def test_sprite_evaluation_8x8():
    """Sprite evaluation fills secondary OAM for next scanline."""
    ppu = _make_ppu()
    ppu.control = 0
    ppu.write_register(0x2001, 0x18)
    ppu._update_rendering_flags()

    # Initialize all OAM entries off-screen to avoid false positives
    for i in range(0, 256, 4):
        ppu.oam[i] = 0xFF
    ppu.oam[0] = 10
    ppu.oam[1] = 0x42
    ppu.oam[2] = 0
    ppu.oam[3] = 50

    ppu.scanline = 0
    ppu._evaluate_sprites()
    assert ppu._sprite_count == 0

    ppu.scanline = 10
    ppu._evaluate_sprites()
    assert ppu._sprite_count == 1
    assert ppu._secondary_oam[0] == 10
    assert ppu._secondary_oam[1] == 0x42


def test_sprite_evaluation_8x16():
    """8x16 mode: Y=10 visible on scanlines 11-26."""
    ppu = _make_ppu()
    ppu.control = 0x20
    ppu.write_register(0x2001, 0x18)
    ppu._update_rendering_flags()
    ppu.scanline = 25

    for i in range(0, 256, 4):
        ppu.oam[i] = 0xFF
    ppu.oam[0] = 10
    ppu.oam[1] = 1
    ppu.oam[2] = 0
    ppu.oam[3] = 0

    ppu._evaluate_sprites()
    assert ppu._sprite_count == 1


def test_sprite_overflow():
    """More than 8 sprites → status bit 5."""
    ppu = _make_ppu()
    ppu.control = 0
    ppu.write_register(0x2001, 0x18)
    ppu._update_rendering_flags()
    ppu.scanline = 20

    for n in range(10):
        ppu.oam[n * 4] = 20
    ppu._evaluate_sprites()
    assert ppu._sprite_count == 8
    assert ppu.status & 0x20


# ======================================================================
# Sprite pixel fetch
# ======================================================================

def test_sprite_pixel_fetch_8x8():
    """_fetch_sprite_pixel returns correct pixel."""
    ppu = _make_ppu()
    ppu.control = 0
    ppu.bus.write(0x0000, 0x80)
    ppu.bus.write(0x0008, 0x80)
    pixel = ppu._fetch_sprite_pixel(11, 10, 0, 0, 0)
    assert pixel == 3


def test_sprite_pixel_fetch_8x16():
    """8x16: tile_idx bit 0 selects table."""
    ppu = _make_ppu()
    ppu.control = 0x20

    # Top tile (tile 0, table $0000) row 0
    ppu.bus.write(0x0000, 0x80)  # top tile, row 0, pt_lo
    ppu.bus.write(0x0008, 0x00)
    pixel = ppu._fetch_sprite_pixel(11, 10, 0, 0, 0)
    assert pixel == 1

    # Bottom tile (tile 1, table $0000) row 0
    ppu.bus.write(0x0010, 0x80)  # tile 1, bottom, row 0, pt_lo
    ppu.bus.write(0x0018, 0x80)  # pt_hi bit 7 set
    pixel = ppu._fetch_sprite_pixel(19, 10, 0, 0, 0)
    assert pixel == 3  # (1<<1)|1


def test_sprite_horizontal_flip():
    """attr bit 6 flips column."""
    ppu = _make_ppu()
    ppu.control = 0

    ppu.bus.write(0x0000, 0x80)  # bit 7 = 1
    ppu.bus.write(0x0008, 0x00)
    pixel = ppu._fetch_sprite_pixel(11, 10, 0, 0, 0)
    assert pixel == 1
    # Simulate horizontal flip: compositor passes column=7 for original column 0
    pixel = ppu._fetch_sprite_pixel(11, 10, 0, 0, 7)
    assert pixel == 0


def test_sprite_vertical_flip():
    """attr bit 7 flips rows."""
    ppu = _make_ppu()
    ppu.control = 0

    ppu.bus.write(0x0000, 0x80)  # row 0
    ppu.bus.write(0x0007, 0x80)  # row 7, same layout
    ppu.bus.write(0x0008, 0x00)
    ppu.bus.write(0x000F, 0x00)

    pixel = ppu._fetch_sprite_pixel(11, 10, 0, 0, 0)
    assert pixel == 1
    pixel = ppu._fetch_sprite_pixel(11, 10, 0, 0x80, 0)
    assert pixel == 1  # reads row 7, bit 0 set


# ======================================================================
# Sprite compositing
# ======================================================================

def test_sprite_composite():
    """Sprite pixel overwrites framebuffer."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1

    ppu.bus.write(0x3F00, 0x0F)
    ppu.bus.write(0x3F11, 0x22)

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0
    ppu._secondary_oam[3] = 10
    ppu._sprite_count = 1

    ppu.bus.write(0x0004, 0x80)
    ppu.bus.write(0x000C, 0x00)

    ppu._composite_sprite_pixel(10)
    assert ppu.framebuffer[5 * 256 + 10] == 0x22


def test_sprite_behind_bg_blocks_lower_sprites():
    """High-priority behind-bg sprite blocks lower-priority sprites."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1

    ppu.bus.write(0x3F00, 0x0F)
    ppu.bus.write(0x3F11, 0x22)
    ppu.bus.write(0x3F12, 0x33)

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0x20  # behind bg
    ppu._secondary_oam[3] = 10
    ppu._secondary_oam[4] = 0
    ppu._secondary_oam[5] = 0
    ppu._secondary_oam[6] = 0
    ppu._secondary_oam[7] = 10
    ppu._sprite_count = 2

    ppu.bus.write(0x0004, 0x80)
    ppu.bus.write(0x000C, 0x00)

    ppu._composite_sprite_pixel(10)
    assert ppu.framebuffer[5 * 256 + 10] != 0x22
    assert ppu.framebuffer[5 * 256 + 10] != 0x33


# ======================================================================
# Sprite 0 hit
# ======================================================================

def test_sprite_zero_hit():
    """Sprite 0 + opaque bg → status bit 6."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1
    ppu._sprite_zero_possible = True

    ppu.bus.write(0x3F00, 0x0F)
    ppu.bus.write(0x3F11, 0x22)

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0
    ppu._secondary_oam[3] = 20
    ppu._sprite_count = 1

    ppu.bus.write(0x0004, 0x80)
    ppu.bus.write(0x000C, 0x00)

    ppu._composite_sprite_pixel(20)
    assert ppu.status & 0x40


def test_sprite_zero_hit_left_clipping():
    """Left 8px: both bg/sprite left clipping OFF → no hit."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x18)  # bg+sprites on, both left clipping OFF
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1
    ppu._sprite_zero_possible = True

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0
    ppu._secondary_oam[3] = 3
    ppu._sprite_count = 1

    ppu.bus.write(0x0000, 0x80)
    ppu.bus.write(0x0008, 0x00)

    ppu.bus.write(0x0004, 0x80)
    ppu.bus.write(0x000C, 0x00)
    ppu._composite_sprite_pixel(3)
    assert not (ppu.status & 0x40)


def test_sprite_zero_hit_x255():
    """No hit at x=255."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1
    ppu._sprite_zero_possible = True

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0
    ppu._secondary_oam[3] = 255
    ppu._sprite_count = 1

    ppu.bus.write(0x0000, 0x80)
    ppu.bus.write(0x0008, 0x00)

    ppu._composite_sprite_pixel(255)
    assert not (ppu.status & 0x40)


def test_sprite_zero_hit_later_pixel():
    """Hit can trigger on later pixel in same scanline."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 0
    ppu._sprite_zero_possible = True

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0
    ppu._secondary_oam[3] = 10
    ppu._sprite_count = 1

    ppu.bus.write(0x0004, 0xC0)  # bits 7+6 set → visible at columns 0+1
    ppu.bus.write(0x000C, 0x00)
    ppu._composite_sprite_pixel(10)
    assert not (ppu.status & 0x40)

    ppu._last_bg_pixel = 1
    ppu._composite_sprite_pixel(11)
    assert ppu.status & 0x40


def test_sprite_zero_hit_when_sprite_behind_background():
    """Behind-bg sprite 0 still triggers hit (checked before priority)."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1
    ppu._sprite_zero_possible = True

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0x20  # behind bg
    ppu._secondary_oam[3] = 20
    ppu._sprite_count = 1

    ppu.bus.write(0x0004, 0x80)
    ppu.bus.write(0x000C, 0x00)

    ppu._composite_sprite_pixel(20)
    assert ppu.status & 0x40


# ======================================================================
# Sprite left clipping
# ======================================================================

def test_sprite_left_clipping():
    """mask bit 2==0 → sprites invisible in left 8px."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x18)  # sprites on, sprite left clipping OFF
    ppu._update_rendering_flags()
    ppu.scanline = 5
    ppu._last_bg_pixel = 1

    ppu.bus.write(0x3F00, 0x0F)
    ppu.bus.write(0x3F11, 0x22)

    ppu._secondary_oam[0] = 0
    ppu._secondary_oam[1] = 0
    ppu._secondary_oam[2] = 0
    ppu._secondary_oam[3] = 3
    ppu._sprite_count = 1

    ppu.bus.write(0x0000, 0x80)
    ppu.bus.write(0x0008, 0x00)

    ppu._composite_sprite_pixel(3)
    assert ppu.framebuffer[5 * 256 + 3] == 0
