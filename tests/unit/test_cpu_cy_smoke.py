"""Smoke tests verifying Cython CPUCy matches pure-Python Oracle.

Smoke tests that depend on the compiled _cpu_cy extension are skipped
unless SIMPLENES_BACKEND=cython (loud fail) or the extension is built.
"""
import os

import pytest

from simplenes.cpu.cpu import CPU as PureCPU
from simplenes.bus.cpu_bus import CPUBus
from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.interrupts import InterruptLines


# ---------------------------------------------------------------------------
# Helper — import _cpu_cy safely, skip/raise per policy
# ---------------------------------------------------------------------------

def _get_ppu_cy():
    """Return CPUCy class or skip / raise per SIMPLENES_BACKEND policy."""
    if os.environ.get("SIMPLENES_BACKEND") == "cython":
        from simplenes.cpu._cpu_cy import CPUCy
        return CPUCy
    module = pytest.importorskip(
        "simplenes.cpu._cpu_cy",
        reason="Cython CPU extension is not built",
    )
    return module.CPUCy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RAM_BASE = 0x0200  # test code runs from CPU RAM (writable)


def _make_nrom_prg():
    """32 KiB PRG with RESET vector = $C000, filled with NOP."""
    prg = bytearray([0xEA] * 32768)
    prg[0x7FFC] = 0x00
    prg[0x7FFD] = 0xC0  # RESET -> $C000
    prg[0x7FFA] = 0x00
    prg[0x7FFB] = 0xC0  # NMI
    prg[0x7FFE] = 0x00
    prg[0x7FFF] = 0xC0  # IRQ
    return bytes(prg)


def _make_nrom_image():
    return CartridgeImage(
        format=RomFormat.INES_1_0, mapper_id=0, submapper_id=0,
        prg_rom=_make_nrom_prg(), chr_rom=b"\x00" * 8192,
        prg_ram_size=0, prg_nvram_size=0, chr_ram_size=0, chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL, has_battery=False, has_trainer=False,
    )


def _setup_cpus(reset_pc=None):
    """Return (cpu_py, cpu_cy, bus_py, bus_cy) with matching memory."""
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
    from simplenes.ppu.ppu import PPU as PurePPU
    from simplenes.apu.apu import APU
    from simplenes.dma.oam_dma import OAMDMAState
    from simplenes.input.controller import Controller

    CPUCy = _get_ppu_cy()

    def _make_bus(mapper, intr):
        ppu_bus = PPUBus(mapper)
        ppu = PurePPU(bus=ppu_bus, interrupts=intr)
        apu = APU(interrupts=intr)
        return CPUBus(ppu=ppu, apu=apu, mapper=mapper,
                      controller1=Controller(), controller2=Controller(),
                      oam_dma_state=OAMDMAState())

    mapper_py = NROMMapper(_make_nrom_image())
    int_py = InterruptLines()
    bus_py = _make_bus(mapper_py, int_py)
    cpu_py = PureCPU(bus=bus_py, interrupts=int_py)

    mapper_cy = NROMMapper(_make_nrom_image())
    int_cy = InterruptLines()
    bus_cy = _make_bus(mapper_cy, int_cy)
    cpu_cy = CPUCy(bus=bus_cy, interrupts=int_cy)

    cpu_py.reset()
    cpu_cy.reset()
    if reset_pc is not None:
        cpu_py.pc = reset_pc
        cpu_cy.pc = reset_pc
    return cpu_py, cpu_cy, bus_py, bus_cy


def _write_rom(cpu, bus, *bytes_list):
    """Write instruction bytes at cpu.pc into RAM (writable)."""
    for i, b in enumerate(bytes_list):
        bus.write(cpu.pc + i, b)


# ---------------------------------------------------------------------------
# Register state after reset
# ---------------------------------------------------------------------------

def test_reset_register_state():
    cpu_py, cpu_cy, _, _ = _setup_cpus()
    assert cpu_cy.a == cpu_py.a
    assert cpu_cy.x == cpu_py.x
    assert cpu_cy.y == cpu_py.y
    assert cpu_cy.sp == cpu_py.sp
    assert cpu_cy.pc == cpu_py.pc
    assert cpu_cy.p == cpu_py.p
    assert cpu_cy.total_cycles == cpu_py.total_cycles


# ---------------------------------------------------------------------------
# LDA / STA
# ---------------------------------------------------------------------------

def test_lda_immediate():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        _write_rom(cpu, bus, 0xA9, 0x42)  # LDA #$42
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.a == 0x42
    assert cpu_cy.a == cpu_py.a
    assert cpu_cy.p == cpu_py.p
    assert cpu_cy.pc == cpu_py.pc
    assert cpu_cy.total_cycles == cpu_py.total_cycles


def test_sta_absolute():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.a = 0x88
        _write_rom(cpu, bus, 0x8D, 0x00, 0x04)  # STA $0400
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert bus_cy.read(0x0400) == 0x88
    assert bus_cy.read(0x0400) == bus_py.read(0x0400)


# ---------------------------------------------------------------------------
# JMP
# ---------------------------------------------------------------------------

def test_jmp_absolute():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        _write_rom(cpu, bus, 0x4C, 0x00, 0x03)  # JMP $0300
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.pc == cpu_py.pc == 0x0300


# ---------------------------------------------------------------------------
# Branch
# ---------------------------------------------------------------------------

def test_beq_taken():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.p |= cpu.FLAG_Z
        _write_rom(cpu, bus, 0xF0, 0x04)  # BEQ +4
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.pc == cpu_py.pc


def test_beq_not_taken():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.p &= ~cpu.FLAG_Z
        _write_rom(cpu, bus, 0xF0, 0x04)
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.pc == cpu_py.pc


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------

def test_pha_pla():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.a = 0xCD
        _write_rom(cpu, bus, 0x48, 0x68)  # PHA / PLA
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.a == 0xCD
    assert cpu_cy.a == cpu_py.a
    assert cpu_cy.sp == cpu_py.sp


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def test_adc_immediate():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.a = 0x10
        _write_rom(cpu, bus, 0x69, 0x20)  # ADC #$20
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.a == 0x30
    assert cpu_cy.a == cpu_py.a
    assert cpu_cy.p == cpu_py.p


def test_sbc_immediate():
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.a = 0x05
        cpu.p |= cpu.FLAG_C
        _write_rom(cpu, bus, 0xE9, 0x01)  # SBC #$01
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.a == 0x04
    assert cpu_cy.a == cpu_py.a
    assert cpu_cy.p == cpu_py.p


# ---------------------------------------------------------------------------
# Interrupt
# ---------------------------------------------------------------------------

def test_nmi_triggers():
    """NMI pending triggers interrupt: PC changes, I flag set."""
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)
    for cpu, bus in [(cpu_py, bus_py), (cpu_cy, bus_cy)]:
        cpu.interrupts.nmi_pending = True
        _write_rom(cpu, bus, 0xEA)  # NOP
    pc_before_cy = cpu_cy.pc
    cpu_py.step_instruction()
    cpu_cy.step_instruction()
    assert cpu_cy.pc != pc_before_cy
    assert cpu_cy.p & cpu_cy.FLAG_I
    assert cpu_cy.pc == cpu_py.pc
    assert cpu_cy.p == cpu_py.p
    assert cpu_cy.sp == cpu_py.sp


# ---------------------------------------------------------------------------
# Flag constants
# ---------------------------------------------------------------------------

def test_flag_constants_accessible():
    cpu_py, cpu_cy, _, _ = _setup_cpus()
    ref = PureCPU(bus=cpu_cy.bus, interrupts=cpu_cy.interrupts)
    for name in ("FLAG_C", "FLAG_Z", "FLAG_I", "FLAG_D", "FLAG_B",
                 "FLAG_U", "FLAG_V", "FLAG_N",
                 "STACK_BASE", "VECTOR_NMI", "VECTOR_RESET", "VECTOR_IRQ"):
        assert getattr(cpu_cy, name) == getattr(ref, name), f"{name} mismatch"


# ---------------------------------------------------------------------------
# Synthetic trace parity (mandatory gate)
# ---------------------------------------------------------------------------

def test_synthetic_trace_parity():
    """Run a small ROM through both CPUs and verify trace entries match."""
    cpu_py, cpu_cy, bus_py, bus_cy = _setup_cpus(reset_pc=RAM_BASE)

    rom = bytearray([
        0xA9, 0x42,       # LDA #$42
        0x8D, 0x00, 0x03, # STA $0300
        0xAD, 0x00, 0x03, # LDA $0300
        0xC9, 0x42,       # CMP #$42 → Z=1
        0xF0, 0x06,       # BEQ +6 (taken)
        0xA9, 0x00,       # (skipped) LDA #0
        0xD0, 0xFD,       # BNE -3 (not taken)
        0xA9, 0x42,       # LDA #$42 again
        0x48,             # PHA
        0x69, 0x01,       # ADC #$01
        0x68,             # PLA
        0xE9, 0x02,       # SBC #$02
        0x4C, 0x00, 0x02, # JMP RAM_BASE (loop)
    ])

    for i, b in enumerate(rom):
        bus_py.write(RAM_BASE + i, b)
        bus_cy.write(RAM_BASE + i, b)

    cpu_py.set_trace_enabled(True)
    cpu_cy.set_trace_enabled(True)

    for _ in range(200):
        cpu_py.step_instruction()
        cpu_cy.step_instruction()

    log_py = cpu_py.get_trace_logger()
    log_cy = cpu_cy.get_trace_logger()
    assert log_py is not None and log_cy is not None
    assert len(log_py.entries) == len(log_cy.entries) == 200

    for i, (py_entry, cy_entry) in enumerate(
        zip(log_py.entries, log_cy.entries)
    ):
        for field in ("pc", "a", "x", "y", "p", "sp", "opcode", "mnemonic"):
            val_py = getattr(py_entry, field)
            val_cy = getattr(cy_entry, field)
            assert val_cy == val_py, (
                f"trace line {i}: {field} mismatch: "
                f"cy={val_cy}, py={val_py}"
            )
