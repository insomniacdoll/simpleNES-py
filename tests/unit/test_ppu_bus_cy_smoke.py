"""Smoke tests for Cython PPUBusCy — verifies correctness against pure Python oracle.

Tests use ``pytest.importorskip("simplenes.bus._ppu_bus_cy")`` so they
silently skip when the Cython extension is not compiled.
"""
import pytest

from simplenes.bus.ppu_bus import PPUBus as PyPPUBus
from simplenes.cartridge.image import Mirroring

_ppu_bus_cy = pytest.importorskip("simplenes.bus._ppu_bus_cy")
PPUBusCy = _ppu_bus_cy.PPUBusCy


# ---------------------------------------------------------------------------
# Minimal fake mapper for nametable / palette tests
# ---------------------------------------------------------------------------

class _FakeMapper:
    """Minimal mapper for PPUBus testing — supports mirroring prop + observer."""

    __slots__ = ("_mirroring", "_chr", "_observe_calls")

    def __init__(self, mirroring=Mirroring.HORIZONTAL, chr_data=None,
                 has_observer=False):
        self._mirroring = mirroring
        self._chr = bytearray(chr_data) if chr_data else bytearray(8192)
        self._observe_calls = [] if has_observer else None

    @property
    def mirroring(self):
        return self._mirroring

    @mirroring.setter
    def mirroring(self, value):
        self._mirroring = value

    @property
    def has_ppu_observer(self):
        return self._observe_calls is not None

    def observe_ppu_address(self, address):
        if self._observe_calls is not None:
            self._observe_calls.append(address)

    def ppu_read(self, address):
        return self._chr[address & 0x1FFF]

    def ppu_write(self, address, value):
        self._chr[address & 0x1FFF] = value & 0xFF


# ---------------------------------------------------------------------------
# Nametable mirroring — PPUBusCy vs PyPPUBus
# ---------------------------------------------------------------------------

def test_nametable_horizontal_mirroring():
    """NT0/NT1 map to physical NT0; NT2/NT3 map to NT1 — both backends agree."""
    mapper_py = _FakeMapper(mirroring=Mirroring.HORIZONTAL)
    mapper_cy = _FakeMapper(mirroring=Mirroring.HORIZONTAL)
    bus_py = PyPPUBus(mapper_py)
    bus_cy = PPUBusCy(mapper_cy)

    for addr, val in [(0x2000, 0x11), (0x2400, 0x22), (0x2800, 0x33), (0x2C00, 0x44)]:
        bus_py.write(addr, val)
        bus_cy.write(addr, val)
    for addr in (0x2000, 0x2400, 0x2800, 0x2C00):
        assert bus_cy.read(addr) == bus_py.read(addr), f"mismatch at ${addr:04X}"


def test_nametable_vertical_mirroring():
    """NT0/NT2 map to physical NT0; NT1/NT3 map to NT1 — both backends agree."""
    mapper_py = _FakeMapper(mirroring=Mirroring.VERTICAL)
    mapper_cy = _FakeMapper(mirroring=Mirroring.VERTICAL)
    bus_py = PyPPUBus(mapper_py)
    bus_cy = PPUBusCy(mapper_cy)

    for addr, val in [(0x2000, 0xAA), (0x2800, 0xBB), (0x2400, 0xCC), (0x2C00, 0xDD)]:
        bus_py.write(addr, val)
        bus_cy.write(addr, val)
    for addr in (0x2000, 0x2800, 0x2400, 0x2C00):
        assert bus_cy.read(addr) == bus_py.read(addr), f"mismatch at ${addr:04X}"


def test_nametable_single_screen_lower():
    """All 4 NT slots map to physical NT0."""
    mapper_py = _FakeMapper(mirroring=Mirroring.SINGLE_SCREEN_LOWER)
    mapper_cy = _FakeMapper(mirroring=Mirroring.SINGLE_SCREEN_LOWER)
    bus_py = PyPPUBus(mapper_py)
    bus_cy = PPUBusCy(mapper_cy)

    bus_py.write(0x2000, 0x55)
    bus_cy.write(0x2000, 0x55)
    for addr in (0x2000, 0x2400, 0x2800, 0x2C00):
        assert bus_cy.read(addr) == 0x55, f"expected 0x55 at ${addr:04X}"


# ---------------------------------------------------------------------------
# Palette mirroring
# ---------------------------------------------------------------------------

def test_palette_mirror_3f10_3f00():
    """$3F10 mirrors to $3F00 — both backends agree."""
    mapper_py = _FakeMapper()
    mapper_cy = _FakeMapper()
    bus_py = PyPPUBus(mapper_py)
    bus_cy = PPUBusCy(mapper_cy)

    bus_py.write(0x3F00, 0x12)
    bus_cy.write(0x3F00, 0x12)
    assert bus_cy.read(0x3F10) == 0x12

    bus_py.write(0x3F10, 0x34)
    bus_cy.write(0x3F10, 0x34)
    assert bus_cy.read(0x3F00) == 0x34


def test_palette_mirror_3f14_3f04():
    """$3F14 mirrors to $3F04."""
    mapper_py = _FakeMapper()
    mapper_cy = _FakeMapper()
    bus_py = PyPPUBus(mapper_py)
    bus_cy = PPUBusCy(mapper_cy)

    bus_py.write(0x3F04, 0xAB)
    bus_cy.write(0x3F04, 0xAB)
    assert bus_cy.read(0x3F14) == 0xAB


# ---------------------------------------------------------------------------
# get_palette_cache() — shared reference sync verification
# ---------------------------------------------------------------------------

def test_get_palette_cache_sync_after_write():
    """After bus.write($3F01, $7F), cache[1]==0x3F and peek_palette(1)==0x3F."""
    mapper = _FakeMapper()
    bus = PPUBusCy(mapper)

    cache = bus.get_palette_cache()
    assert isinstance(cache, bytearray)
    assert len(cache) == 32

    bus.write(0x3F01, 0x7F)
    # $7F & $3F = $3F (only lower 6 bits stored in palette cache)
    assert cache[1] == 0x3F, f"cache[1] = {cache[1]:#04x}, expected 0x3F"
    assert bus.peek_palette(1) == 0x3F, (
        f"peek_palette(1) = {bus.peek_palette(1):#04x}, expected 0x3F"
    )


# ---------------------------------------------------------------------------
# MMC1 dynamic mirroring — mirroring changes must take effect
# ---------------------------------------------------------------------------

def test_dynamic_mirroring_switching():
    """After changing mapper.mirroring at runtime, nametable reflects new mode."""
    mapper_py = _FakeMapper(mirroring=Mirroring.HORIZONTAL)
    mapper_cy = _FakeMapper(mirroring=Mirroring.HORIZONTAL)
    bus_py = PyPPUBus(mapper_py)
    bus_cy = PPUBusCy(mapper_cy)

    # Start in HORIZONTAL
    bus_py.write(0x2000, 0x99)
    bus_cy.write(0x2000, 0x99)
    assert bus_cy.read(0x2400) == 0x99  # H: NT0==NT1

    # Switch to VERTICAL
    mapper_py.mirroring = Mirroring.VERTICAL
    mapper_cy.mirroring = Mirroring.VERTICAL

    bus_py.write(0x2000, 0xEE)
    bus_cy.write(0x2000, 0xEE)
    # V: NT0 maps to NT0, NT2→NT0. NT1/NT3 → NT1 (untouched from earlier).
    assert bus_cy.read(0x2000) == 0xEE
    assert bus_cy.read(0x2800) == 0xEE  # NT2 mirrors NT0 in V mode

    # Switch to SINGLE_SCREEN_LOWER
    mapper_py.mirroring = Mirroring.SINGLE_SCREEN_LOWER
    mapper_cy.mirroring = Mirroring.SINGLE_SCREEN_LOWER

    bus_py.write(0x2C00, 0xFF)
    bus_cy.write(0x2C00, 0xFF)
    # All NT slots → physical NT0
    assert bus_cy.read(0x2000) == 0xFF
    assert bus_cy.read(0x2800) == 0xFF
    assert bus_cy.read(0x2C00) == 0xFF


# ---------------------------------------------------------------------------
# MMC3 observer — verify observe_ppu_address is called
# ---------------------------------------------------------------------------

def test_observer_is_called():
    """When mapper has observer, bus.read/write calls observe_ppu_address."""
    mapper = _FakeMapper(has_observer=True)
    bus = PPUBusCy(mapper)

    bus.read(0x0000)
    bus.read(0x2000)
    bus.write(0x3F00, 0x20)

    assert len(mapper._observe_calls) >= 3
    # peek_palette must NOT trigger observer
    before = len(mapper._observe_calls)
    bus.peek_palette(0)
    assert len(mapper._observe_calls) == before, "peek_palette should NOT trigger observer"


def test_observer_not_called_without_flag():
    """When mapper has no observer, no error (common path for NROM)."""
    mapper = _FakeMapper(has_observer=False)
    bus = PPUBusCy(mapper)

    # These should not raise
    bus.read(0x0000)
    bus.read(0x2000)
    bus.write(0x3F00, 0x20)
    bus.peek_palette(0)
