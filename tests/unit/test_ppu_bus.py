"""Unit tests for PPUBus: nametable mirroring, palette mirroring, CHR passthrough."""


from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import Mirroring


class _FakeMapper:
    """Minimal mapper for PPUBus testing."""

    __slots__ = ("_mirroring", "_chr")

    def __init__(self, mirroring=Mirroring.HORIZONTAL, chr_data=None):
        self._mirroring = mirroring
        self._chr = bytearray(chr_data) if chr_data else bytearray(8192)

    @property
    def mirroring(self):
        return self._mirroring

    def observe_ppu_address(self, address):
        pass

    def ppu_read(self, address):
        return self._chr[address & 0x1FFF]

    def ppu_write(self, address, value):
        self._chr[address & 0x1FFF] = value & 0xFF


# ---------------------------------------------------------------------------
# Nametable mirroring
# ---------------------------------------------------------------------------

def test_nametable_horizontal_mirroring():
    """NT0 and NT1 map to physical NT0; NT2 and NT3 map to NT1."""
    mapper = _FakeMapper(mirroring=Mirroring.HORIZONTAL)
    bus = PPUBus(mapper)

    # NT0 ($2000) and NT1 ($2400) both go to physical NT0
    bus.write(0x2000, 0x11)
    bus.write(0x2400, 0x22)
    assert bus.read(0x2000) == 0x22
    assert bus.read(0x2400) == 0x22

    # NT2 ($2800) and NT3 ($2C00) both go to physical NT1
    bus.write(0x2800, 0x33)
    bus.write(0x2C00, 0x44)
    assert bus.read(0x2800) == 0x44
    assert bus.read(0x2C00) == 0x44


def test_nametable_vertical_mirroring():
    """NT0 and NT2 map to physical NT0; NT1 and NT3 map to NT1."""
    mapper = _FakeMapper(mirroring=Mirroring.VERTICAL)
    bus = PPUBus(mapper)

    bus.write(0x2000, 0xAA)
    bus.write(0x2800, 0xBB)
    assert bus.read(0x2000) == 0xBB
    assert bus.read(0x2800) == 0xBB

    bus.write(0x2400, 0xCC)
    bus.write(0x2C00, 0xDD)
    assert bus.read(0x2400) == 0xDD
    assert bus.read(0x2C00) == 0xDD


def test_nametable_single_screen_lower():
    """All 4 NT slots map to physical NT0."""
    mapper = _FakeMapper(mirroring=Mirroring.SINGLE_SCREEN_LOWER)
    bus = PPUBus(mapper)

    bus.write(0x2000, 0x55)
    assert bus.read(0x2000) == 0x55
    assert bus.read(0x2400) == 0x55
    assert bus.read(0x2800) == 0x55
    assert bus.read(0x2C00) == 0x55


# ---------------------------------------------------------------------------
# Palette mirroring
# ---------------------------------------------------------------------------

def test_palette_mirror_3f10_3f00():
    """$3F10 mirrors to $3F00 (background palette 0)."""
    mapper = _FakeMapper()
    bus = PPUBus(mapper)

    bus.write(0x3F00, 0x12)
    assert bus.read(0x3F10) == 0x12
    bus.write(0x3F10, 0x34)
    assert bus.read(0x3F00) == 0x34


def test_palette_mirror_3f14_3f04():
    """$3F14 mirrors to $3F04."""
    mapper = _FakeMapper()
    bus = PPUBus(mapper)

    bus.write(0x3F04, 0xAB)
    assert bus.read(0x3F14) == 0xAB


def test_palette_read_write():
    """Direct palette read/write works."""
    mapper = _FakeMapper()
    bus = PPUBus(mapper)

    bus.write(0x3F01, 0x0F)
    assert bus.read(0x3F01) == 0x0F


# ---------------------------------------------------------------------------
# CHR passthrough
# ---------------------------------------------------------------------------

def test_chr_passthrough_to_mapper():
    """$0000-$1FFF routes to mapper.ppu_read/write."""
    chr_data = bytearray(8192)
    chr_data[0x100] = 0x77
    mapper = _FakeMapper(chr_data=chr_data)
    bus = PPUBus(mapper)

    assert bus.read(0x0100) == 0x77
    bus.write(0x0100, 0x88)
    assert bus.read(0x0100) == 0x88
