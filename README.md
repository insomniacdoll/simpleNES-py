# simpleNES-py

A correctness-first NES emulator implemented in Python 3.12+, designed for testability and incremental evolution.

## Features

-   **CPU** — Full 6502 official instruction set with cycle-accurate execution and interrupt handling
-   **PPU** — Register interface, background rendering, sprite rendering, OAM DMA, palette indexing, with optional Cython acceleration
-   **APU** — Pulse (2 channels), triangle, noise, frame counter, length counter, envelope, mixer at 44100 Hz
-   **Controllers** — Single controller input with strobe protocol
-   **Mappers** — NROM (0), MMC1 (1), UxROM (2), CNROM (3), MMC3 (4) with PRG/CHR banking, mirroring control, and MMC3 scanline IRQ
-   **Pygame frontend** — Optional graphical frontend with keyboard input and audio playback
-   **Headless mode** — Run without GUI for testing and automation
-   **nestest integration** — CPU emulation verified against the standard nestest ROM

## Quick Start

### Install

```bash
# Clone and install the package in development mode
git clone <repo-url>
cd simpleNES-py
uv sync

# Or install with the Pygame frontend and Cython optimizations
uv sync --group frontend --group dev
```

### Build Cython Extensions (optional, boosts performance ~2x)

```bash
uv run python scripts/build_cython.py
```

After building, the emulator automatically uses Cython-accelerated PPU and PPUBus modules.
Use `SIMPLENES_BACKEND` to override:

```bash
SIMPLENES_BACKEND=cython uv run simplenes game.nes   # force Cython (fails if not built)
SIMPLENES_BACKEND=python uv run simplenes game.nes   # force pure Python
# unset → auto-detect: uses Cython if available, falls back to pure Python
```

### Run

```bash
# Launch the Pygame frontend with a ROM
uv run simplenes path/to/game.nes

# Use headless mode
uv run python -m simplenes path/to/game.nes --headless
```

### Run Tests

```bash
uv run ruff check src/ tests/
SIMPLENES_BACKEND=python uv run pytest tests/ -q

# If Cython extensions are built:
SIMPLENES_BACKEND=cython uv run pytest tests/ -q
```

### Run Benchmarks

```bash
SIMPLENES_BACKEND=python uv run pytest benchmarks/ --benchmark-only -q
SIMPLENES_BACKEND=cython uv run pytest benchmarks/ --benchmark-only -q
```

## Architecture

The emulator mirrors NES hardware boundaries. Each component owns its own state and communicates through well-defined interfaces.

```text
Frontend
  └── NESMachine
        ├── Scheduler         → drives CPU/PPU/APU clocks
        ├── CPU  ── CPUBus    → RAM, PPU regs, APU regs, Controller, Mapper
        ├── PPU  ── PPUBus    → CHR, Nametable, Palette, Mapper
        ├── APU               → audio channels, frame counter, mixer
        ├── OAM DMA           → sprite DMA coordinator
        ├── InterruptLines    → shared NMI / IRQ lines
        └── Mapper            → PRG/CHR banking, mirroring, IRQ
```

### Key Boundaries

| Component | Responsibility |
|-----------|---------------|
| `CartridgeImage` | Static ROM metadata (iNES parsing result) |
| `Mapper`          | Runtime banking, mirroring, IRQ behavior per mapper |
| `CPUBus`          | CPU address space and register routing |
| `PPUBus`          | PPU address space, nametable mirroring, palette mirrors |
| `CPU`             | Registers, flags, instruction execution, interrupt sampling |
| `PPU`             | Registers, rendering pipeline, OAM, framebuffer, NMI generation |
| `APU`             | Audio channels, frame counter, IRQ, sample buffer |
| `Scheduler`       | Clock advance, DMA coordination, interrupt timing |
| `NESMachine`      | Composition root and stable public API |
| `Frontend`        | Window, input, audio output, frame pacing — never imported by core |

### SIMPLENES_BACKEND

The emulator supports optional Cython-compiled hot-path modules. The runtime backend is controlled by the `SIMPLENES_BACKEND` environment variable:

| `SIMPLENES_BACKEND` | PPU | PPUBus | Behavior |
|---------------------|------|--------|----------|
| (unset) | auto | auto | Auto-detect; Cython if built, pure Python fallback |
| `python` | pure Python | pure Python | Force pure Python (CI / oracle validation) |
| `cython` | Cython | Cython | Both must be built; fails loudly if missing |

## Project Structure

```text
src/simplenes/
├── apu/                 # Audio Processing Unit
│   ├── apu.py           # APU coordinator
│   ├── pulse.py         # Pulse channel (2 instances)
│   ├── triangle.py      # Triangle channel
│   ├── noise.py         # Noise channel
│   ├── envelope.py      # Envelope generator
│   ├── length_counter.py
│   ├── frame_counter.py # $4017 frame counter
│   └── mixer.py         # DAC → linear output mixer
├── bus/
│   ├── __init__.py       # PPUBus backend auto-selection
│   ├── cpu_bus.py        # CPU address space ($0000-$FFFF)
│   ├── ppu_bus.py        # PPU address space ($0000-$3FFF) — pure Python oracle
│   └── _ppu_bus_cy.pyx   # Cython PPUBus (optional, compiled to .so)
├── cartridge/
│   ├── image.py         # CartridgeImage + Mirroring enum
│   ├── ines.py          # iNES 1.0 parser
│   ├── mapper.py        # Mapper protocol
│   └── mappers/         # Mapper implementations
│       ├── mapper000_nrom.py
│       ├── mapper001_mmc1.py
│       ├── mapper002_uxrom.py
│       ├── mapper003_cnrom.py
│       └── mapper004_mmc3.py
├── cpu/
│   ├── cpu.py           # 6502 CPU core
│   └── opcodes.py       # Opcode table
├── dma/
│   └── oam_dma.py       # OAM DMA controller
├── frontend/
│   ├── protocol.py      # Frontend interface
│   ├── headless.py      # Headless frontend
│   ├── palette.py       # NES palette LUT
│   └── pygame_frontend.py
├── input/
│   └── controller.py    # Standard controller
├── ppu/
│   ├── __init__.py       # PPU backend auto-selection
│   ├── ppu.py            # PPU core — pure Python oracle
│   └── _ppu_cy.pyx       # Cython PPU (optional, compiled to .so)
├── machine.py           # NESMachine composition root
├── scheduler.py         # Clock scheduler
├── interrupts.py        # InterruptLines
├── timing.py            # NTSC timing constants
├── errors.py            # Domain errors
├── cli.py               # CLI entry point
└── __main__.py          # Allow `python -m simplenes`

benchmarks/              # Performance micro-benchmarks
├── conftest.py
├── test_bench_ppu.py
└── test_bench_scheduler.py

scripts/
└── build_cython.py      # In-place Cython extension builder
```

## Mapper Support

| ID | Name   | PRG Banking             | CHR Banking                  | Mirroring | IRQ      |
|----|--------|-------------------------|------------------------------|-----------|----------|
| 0  | NROM   | 16/32 KiB fixed         | 8 KiB fixed CHR-ROM/RAM      | Header    | —        |
| 1  | MMC1   | 32 KiB / 16+16 KiB      | 8 KiB / 4+4 KiB              | MMC1      | —        |
| 2  | UxROM  | 16 KiB switchable + 16 KiB fixed | 8 KiB CHR-RAM       | Header    | —        |
| 3  | CNROM  | 16/32 KiB fixed         | 8 KiB switchable CHR-ROM     | Header    | —        |
| 4  | MMC3   | 8 KiB × 4               | 2 KiB × 2 + 1 KiB × 4        | MMC3      | Scanline |

## Testing

```text
tests/
├── unit/                        # Per-component unit tests
│   ├── test_cpu.py              # CPU instruction set
│   ├── test_ppu_registers.py
│   ├── test_ppu_background.py
│   ├── test_ppu_sprites.py
│   ├── test_ppu_bus.py          # PPUBus oracle correctness
│   ├── test_ppu_cy_smoke.py     # Cython PPU vs oracle equivalence
│   ├── test_ppu_bus_cy_smoke.py # Cython PPUBus vs oracle equivalence
│   ├── test_apu.py              # Audio channels and mixer
│   ├── test_mapper2.py          # UxROM
│   ├── test_mapper3.py          # CNROM
│   ├── test_mmc1.py             # MMC1
│   ├── test_mmc3.py             # MMC3
│   └── ...
├── integration/
│   ├── test_machine.py          # NESMachine factory and lifecycle
│   └── test_nestest.py          # Official CPU diagnostic ROM
└── fixtures/
    ├── nrom_sample.py           # Test ROM builder
    └── nestest_helper.py        # nestest log parser
```

Run the full suite:

```bash
uv run ruff check src/ tests/
SIMPLENES_BACKEND=python uv run pytest tests/ -q
SIMPLENES_BACKEND=cython uv run pytest tests/ -q     # after build_cython.py
```

## Requirements

-   Python 3.12+
-   Optional: `pygame >= 2` for the graphical frontend (`uv sync --group frontend`)
-   Optional: `cython >= 3.0`, `setuptools >= 64` for Cython compilation (`uv sync --group dev`)
-   Optional: `pytest-benchmark >= 5.2.3` for performance benchmarks
-   NTSC only (PAL / Dendy not yet supported)

## License

MIT
