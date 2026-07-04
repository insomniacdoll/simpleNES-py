"""Unit tests for CPU: instructions, flags, cycles."""

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
from tests.fixtures.nrom_sample import build_nrom_ines


@pytest.fixture
def cpu():
    """Build a CPU with full bus wiring."""
    image = RomParser.parse(bytes(build_nrom_ines(prg_banks=2)))
    mapper = NROMMapper(image)
    ppu_bus = PPUBus(mapper)
    ppt = PPU(bus=ppu_bus, interrupts=InterruptLines())
    apu = APU(interrupts=InterruptLines())
    bus = CPUBus(ppu=ppt, apu=apu, mapper=mapper,
                 controller1=Controller(), controller2=Controller(),
                 oam_dma_state=OAMDMAState())
    cpu = CPU(bus=bus, interrupts=InterruptLines())
    cpu.reset()
    return cpu


# ---- Load/Store ----

def test_lda_immediate(cpu):
    # Write LDA #$42 (A9 42) at PC
    cpu.bus.write(0x0200, 0xA9)
    cpu.bus.write(0x0201, 0x42)
    cpu.pc = 0x0200
    cycles = cpu.step_instruction()
    assert cpu.a == 0x42
    assert cycles == 2
    assert not (cpu.p & cpu.FLAG_Z)
    assert not (cpu.p & cpu.FLAG_N)


def test_lda_immediate_zero(cpu):
    cpu.bus.write(0x0200, 0xA9)
    cpu.bus.write(0x0201, 0x00)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0
    assert cpu.p & cpu.FLAG_Z


def test_lda_immediate_negative(cpu):
    cpu.bus.write(0x0200, 0xA9)
    cpu.bus.write(0x0201, 0x80)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x80
    assert cpu.p & cpu.FLAG_N


def test_lda_zp(cpu):
    cpu.bus.write(0x0200, 0xA5)  # LDA $10
    cpu.bus.write(0x0201, 0x10)
    cpu.bus.write(0x0010, 0x55)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x55
    assert not (cpu.p & cpu.FLAG_Z)


def test_lda_zpx(cpu):
    cpu.bus.write(0x0200, 0xB5)  # LDA $10,X
    cpu.bus.write(0x0201, 0x10)
    cpu.bus.write(0x0015, 0x77)  # $10 + X=5 = $15
    cpu.x = 5
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x77


def test_lda_zpx_wrap(cpu):
    cpu.bus.write(0x0200, 0xB5)  # LDA $FF,X
    cpu.bus.write(0x0201, 0xFF)
    cpu.bus.write(0x0004, 0x99)  # $FF + X=5 = $104, wrapped to $04
    cpu.x = 5
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x99


def test_lda_absolute(cpu):
    cpu.bus.write(0x0200, 0xAD)  # LDA $0400
    cpu.bus.write(0x0201, 0x00)
    cpu.bus.write(0x0202, 0x04)
    cpu.bus.write(0x0400, 0xAB)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0xAB


def test_lda_abx_page_cross(cpu):
    """LDA $02FF,X with X=2 -> $0301. Page crossed."""
    cpu.bus.write(0x0200, 0xBD)
    cpu.bus.write(0x0201, 0xFF)
    cpu.bus.write(0x0202, 0x02)
    cpu.bus.write(0x0301, 0xCD)
    cpu.x = 2
    cpu.pc = 0x0200
    cycles = cpu.step_instruction()
    assert cpu.a == 0xCD
    assert cycles == 5  # 4 + 1 page-cross


def test_sta_zp(cpu):
    cpu.a = 0x88
    cpu.bus.write(0x0200, 0x85)  # STA $20
    cpu.bus.write(0x0201, 0x20)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.bus.read(0x0020) == 0x88


def test_sta_absolute(cpu):
    cpu.a = 0x99
    cpu.bus.write(0x0200, 0x8D)
    cpu.bus.write(0x0201, 0x00)
    cpu.bus.write(0x0202, 0x04)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.bus.read(0x0400) == 0x99


# ---- Register transfer ----

def test_tax(cpu):
    cpu.a = 0x42
    cpu.bus.write(0x0200, 0xAA)  # TAX
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.x == 0x42
    assert not (cpu.p & cpu.FLAG_Z)


def test_tax_zero(cpu):
    cpu.a = 0
    cpu.bus.write(0x0200, 0xAA)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.x == 0
    assert cpu.p & cpu.FLAG_Z


def test_txs(cpu):
    """TXS does NOT set flags."""
    cpu.x = 0x10
    cpu.p = 0x00
    cpu.bus.write(0x0200, 0x9A)  # TXS
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.sp == 0x10
    assert cpu.p == 0x00  # flags unchanged


# ---- ADC / SBC ----

def test_adc_imm_basic(cpu):
    cpu.a = 0x10
    cpu.p = cpu.FLAG_C  # C=1
    cpu.bus.write(0x0200, 0x69)  # ADC #$20
    cpu.bus.write(0x0201, 0x20)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x31  # 0x10 + 0x20 + 1 = 0x31


def test_adc_carry_out(cpu):
    cpu.a = 0xFF
    cpu.p = cpu.FLAG_C
    cpu.bus.write(0x0200, 0x69)
    cpu.bus.write(0x0201, 0x01)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x01  # 0xFF + 0x01 + 1 = 0x101, wrapped to 0x01
    assert cpu.p & cpu.FLAG_C  # carry set


def test_adc_overflow(cpu):
    """0x50 + 0x50 = 0xA0 : signed overflow (80+80=160, >127)"""
    cpu.a = 0x50
    cpu.p = 0  # C=0
    cpu.bus.write(0x0200, 0x69)
    cpu.bus.write(0x0201, 0x50)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0xA0
    assert cpu.p & cpu.FLAG_V
    assert cpu.p & cpu.FLAG_N


def test_adc_no_overflow(cpu):
    """0x50 + 0x10 = 0x60 : no signed overflow"""
    cpu.a = 0x50
    cpu.p = 0
    cpu.bus.write(0x0200, 0x69)
    cpu.bus.write(0x0201, 0x10)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x60
    assert not (cpu.p & cpu.FLAG_V)


def test_sbc_basic(cpu):
    cpu.a = 0x10
    cpu.p = cpu.FLAG_C  # no borrow
    cpu.bus.write(0x0200, 0xE9)  # SBC #$01
    cpu.bus.write(0x0201, 0x01)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x0F  # 0x10 - 0x01 = 0x0F


def test_sbc_borrow(cpu):
    cpu.a = 0x00
    cpu.p = 0  # borrow active
    cpu.bus.write(0x0200, 0xE9)
    cpu.bus.write(0x0201, 0x01)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0xFE  # 0x00 - 0x01 - 1 = 0xFE
    assert not (cpu.p & cpu.FLAG_C)  # borrow set (carry clear)


# ---- CMP ----

def test_cmp_equal(cpu):
    cpu.a = 0x42
    cpu.bus.write(0x0200, 0xC9)  # CMP #$42
    cpu.bus.write(0x0201, 0x42)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.p & cpu.FLAG_Z
    assert cpu.p & cpu.FLAG_C


def test_cmp_greater(cpu):
    cpu.a = 0x50
    cpu.bus.write(0x0200, 0xC9)
    cpu.bus.write(0x0201, 0x30)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert not (cpu.p & cpu.FLAG_Z)
    assert cpu.p & cpu.FLAG_C


# ---- Branch ----

def test_beq_taken(cpu):
    cpu.p = cpu.FLAG_Z  # Z set
    cpu.bus.write(0x0200, 0xF0)  # BEQ +4
    cpu.bus.write(0x0201, 0x04)
    cpu.pc = 0x0200
    cycles = cpu.step_instruction()
    assert cpu.pc == 0x0206  # 0x0200 + 2 + 4
    assert cycles == 3  # branch taken, same page


def test_beq_not_taken(cpu):
    cpu.p = 0  # Z clear
    cpu.bus.write(0x0200, 0xF0)  # BEQ +4
    cpu.bus.write(0x0201, 0x04)
    cpu.pc = 0x0200
    cycles = cpu.step_instruction()
    assert cpu.pc == 0x0202  # not taken, PC advances past operand
    assert cycles == 2


def test_bne_backward(cpu):
    cpu.p = 0  # Z clear, so BNE taken
    cpu.bus.write(0x0200, 0xD0)  # BNE -6 (0xFA)
    cpu.bus.write(0x0201, 0xFA)
    cpu.pc = 0x0200
    cycles = cpu.step_instruction()
    assert cpu.pc == 0x01FC  # 0x0200 + 2 - 6 = 0x01FC
    assert cycles == 4  # branch taken, page crossed


# ---- JMP / JSR / RTS ----

def test_jmp_abs(cpu):
    cpu.bus.write(0x0200, 0x4C)
    cpu.bus.write(0x0201, 0x00)
    cpu.bus.write(0x0202, 0x03)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.pc == 0x0300


def test_jmp_ind_bug(cpu):
    """JMP ($04FF) reads high byte from $0400, not $0500 (6502 bug)."""
    cpu.bus.write(0x0200, 0x6C)
    cpu.bus.write(0x0201, 0xFF)
    cpu.bus.write(0x0202, 0x04)
    cpu.bus.write(0x04FF, 0x34)  # low byte
    cpu.bus.write(0x0400, 0x56)  # high byte (6502 bug!)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.pc == 0x5634


def test_jsr_rts(cpu):
    """JSR pushes return addr; RTS pulls + 1."""
    # JSR $C000
    cpu.bus.write(0x0200, 0x20)
    cpu.bus.write(0x0201, 0x00)
    cpu.bus.write(0x0202, 0x03)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.pc == 0x0300
    # Stack should have return addr - 1 = 0x0202
    assert cpu.sp == 0xFB  # pushed 2 bytes: FD -> FC -> FB

    # RTS at $C000
    cpu.bus.write(0x0300, 0x60)
    cpu.pc = 0x0300
    cpu.step_instruction()
    assert cpu.pc == 0x0203  # 0x0202 + 1


# ---- Stack ----

def test_pha_pla(cpu):
    cpu.a = 0xAB
    cpu.bus.write(0x0200, 0x48)  # PHA
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.sp == 0xFC
    cpu.a = 0x00
    cpu.bus.write(0x0201, 0x68)  # PLA
    cpu.pc = 0x0201
    cpu.step_instruction()
    assert cpu.a == 0xAB


def test_php_plp(cpu):
    cpu.p = 0x55
    cpu.bus.write(0x0200, 0x08)  # PHP
    cpu.pc = 0x0200
    cpu.step_instruction()
    cpu.p = 0x00
    cpu.bus.write(0x0201, 0x28)  # PLP
    cpu.pc = 0x0201
    cpu.step_instruction()
    # PHP pushes (p | B | U), PLP restores raw stack value
    assert cpu.p == (0x55 | cpu.FLAG_B | cpu.FLAG_U)


# ---- Flag instructions ----

def test_sec(cpu):
    cpu.bus.write(0x0200, 0x38)  # SEC
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.p & cpu.FLAG_C


def test_cli_sei(cpu):
    cpu.bus.write(0x0200, 0x58)  # CLI
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert not (cpu.p & cpu.FLAG_I)
    cpu.bus.write(0x0201, 0x78)  # SEI
    cpu.pc = 0x0201
    cpu.step_instruction()
    assert cpu.p & cpu.FLAG_I


# ---- INC/DEC ----

def test_inx(cpu):
    cpu.x = 0x10
    cpu.bus.write(0x0200, 0xE8)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.x == 0x11


def test_dex_wrap(cpu):
    cpu.x = 0x00
    cpu.bus.write(0x0200, 0xCA)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.x == 0xFF
    assert cpu.p & cpu.FLAG_N


# ---- Shift ----

def test_asl_acc(cpu):
    cpu.a = 0x81  # 1000_0001
    cpu.bus.write(0x0200, 0x0A)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x02
    assert cpu.p & cpu.FLAG_C  # bit 7 shifted into carry


def test_lsr_acc(cpu):
    cpu.a = 0x03  # 0000_0011
    cpu.bus.write(0x0200, 0x4A)
    cpu.pc = 0x0200
    cpu.step_instruction()
    assert cpu.a == 0x01
    assert cpu.p & cpu.FLAG_C  # bit 0 shifted into carry


# ---- Decimal mode (2A03: no effect) ----

def test_adc_decimal_ignored(cpu):
    """On 2A03, D flag does not affect ADC/SBC."""
    cpu.a = 0x05
    cpu.p = cpu.FLAG_C | cpu.FLAG_D  # C=1, D=1
    cpu.bus.write(0x0200, 0x69)  # ADC #$05
    cpu.bus.write(0x0201, 0x05)
    cpu.pc = 0x0200
    cpu.step_instruction()
    # Binary: 5 + 5 + 1 = 11 (0x0B), not BCD 11
    assert cpu.a == 0x0B


# ---- Trace ----

def test_trace_logger(cpu):
    cpu.set_trace_enabled(True)
    cpu.bus.write(0x0200, 0xA9)  # LDA #$42
    cpu.bus.write(0x0201, 0x42)
    cpu.bus.write(0x0202, 0xAA)  # TAX
    cpu.pc = 0x0200
    cpu.step_instruction()
    cpu.step_instruction()
    logger = cpu.get_trace_logger()
    assert logger is not None
    assert len(logger.entries) == 2
    assert logger.entries[0].mnemonic == "LDA"
    assert logger.entries[0].pc == 0x0200
    assert logger.entries[1].mnemonic == "TAX"
    assert logger.entries[1].pc == 0x0202


def test_trace_callback_auto_enables_trace(cpu):
    """set_trace_callback() auto-enables the trace logger and fires on each step."""
    calls = []

    cpu.set_trace_callback(calls.append)

    cpu.bus.write(0x0200, 0xA9)  # LDA #$42
    cpu.bus.write(0x0201, 0x42)
    cpu.pc = 0x0200
    cpu.step_instruction()

    assert len(calls) == 1
    assert calls[0].mnemonic == "LDA"
    assert calls[0].pc == 0x0200
    assert calls[0].a == 0x00  # pre-instruction snapshot

    logger = cpu.get_trace_logger()
    assert logger is not None
    assert logger.enabled is True


def test_trace_callback_can_be_disabled(cpu):
    """Setting callback to None stops invocations."""
    calls = []
    cpu.set_trace_callback(calls.append)
    cpu.set_trace_callback(None)

    cpu.bus.write(0x0200, 0xEA)  # NOP
    cpu.pc = 0x0200
    cpu.step_instruction()

    assert calls == []
