"""Integration test: nestest trace comparison.

Requires a nestest.nes ROM at tests/roms/nestest.nes.
If the ROM is not present the test is skipped.

To configure:
    1. Place nestest.nes in tests/roms/nestest.nes
    2. Optionally place nestest.log in tests/roms/nestest.log
       (if missing, only instruction-count smoke test runs)
"""

import pytest

from simplenes.cpu.cpu import CPU
from simplenes.bus.cpu_bus import CPUBus
from simplenes.ppu.ppu import PPU
from simplenes.apu.apu import APU
from simplenes.cartridge.ines import RomParser
from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
from simplenes.bus.ppu_bus import PPUBus
from simplenes.dma.oam_dma import OAMDMAState
from simplenes.input.controller import Controller
from simplenes.interrupts import InterruptLines
from simplenes.errors import IllegalOpcodeError

from tests.fixtures.nestest_helper import (
    get_nestest_rom_path,
    load_nestest_rom,
    parse_nestest_log,
    ROM_PATH,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _build_cpu_for_nestest(rom_bytes: bytes) -> CPU:
    """Wire up a CPU with the given nestest ROM."""
    image = RomParser.parse(rom_bytes)
    mapper = NROMMapper(image)
    interrupts = InterruptLines()
    ppu_bus = PPUBus(mapper)
    ppt = PPU(bus=ppu_bus, interrupts=interrupts)
    apu = APU(interrupts=interrupts)
    bus = CPUBus(ppu=ppt, apu=apu, mapper=mapper,
                 controller1=Controller(), controller2=Controller(),
                 oam_dma_state=OAMDMAState())
    return CPU(bus=bus, interrupts=interrupts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_SKIP_NO_ROM = pytest.mark.skipif(
    get_nestest_rom_path() is None,
    reason="nestest.nes not found in tests/roms/",
)


@_SKIP_NO_ROM
def test_nestest_starts():
    """Basic smoke test: nestest ROM boots and runs N instructions without error.

    Since Phase 2 does not implement illegal opcodes, encountering one
    is a hard failure — nestest's official-only instruction range should
    not trigger it.
    """
    rom_path = get_nestest_rom_path()
    assert rom_path is not None

    cpu = _build_cpu_for_nestest(load_nestest_rom(rom_path))
    cpu.reset()

    # Verify reset set PC to a valid PRG region (not 0)
    assert cpu.pc >= 0x8000, f"PC not in PRG ROM: ${cpu.pc:04X}"

    # Run instructions and verify no crash
    for step_idx in range(100):
        try:
            cpu.step_instruction()
        except IllegalOpcodeError as exc:
            pytest.fail(
                f"Illegal opcode during nestest smoke run "
                f"at step {step_idx}: {exc}"
            )


@_SKIP_NO_ROM
def test_nestest_trace_compare():
    """Compare emulator trace against nestest.log golden file.

    Compares PC, A, X, Y, P, SP, CYC for the first 500 instructions.
    Reports the first mismatch with both expected and actual values.
    """
    rom_path = get_nestest_rom_path()
    assert rom_path is not None

    log_path = ROM_PATH.with_suffix(".log")
    assert log_path.exists(), f"nestest.log not found at {log_path}"

    golden = parse_nestest_log(log_path)
    assert golden, "nestest.log is empty or unparseable"

    cpu = _build_cpu_for_nestest(load_nestest_rom(rom_path))
    cpu.reset()

    # Some nestest ROM variants reset to $8000 while official logs start
    # at $C000. For NROM-128 these may mirror the same PRG data; align PC
    # to the golden trace start so the comparison is valid.
    reset_pc = cpu.pc
    cpu.pc = golden[0].pc

    cpu.set_trace_enabled(True)

    max_compare = min(500, len(golden))

    mismatches = []
    for i in range(max_compare):
        expected = golden[i]

        # Advance one instruction
        try:
            cpu.step_instruction()
        except IllegalOpcodeError as exc:
            pytest.fail(
                f"Illegal opcode during nestest trace compare "
                f"at line {i}, expected PC=${expected.pc:04X}: {exc}"
            )

        logger = cpu.get_trace_logger()
        actual = logger.entries[-1] if logger and logger.entries else None
        if actual is None:
            mismatches.append(
                f"Line {i}: no trace entry. Expected PC=${expected.pc:04X}"
            )
            break

        # Compare all fields including cycle count
        errors = []
        if actual.pc != expected.pc:
            errors.append(f"PC act=${actual.pc:04X} exp=${expected.pc:04X}")
        if actual.a != expected.a:
            errors.append(f"A act={actual.a:02X} exp={expected.a:02X}")
        if actual.x != expected.x:
            errors.append(f"X act={actual.x:02X} exp={expected.x:02X}")
        if actual.y != expected.y:
            errors.append(f"Y act={actual.y:02X} exp={expected.y:02X}")
        if actual.p != expected.p:
            errors.append(f"P act={actual.p:02X} exp={expected.p:02X}")
        if actual.sp != expected.sp:
            errors.append(f"SP act={actual.sp:02X} exp={expected.sp:02X}")
        if actual.cycle != expected.cycle:
            errors.append(f"CYC act={actual.cycle} exp={expected.cycle}")

        if errors:
            mismatches.append(
                f"Line {i}: Insn=${expected.opcode:02X} {expected.mnemonic} "
                + " ".join(errors)
            )
            break  # Stop on first mismatch

    if mismatches:
        mismatches.append(
            f"(reset PC=${reset_pc:04X}, trace start=${golden[0].pc:04X})"
        )
        pytest.fail("\n".join(mismatches))

    expected_compared = i + 1
    assert expected_compared == max_compare, (
        f"Only compared {expected_compared} of {max_compare} entries"
    )
