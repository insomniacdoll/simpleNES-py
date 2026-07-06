"""PPU micro-benchmarks."""


def test_bench_ppu_clock_visible_scanline(benchmark, ppu):
    """Measure PPU.clock() throughput on visible scanlines."""
    ppu.scanline = 100
    ppu.dot = 1
    ppu.write_register(0x2001, 0x0E)  # enable background
    benchmark(ppu.clock)


def test_bench_ppu_clock_vblank(benchmark, ppu):
    """Measure PPU.clock() throughput during VBlank."""
    ppu.scanline = 241
    ppu.dot = 1
    benchmark(ppu.clock)


def test_bench_ppu_clock_prerender(benchmark, ppu):
    """Measure PPU.clock() throughput during pre-render scanline."""
    ppu.scanline = 261
    ppu.dot = 1
    benchmark(ppu.clock)
