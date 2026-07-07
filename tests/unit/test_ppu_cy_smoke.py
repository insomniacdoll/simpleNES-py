"""Smoke tests verifying Cython PPUCy matches pure-Python PPU oracle.

Smoke tests that depend on the compiled _ppu_cy extension are skipped
unless SIMPLENES_BACKEND=cython (loud fail) or the extension is built.
"""
import hashlib
import os

import pytest

from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.interrupts import InterruptLines


# ---------------------------------------------------------------------------
# Helper — import _ppu_cy safely, skip/raise per policy
# ---------------------------------------------------------------------------

def _get_ppu_cy():
    """Return PPUCy class or skip / raise per SIMPLENES_BACKEND policy."""
    if os.environ.get("SIMPLENES_BACKEND") == "cython":
        # Force Cython — loud failure if not built (design requirement)
        from simplenes.ppu._ppu_cy import PPUCy  # noqa: F811
        return PPUCy

    # Auto-detect: skip if not compiled, use if available
    module = pytest.importorskip(
        "simplenes.ppu._ppu_cy",
        reason="Cython PPU extension is not built",
    )
    return module.PPUCy


# ---------------------------------------------------------------------------
# Shared test ROM helpers
# ---------------------------------------------------------------------------

def _make_nrom_prg():
    """32 KiB NROM with NOP-loop at RESET vector (same as benchmarks/conftest)."""
    prg = bytearray([0xEA] * 32768)
    prg[0x0000] = 0xEA       # NOP
    prg[0x0001] = 0x4C       # JMP $8000
    prg[0x0002] = 0x00
    prg[0x0003] = 0x80
    prg[0x7FFA] = 0x00       # NMI vector
    prg[0x7FFB] = 0x80
    prg[0x7FFC] = 0x00       # RESET → $8000
    prg[0x7FFD] = 0x80
    prg[0x7FFE] = 0x00       # IRQ/BRK
    prg[0x7FFF] = 0x80
    return bytes(prg)


def _make_nrom_image():
    return CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=0,
        submapper_id=0,
        prg_rom=_make_nrom_prg(),
        chr_rom=b"\x00" * 8192,
        prg_ram_size=0,
        prg_nvram_size=0,
        chr_ram_size=0,
        chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL,
        has_battery=False,
        has_trainer=False,
    )


def _framebuffer_hash(ppu) -> str:
    return hashlib.sha256(ppu.framebuffer).hexdigest()


# ---------------------------------------------------------------------------
# Core equivalence tests
# ---------------------------------------------------------------------------

def test_ppu_cy_vs_python_10_frames():
    """After 10 frames, Cython PPU matches pure-Python oracle."""
    PPUCy = _get_ppu_cy()
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
    from simplenes.ppu.ppu import PPU as PurePPU

    image = _make_nrom_image()

    # Pure Python oracle
    mapper_py = NROMMapper(image)
    bus_py = PPUBus(mapper_py)
    int_py = InterruptLines()
    ppu_py = PurePPU(bus=bus_py, interrupts=int_py)

    # Cython backend
    mapper_cy = NROMMapper(image)
    bus_cy = PPUBus(mapper_cy)
    int_cy = InterruptLines()
    ppu_cy = PPUCy(bus=bus_cy, interrupts=int_cy)

    # Run 10 frames through 89342 dots each
    for _ in range(10):
        for _ in range(89342):
            ppu_py.clock()
            ppu_cy.clock()

    assert ppu_py.status == ppu_cy.status
    assert ppu_py.control == ppu_cy.control
    assert ppu_py.mask == ppu_cy.mask
    assert ppu_py.scanline == ppu_cy.scanline
    assert ppu_py.dot == ppu_cy.dot
    assert ppu_py.frame == ppu_cy.frame
    assert _framebuffer_hash(ppu_py) == _framebuffer_hash(ppu_cy)


def test_clock_vs_advance_dots_equivalence():
    """advance_dots(N) produces identical state to N × clock()."""
    PPUCy = _get_ppu_cy()
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper

    image = _make_nrom_image()

    mapper_a = NROMMapper(image)
    bus_a = PPUBus(mapper_a)
    ints_a = InterruptLines()
    ppu_a = PPUCy(bus=bus_a, interrupts=ints_a)

    mapper_b = NROMMapper(_make_nrom_image())
    bus_b = PPUBus(mapper_b)
    ints_b = InterruptLines()
    ppu_b = PPUCy(bus=bus_b, interrupts=ints_b)

    # Advance both by 1000 dots using different APIs
    for _ in range(1000):
        ppu_a.clock()
    ppu_b.advance_dots(1000)

    assert ppu_a.status == ppu_b.status
    assert ppu_a.control == ppu_b.control
    assert ppu_a.mask == ppu_b.mask
    assert ppu_a.scanline == ppu_b.scanline
    assert ppu_a.dot == ppu_b.dot
    assert ppu_a.frame == ppu_b.frame
    assert _framebuffer_hash(ppu_a) == _framebuffer_hash(ppu_b)


# ---------------------------------------------------------------------------
# VBlank / NMI
# ---------------------------------------------------------------------------

def test_ppu_cy_vblank_nmi_set():
    """PPUCy enters VBlank at scanline 241 dot 1 and sets NMI."""
    PPUCy = _get_ppu_cy()
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper

    image = _make_nrom_image()
    mapper = NROMMapper(image)
    bus = PPUBus(mapper)
    ints = InterruptLines()
    ppu = PPUCy(bus=bus, interrupts=ints)

    # Enable NMI and rendering
    ppu.write_register(0x2000, 0x80)  # NMI enable
    ppu.write_register(0x2001, 0x1E)  # sprites + bg enabled

    # Advance to scanline 241, dot 1
    ppu.scanline = 241
    ppu.dot = 0
    ppu.clock()

    assert ppu.status & 0x80, "VBlank flag should be set"
    assert ints.nmi_pending, "NMI should be pending"


# ---------------------------------------------------------------------------
# PPUDATA read buffer
# ---------------------------------------------------------------------------

def test_ppu_cy_ppudata_read_buffer():
    """PPUDATA read buffer behaves correctly."""
    PPUCy = _get_ppu_cy()
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper

    image = _make_nrom_image()
    mapper = NROMMapper(image)
    bus = PPUBus(mapper)
    ints = InterruptLines()
    ppu = PPUCy(bus=bus, interrupts=ints)

    # Write PPUADDR to point at nametable $2000
    ppu.write_register(0x2006, 0x20)
    ppu.write_register(0x2006, 0x00)

    # Write to nametable
    ppu.write_register(0x2007, 0x42)

    # Reset address and read back — first read is buffer
    ppu.write_register(0x2006, 0x20)
    ppu.write_register(0x2006, 0x00)
    ppu.read_register(0x2007)  # first read: buffer (discard)
    val = ppu.read_register(0x2007)  # second read: actual value

    # Second read should be the written value
    assert val == 0x42, f"Expected 0x42, got {val}"


# ---------------------------------------------------------------------------
# OAM DMA write
# ---------------------------------------------------------------------------

def test_ppu_cy_oam_dma_write():
    """OAM DMA writes into oam bytearray are visible."""
    PPUCy = _get_ppu_cy()
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper

    image = _make_nrom_image()
    mapper = NROMMapper(image)
    bus = PPUBus(mapper)
    ints = InterruptLines()
    ppu = PPUCy(bus=bus, interrupts=ints)

    # Simulate OAM DMA: set oam_address, write 4 bytes
    ppu.write_register(0x2003, 0)  # OAMADDR = 0
    ppu.write_register(0x2004, 0x10)  # y
    ppu.write_register(0x2004, 0x42)  # tile
    ppu.write_register(0x2004, 0x03)  # attr
    ppu.write_register(0x2004, 0x20)  # x

    assert ppu.oam[0] == 0x10
    assert ppu.oam[1] == 0x42
    assert ppu.oam[2] == 0x03
    assert ppu.oam[3] == 0x20


# ---------------------------------------------------------------------------
# NESMachine integration
# ---------------------------------------------------------------------------

def test_ppu_cy_machine_run_frame_smoke():
    """NESMachine with active PPU backend runs a frame cleanly."""
    from simplenes.machine import NESMachine
    image = _make_nrom_image()
    machine = NESMachine(image)

    # Verify machine initializes cleanly
    assert machine.framebuffer is not None
    machine.run_frame()
    # frame counter should advance
    assert machine._ppu.frame >= 1
