"""6502 opcode dispatch table: all 151 official instruction handlers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Callable

from simplenes.errors import IllegalOpcodeError

if TYPE_CHECKING:
    from simplenes.cpu.cpu import CPU


class AddrMode(IntEnum):
    IMP = 0
    ACC = 1
    IMM = 2
    ZP = 3
    ZPX = 4
    ZPY = 5
    REL = 6
    ABS = 7
    ABX = 8
    ABY = 9
    IND = 10
    IDX = 11
    IDY = 12


Handler = Callable[["CPU"], int]


@dataclass(frozen=True, slots=True)
class Opcode:
    """Static metadata for one 6502 opcode."""
    mnemonic: str
    mode: AddrMode
    length: int
    base_cycles: int
    handler: Handler
    page_cross_penalty: bool = False


# ===================== Address resolution helpers =====================
# These consume operands from PC via cpu._read_pc()


def _abs(cpu: "CPU") -> int:
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    return (hi << 8) | lo


def _abx(cpu: "CPU") -> int:
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    base = (hi << 8) | lo
    addr = base + cpu.x
    cpu._page_crossed = (addr >> 8) != hi
    return addr & 0xFFFF


def _aby(cpu: "CPU") -> int:
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    base = (hi << 8) | lo
    addr = base + cpu.y
    cpu._page_crossed = (addr >> 8) != hi
    return addr & 0xFFFF


def _abx_rmw(cpu: "CPU") -> int:
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    return (((hi << 8) | lo) + cpu.x) & 0xFFFF


def _idx(cpu: "CPU") -> int:
    zp = (cpu._read_pc() + cpu.x) & 0xFF
    lo = cpu._read(zp)
    hi = cpu._read((zp + 1) & 0xFF)
    return (hi << 8) | lo


def _idy(cpu: "CPU") -> int:
    zp = cpu._read_pc()
    lo = cpu._read(zp)
    hi = cpu._read((zp + 1) & 0xFF)
    base = (hi << 8) | lo
    addr = base + cpu.y
    cpu._page_crossed = (addr >> 8) != hi
    return addr & 0xFFFF


def _idy_npc(cpu: "CPU") -> int:
    zp = cpu._read_pc()
    lo = cpu._read(zp)
    hi = cpu._read((zp + 1) & 0xFF)
    return (((hi << 8) | lo) + cpu.y) & 0xFFFF


def _branch(cpu: "CPU", cond: bool) -> int:
    offset = cpu._read_pc()
    if not cond:
        return 2
    old_pc = cpu.pc
    if offset < 0x80:
        cpu.pc = (cpu.pc + offset) & 0xFFFF
    else:
        cpu.pc = (cpu.pc + offset - 0x100) & 0xFFFF
    if (old_pc ^ cpu.pc) & 0xFF00:
        return 4
    return 3

def _lda_imm(cpu: 'CPU') -> int:
    cpu.a = cpu._read_pc()
    cpu._set_nz(cpu.a)
    return 2

def _lda_zp(cpu: 'CPU') -> int:
    cpu.a = cpu._read(cpu._read_pc() & 0xFF)
    cpu._set_nz(cpu.a)
    return 3

def _lda_zpx(cpu: 'CPU') -> int:
    cpu.a = cpu._read((cpu._read_pc() + cpu.x) & 0xFF)
    cpu._set_nz(cpu.a)
    return 4

def _lda_abs(cpu: 'CPU') -> int:
    cpu.a = cpu._read(_abs(cpu))
    cpu._set_nz(cpu.a)
    return 4

def _lda_abx(cpu: 'CPU') -> int:
    cpu.a = cpu._read(_abx(cpu))
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _lda_aby(cpu: 'CPU') -> int:
    cpu.a = cpu._read(_aby(cpu))
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _lda_idx(cpu: 'CPU') -> int:
    cpu.a = cpu._read(_idx(cpu))
    cpu._set_nz(cpu.a)
    return 6

def _lda_idy(cpu: 'CPU') -> int:
    cpu.a = cpu._read(_idy(cpu))
    cpu._set_nz(cpu.a)
    return 5 + cpu._page_crossed

def _ldx_imm(cpu: 'CPU') -> int:
    cpu.x = cpu._read_pc()
    cpu._set_nz(cpu.x)
    return 2

def _ldx_zp(cpu: 'CPU') -> int:
    cpu.x = cpu._read(cpu._read_pc() & 0xFF)
    cpu._set_nz(cpu.x)
    return 3

def _ldx_zpy(cpu: 'CPU') -> int:
    cpu.x = cpu._read((cpu._read_pc() + cpu.y) & 0xFF)
    cpu._set_nz(cpu.x)
    return 4

def _ldx_abs(cpu: 'CPU') -> int:
    cpu.x = cpu._read(_abs(cpu))
    cpu._set_nz(cpu.x)
    return 4

def _ldx_aby(cpu: 'CPU') -> int:
    cpu.x = cpu._read(_aby(cpu))
    cpu._set_nz(cpu.x)
    return 4 + cpu._page_crossed

def _ldy_imm(cpu: 'CPU') -> int:
    cpu.y = cpu._read_pc()
    cpu._set_nz(cpu.y)
    return 2

def _ldy_zp(cpu: 'CPU') -> int:
    cpu.y = cpu._read(cpu._read_pc() & 0xFF)
    cpu._set_nz(cpu.y)
    return 3

def _ldy_zpx(cpu: 'CPU') -> int:
    cpu.y = cpu._read((cpu._read_pc() + cpu.x) & 0xFF)
    cpu._set_nz(cpu.y)
    return 4

def _ldy_abs(cpu: 'CPU') -> int:
    cpu.y = cpu._read(_abs(cpu))
    cpu._set_nz(cpu.y)
    return 4

def _ldy_abx(cpu: 'CPU') -> int:
    cpu.y = cpu._read(_abx(cpu))
    cpu._set_nz(cpu.y)
    return 4 + cpu._page_crossed

def _sta_zp(cpu: 'CPU') -> int:
    cpu._write(cpu._read_pc() & 0xFF, cpu.a)
    return 3

def _sta_zpx(cpu: 'CPU') -> int:
    cpu._write((cpu._read_pc() + cpu.x) & 0xFF, cpu.a)
    return 4

def _sta_abs(cpu: 'CPU') -> int:
    cpu._write(_abs(cpu), cpu.a)
    return 4

def _sta_abx(cpu: 'CPU') -> int:
    addr = _abx(cpu)
    cpu._read(addr)
    cpu._write(addr, cpu.a)
    return 5

def _sta_aby(cpu: 'CPU') -> int:
    addr = _aby(cpu)
    cpu._read(addr)
    cpu._write(addr, cpu.a)
    return 5

def _sta_idx(cpu: 'CPU') -> int:
    cpu._write(_idx(cpu), cpu.a)
    return 6

def _sta_idy(cpu: 'CPU') -> int:
    addr = _idy_npc(cpu)
    cpu._read(addr)
    cpu._write(addr, cpu.a)
    return 6

def _stx_zp(cpu: 'CPU') -> int:
    cpu._write(cpu._read_pc() & 0xFF, cpu.x)
    return 3

def _stx_zpy(cpu: 'CPU') -> int:
    cpu._write((cpu._read_pc() + cpu.y) & 0xFF, cpu.x)
    return 4

def _stx_abs(cpu: 'CPU') -> int:
    cpu._write(_abs(cpu), cpu.x)
    return 4

def _sty_zp(cpu: 'CPU') -> int:
    cpu._write(cpu._read_pc() & 0xFF, cpu.y)
    return 3

def _sty_zpx(cpu: 'CPU') -> int:
    cpu._write((cpu._read_pc() + cpu.x) & 0xFF, cpu.y)
    return 4

def _sty_abs(cpu: 'CPU') -> int:
    cpu._write(_abs(cpu), cpu.y)
    return 4

def _adc_imm(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read_pc())
    return 2

def _adc_zp(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read(cpu._read_pc() & 0xFF))
    return 3

def _adc_zpx(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read((cpu._read_pc() + cpu.x) & 0xFF))
    return 4

def _adc_abs(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read(_abs(cpu)))
    return 4

def _adc_abx(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read(_abx(cpu)))
    return 4 + cpu._page_crossed

def _adc_aby(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read(_aby(cpu)))
    return 4 + cpu._page_crossed

def _adc_idx(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read(_idx(cpu)))
    return 6

def _adc_idy(cpu: 'CPU') -> int:
    cpu._add_with_carry(cpu._read(_idy(cpu)))
    return 5 + cpu._page_crossed

def _sbc_imm(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read_pc())
    return 2

def _sbc_zp(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read(cpu._read_pc() & 0xFF))
    return 3

def _sbc_zpx(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read((cpu._read_pc() + cpu.x) & 0xFF))
    return 4

def _sbc_abs(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read(_abs(cpu)))
    return 4

def _sbc_abx(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read(_abx(cpu)))
    return 4 + cpu._page_crossed

def _sbc_aby(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read(_aby(cpu)))
    return 4 + cpu._page_crossed

def _sbc_idx(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read(_idx(cpu)))
    return 6

def _sbc_idy(cpu: 'CPU') -> int:
    cpu._sub_with_carry(cpu._read(_idy(cpu)))
    return 5 + cpu._page_crossed

def _inc_zp(cpu: 'CPU') -> int:
    addr = cpu._read_pc() & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v + 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 5

def _inc_zpx(cpu: 'CPU') -> int:
    addr = (cpu._read_pc() + cpu.x) & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v + 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _inc_abs(cpu: 'CPU') -> int:
    addr = _abs(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v + 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _inc_abx(cpu: 'CPU') -> int:
    addr = _abx_rmw(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v + 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 7

def _dec_zp(cpu: 'CPU') -> int:
    addr = cpu._read_pc() & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v - 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 5

def _dec_zpx(cpu: 'CPU') -> int:
    addr = (cpu._read_pc() + cpu.x) & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v - 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _dec_abs(cpu: 'CPU') -> int:
    addr = _abs(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v - 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _dec_abx(cpu: 'CPU') -> int:
    addr = _abx_rmw(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    v = (v - 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 7

def _inx_imp(cpu: 'CPU') -> int:
    cpu.x = (cpu.x + 1) & 0xFF
    cpu._set_nz(cpu.x)
    return 2

def _iny_imp(cpu: 'CPU') -> int:
    cpu.y = (cpu.y + 1) & 0xFF
    cpu._set_nz(cpu.y)
    return 2

def _dex_imp(cpu: 'CPU') -> int:
    cpu.x = (cpu.x - 1) & 0xFF
    cpu._set_nz(cpu.x)
    return 2

def _dey_imp(cpu: 'CPU') -> int:
    cpu.y = (cpu.y - 1) & 0xFF
    cpu._set_nz(cpu.y)
    return 2

def _and_imm(cpu: 'CPU') -> int:
    cpu.a &= cpu._read_pc()
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 2

def _and_zp(cpu: 'CPU') -> int:
    cpu.a &= cpu._read(cpu._read_pc() & 0xFF)
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 3

def _and_zpx(cpu: 'CPU') -> int:
    cpu.a &= cpu._read((cpu._read_pc() + cpu.x) & 0xFF)
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4

def _and_abs(cpu: 'CPU') -> int:
    cpu.a &= cpu._read(_abs(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4

def _and_abx(cpu: 'CPU') -> int:
    cpu.a &= cpu._read(_abx(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _and_aby(cpu: 'CPU') -> int:
    cpu.a &= cpu._read(_aby(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _and_idx(cpu: 'CPU') -> int:
    cpu.a &= cpu._read(_idx(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 6

def _and_idy(cpu: 'CPU') -> int:
    cpu.a &= cpu._read(_idy(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 5 + cpu._page_crossed

def _ora_imm(cpu: 'CPU') -> int:
    cpu.a |= cpu._read_pc()
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 2

def _ora_zp(cpu: 'CPU') -> int:
    cpu.a |= cpu._read(cpu._read_pc() & 0xFF)
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 3

def _ora_zpx(cpu: 'CPU') -> int:
    cpu.a |= cpu._read((cpu._read_pc() + cpu.x) & 0xFF)
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4

def _ora_abs(cpu: 'CPU') -> int:
    cpu.a |= cpu._read(_abs(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4

def _ora_abx(cpu: 'CPU') -> int:
    cpu.a |= cpu._read(_abx(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _ora_aby(cpu: 'CPU') -> int:
    cpu.a |= cpu._read(_aby(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _ora_idx(cpu: 'CPU') -> int:
    cpu.a |= cpu._read(_idx(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 6

def _ora_idy(cpu: 'CPU') -> int:
    cpu.a |= cpu._read(_idy(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 5 + cpu._page_crossed

def _eor_imm(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read_pc()
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 2

def _eor_zp(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read(cpu._read_pc() & 0xFF)
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 3

def _eor_zpx(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read((cpu._read_pc() + cpu.x) & 0xFF)
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4

def _eor_abs(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read(_abs(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4

def _eor_abx(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read(_abx(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _eor_aby(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read(_aby(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 4 + cpu._page_crossed

def _eor_idx(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read(_idx(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 6

def _eor_idy(cpu: 'CPU') -> int:
    cpu.a ^= cpu._read(_idy(cpu))
    cpu.a &= 0xFF
    cpu._set_nz(cpu.a)
    return 5 + cpu._page_crossed

def _asl_acc(cpu: 'CPU') -> int:
    cpu.p = (cpu.p & ~cpu.FLAG_C) | ((cpu.a >> 7) & 1)
    cpu.a = (cpu.a << 1) & 0xFF
    cpu._set_nz(cpu.a)
    return 2

def _asl_zp(cpu: 'CPU') -> int:
    addr = cpu._read_pc() & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = (v << 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 5

def _asl_zpx(cpu: 'CPU') -> int:
    addr = (cpu._read_pc() + cpu.x) & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = (v << 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _asl_abs(cpu: 'CPU') -> int:
    addr = _abs(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = (v << 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _asl_abx(cpu: 'CPU') -> int:
    addr = _abx_rmw(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = (v << 1) & 0xFF
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 7

def _lsr_zp(cpu: 'CPU') -> int:
    addr = cpu._read_pc() & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = v >> 1
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 5

def _lsr_zpx(cpu: 'CPU') -> int:
    addr = (cpu._read_pc() + cpu.x) & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = v >> 1
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _lsr_abs(cpu: 'CPU') -> int:
    addr = _abs(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = v >> 1
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _lsr_abx(cpu: 'CPU') -> int:
    addr = _abx_rmw(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = v >> 1
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 7

def _lsr_acc(cpu: 'CPU') -> int:
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (cpu.a & 1)
    cpu.a >>= 1
    cpu._set_nz(cpu.a)
    return 2

def _rol_acc(cpu: 'CPU') -> int:
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | ((cpu.a >> 7) & 1)
    cpu.a = ((cpu.a << 1) & 0xFF) | c
    cpu._set_nz(cpu.a)
    return 2

def _ror_acc(cpu: 'CPU') -> int:
    c = (cpu.p & cpu.FLAG_C) << 7
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (cpu.a & 1)
    cpu.a = (cpu.a >> 1) | c
    cpu._set_nz(cpu.a)
    return 2

def _rol_zp(cpu: 'CPU') -> int:
    addr = cpu._read_pc() & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = ((v << 1) & 0xFF) | c
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 5

def _rol_zpx(cpu: 'CPU') -> int:
    addr = (cpu._read_pc() + cpu.x) & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = ((v << 1) & 0xFF) | c
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _rol_abs(cpu: 'CPU') -> int:
    addr = _abs(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = ((v << 1) & 0xFF) | c
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _rol_abx(cpu: 'CPU') -> int:
    addr = _abx_rmw(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | (v >> 7) & 1
    v = ((v << 1) & 0xFF) | c
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 7

def _ror_zp(cpu: 'CPU') -> int:
    addr = cpu._read_pc() & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = (v >> 1) | (c << 7)
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 5

def _ror_zpx(cpu: 'CPU') -> int:
    addr = (cpu._read_pc() + cpu.x) & 0xFF
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = (v >> 1) | (c << 7)
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _ror_abs(cpu: 'CPU') -> int:
    addr = _abs(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = (v >> 1) | (c << 7)
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 6

def _ror_abx(cpu: 'CPU') -> int:
    addr = _abx_rmw(cpu)
    v = cpu._read(addr)
    cpu._write(addr, v)
    c = cpu.p & cpu.FLAG_C
    cpu.p = (cpu.p & ~cpu.FLAG_C) | v & 1
    v = (v >> 1) | (c << 7)
    cpu._write(addr, v)
    cpu._set_nz(v)
    return 7

def _bit_zp(cpu: 'CPU') -> int:
    v = cpu._read(cpu._read_pc() & 0xFF)
    z = cpu.FLAG_Z if (cpu.a & v) == 0 else 0
    nv = v & (cpu.FLAG_N | cpu.FLAG_V)
    cpu.p = (cpu.p & ~(cpu.FLAG_Z|cpu.FLAG_N|cpu.FLAG_V)) | z | nv
    return 3

def _bit_abs(cpu: 'CPU') -> int:
    v = cpu._read(_abs(cpu))
    z = cpu.FLAG_Z if (cpu.a & v) == 0 else 0
    nv = v & (cpu.FLAG_N | cpu.FLAG_V)
    cpu.p = (cpu.p & ~(cpu.FLAG_Z|cpu.FLAG_N|cpu.FLAG_V)) | z | nv
    return 4

def _cmp_imm(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read_pc())
    return 2

def _cmp_zp(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read(cpu._read_pc() & 0xFF))
    return 3

def _cmp_zpx(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read((cpu._read_pc() + cpu.x) & 0xFF))
    return 4

def _cmp_abs(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read(_abs(cpu)))
    return 4

def _cmp_abx(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read(_abx(cpu)))
    return 4 + cpu._page_crossed

def _cmp_aby(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read(_aby(cpu)))
    return 4 + cpu._page_crossed

def _cmp_idx(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read(_idx(cpu)))
    return 6

def _cmp_idy(cpu: 'CPU') -> int:
    cpu._compare(cpu.a, cpu._read(_idy(cpu)))
    return 5 + cpu._page_crossed

def _cpx_imm(cpu: 'CPU') -> int:
    cpu._compare(cpu.x, cpu._read_pc())
    return 2

def _cpx_zp(cpu: 'CPU') -> int:
    cpu._compare(cpu.x, cpu._read(cpu._read_pc() & 0xFF))
    return 3

def _cpx_abs(cpu: 'CPU') -> int:
    cpu._compare(cpu.x, cpu._read(_abs(cpu)))
    return 4

def _cpy_imm(cpu: 'CPU') -> int:
    cpu._compare(cpu.y, cpu._read_pc())
    return 2

def _cpy_zp(cpu: 'CPU') -> int:
    cpu._compare(cpu.y, cpu._read(cpu._read_pc() & 0xFF))
    return 3

def _cpy_abs(cpu: 'CPU') -> int:
    cpu._compare(cpu.y, cpu._read(_abs(cpu)))
    return 4

def _bpl_rel(cpu: 'CPU') -> int:
    return _branch(cpu, not (cpu.p & cpu.FLAG_N))

def _bmi_rel(cpu: 'CPU') -> int:
    return _branch(cpu, bool(cpu.p & cpu.FLAG_N))

def _bvc_rel(cpu: 'CPU') -> int:
    return _branch(cpu, not (cpu.p & cpu.FLAG_V))

def _bvs_rel(cpu: 'CPU') -> int:
    return _branch(cpu, bool(cpu.p & cpu.FLAG_V))

def _bcc_rel(cpu: 'CPU') -> int:
    return _branch(cpu, not (cpu.p & cpu.FLAG_C))

def _bcs_rel(cpu: 'CPU') -> int:
    return _branch(cpu, bool(cpu.p & cpu.FLAG_C))

def _bne_rel(cpu: 'CPU') -> int:
    return _branch(cpu, not (cpu.p & cpu.FLAG_Z))

def _beq_rel(cpu: 'CPU') -> int:
    return _branch(cpu, bool(cpu.p & cpu.FLAG_Z))

def _jmp_abs(cpu: 'CPU') -> int:
    cpu.pc = _abs(cpu)
    return 3

def _jmp_ind(cpu: 'CPU') -> int:
    lo=cpu._read_pc()
    hi=cpu._read_pc()
    ptr=(hi<<8)|lo
    alo=cpu._read(ptr)
    ahi=cpu._read(ptr&0xFF00 if lo==0xFF else ptr+1)
    cpu.pc=(ahi<<8)|alo
    return 5

def _jsr_abs(cpu: 'CPU') -> int:
    lo=cpu._read_pc()
    hi=cpu._read_pc()
    cpu._push_word((cpu.pc-1)&0xFFFF)
    cpu.pc=(hi<<8)|lo
    return 6

def _rts_imp(cpu: 'CPU') -> int:
    cpu.pc = (cpu._pull_word() + 1) & 0xFFFF
    return 6

def _pha_imp(cpu: 'CPU') -> int:
    cpu._push(cpu.a)
    return 3

def _php_imp(cpu: 'CPU') -> int:
    cpu._push(cpu.p | cpu.FLAG_B | cpu.FLAG_U)
    return 3

def _pla_imp(cpu: 'CPU') -> int:
    cpu.a = cpu._pull()
    cpu._set_nz(cpu.a)
    return 4

def _plp_imp(cpu: 'CPU') -> int:
    cpu.p = cpu._pull()
    return 4

def _tax_imp(cpu: 'CPU') -> int:
    cpu.x = cpu.a
    cpu._set_nz(cpu.x)
    return 2

def _tay_imp(cpu: 'CPU') -> int:
    cpu.y = cpu.a
    cpu._set_nz(cpu.y)
    return 2

def _txa_imp(cpu: 'CPU') -> int:
    cpu.a = cpu.x
    cpu._set_nz(cpu.a)
    return 2

def _tya_imp(cpu: 'CPU') -> int:
    cpu.a = cpu.y
    cpu._set_nz(cpu.a)
    return 2

def _tsx_imp(cpu: 'CPU') -> int:
    cpu.x = cpu.sp
    cpu._set_nz(cpu.x)
    return 2

def _txs_imp(cpu: 'CPU') -> int:
    cpu.sp = cpu.x
    return 2

def _clc_imp(cpu: 'CPU') -> int:
    cpu.p &= ~cpu.FLAG_C
    return 2

def _sec_imp(cpu: 'CPU') -> int:
    cpu.p |= cpu.FLAG_C
    return 2

def _cli_imp(cpu: 'CPU') -> int:
    cpu.p &= ~cpu.FLAG_I
    return 2

def _sei_imp(cpu: 'CPU') -> int:
    cpu.p |= cpu.FLAG_I
    return 2

def _clv_imp(cpu: 'CPU') -> int:
    cpu.p &= ~cpu.FLAG_V
    return 2

def _cld_imp(cpu: 'CPU') -> int:
    cpu.p &= ~cpu.FLAG_D
    return 2

def _sed_imp(cpu: 'CPU') -> int:
    cpu.p |= cpu.FLAG_D
    return 2

def _brk_imp(cpu: 'CPU') -> int:
    cpu._read_pc()
    cpu._push_word(cpu.pc)
    cpu._push(cpu.p|cpu.FLAG_B|cpu.FLAG_U)
    cpu.p|=cpu.FLAG_I
    cpu.pc=cpu._read_word(cpu.VECTOR_IRQ)
    return 7

def _rti_imp(cpu: 'CPU') -> int:
    cpu.p = cpu._pull()
    cpu.pc = cpu._pull_word()
    return 6

def _nop_imp(cpu: 'CPU') -> int:
    pass
    return 2

def _illegal(cpu: 'CPU') -> int:
    raise IllegalOpcodeError(cpu._last_opcode, cpu._last_pc)


# ===================== 256-entry opcode table =====================

_table: list[Opcode | None] = [None] * 256

_table[0x00] = Opcode(mnemonic="BRK", mode=AddrMode.IMP, length=2, base_cycles=7, handler=_brk_imp, page_cross_penalty=False)
_table[0x01] = Opcode(mnemonic="ORA", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_ora_idx, page_cross_penalty=False)
_table[0x02] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x03] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x04] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x05] = Opcode(mnemonic="ORA", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_ora_zp, page_cross_penalty=False)
_table[0x06] = Opcode(mnemonic="ASL", mode=AddrMode.ZP, length=2, base_cycles=5, handler=_asl_zp, page_cross_penalty=False)
_table[0x07] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x08] = Opcode(mnemonic="PHP", mode=AddrMode.IMP, length=1, base_cycles=3, handler=_php_imp, page_cross_penalty=False)
_table[0x09] = Opcode(mnemonic="ORA", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_ora_imm, page_cross_penalty=False)
_table[0x0A] = Opcode(mnemonic="ASL", mode=AddrMode.ACC, length=1, base_cycles=2, handler=_asl_acc, page_cross_penalty=False)
_table[0x0B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x0C] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x0D] = Opcode(mnemonic="ORA", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_ora_abs, page_cross_penalty=False)
_table[0x0E] = Opcode(mnemonic="ASL", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_asl_abs, page_cross_penalty=False)
_table[0x0F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x10] = Opcode(mnemonic="BPL", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bpl_rel, page_cross_penalty=False)
_table[0x11] = Opcode(mnemonic="ORA", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_ora_idy, page_cross_penalty=True)
_table[0x12] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x13] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x14] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x15] = Opcode(mnemonic="ORA", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_ora_zpx, page_cross_penalty=False)
_table[0x16] = Opcode(mnemonic="ASL", mode=AddrMode.ZPX, length=2, base_cycles=6, handler=_asl_zpx, page_cross_penalty=False)
_table[0x17] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x18] = Opcode(mnemonic="CLC", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_clc_imp, page_cross_penalty=False)
_table[0x19] = Opcode(mnemonic="ORA", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_ora_aby, page_cross_penalty=True)
_table[0x1A] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x1B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x1C] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x1D] = Opcode(mnemonic="ORA", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_ora_abx, page_cross_penalty=True)
_table[0x1E] = Opcode(mnemonic="ASL", mode=AddrMode.ABX, length=3, base_cycles=7, handler=_asl_abx, page_cross_penalty=False)
_table[0x1F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x20] = Opcode(mnemonic="JSR", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_jsr_abs, page_cross_penalty=False)
_table[0x21] = Opcode(mnemonic="AND", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_and_idx, page_cross_penalty=False)
_table[0x22] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x23] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x24] = Opcode(mnemonic="BIT", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_bit_zp, page_cross_penalty=False)
_table[0x25] = Opcode(mnemonic="AND", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_and_zp, page_cross_penalty=False)
_table[0x26] = Opcode(mnemonic="ROL", mode=AddrMode.ZP, length=2, base_cycles=5, handler=_rol_zp, page_cross_penalty=False)
_table[0x27] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x28] = Opcode(mnemonic="PLP", mode=AddrMode.IMP, length=1, base_cycles=4, handler=_plp_imp, page_cross_penalty=False)
_table[0x29] = Opcode(mnemonic="AND", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_and_imm, page_cross_penalty=False)
_table[0x2A] = Opcode(mnemonic="ROL", mode=AddrMode.ACC, length=1, base_cycles=2, handler=_rol_acc, page_cross_penalty=False)
_table[0x2B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x2C] = Opcode(mnemonic="BIT", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_bit_abs, page_cross_penalty=False)
_table[0x2D] = Opcode(mnemonic="AND", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_and_abs, page_cross_penalty=False)
_table[0x2E] = Opcode(mnemonic="ROL", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_rol_abs, page_cross_penalty=False)
_table[0x2F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x30] = Opcode(mnemonic="BMI", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bmi_rel, page_cross_penalty=False)
_table[0x31] = Opcode(mnemonic="AND", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_and_idy, page_cross_penalty=True)
_table[0x32] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x33] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x34] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x35] = Opcode(mnemonic="AND", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_and_zpx, page_cross_penalty=False)
_table[0x36] = Opcode(mnemonic="ROL", mode=AddrMode.ZPX, length=2, base_cycles=6, handler=_rol_zpx, page_cross_penalty=False)
_table[0x37] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x38] = Opcode(mnemonic="SEC", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_sec_imp, page_cross_penalty=False)
_table[0x39] = Opcode(mnemonic="AND", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_and_aby, page_cross_penalty=True)
_table[0x3A] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x3B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x3C] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x3D] = Opcode(mnemonic="AND", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_and_abx, page_cross_penalty=True)
_table[0x3E] = Opcode(mnemonic="ROL", mode=AddrMode.ABX, length=3, base_cycles=7, handler=_rol_abx, page_cross_penalty=False)
_table[0x3F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x40] = Opcode(mnemonic="RTI", mode=AddrMode.IMP, length=1, base_cycles=6, handler=_rti_imp, page_cross_penalty=False)
_table[0x41] = Opcode(mnemonic="EOR", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_eor_idx, page_cross_penalty=False)
_table[0x42] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x43] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x44] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x45] = Opcode(mnemonic="EOR", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_eor_zp, page_cross_penalty=False)
_table[0x46] = Opcode(mnemonic="LSR", mode=AddrMode.ZP, length=2, base_cycles=5, handler=_lsr_zp, page_cross_penalty=False)
_table[0x47] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x48] = Opcode(mnemonic="PHA", mode=AddrMode.IMP, length=1, base_cycles=3, handler=_pha_imp, page_cross_penalty=False)
_table[0x49] = Opcode(mnemonic="EOR", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_eor_imm, page_cross_penalty=False)
_table[0x4A] = Opcode(mnemonic="LSR", mode=AddrMode.ACC, length=1, base_cycles=2, handler=_lsr_acc, page_cross_penalty=False)
_table[0x4B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x4C] = Opcode(mnemonic="JMP", mode=AddrMode.ABS, length=3, base_cycles=3, handler=_jmp_abs, page_cross_penalty=False)
_table[0x4D] = Opcode(mnemonic="EOR", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_eor_abs, page_cross_penalty=False)
_table[0x4E] = Opcode(mnemonic="LSR", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_lsr_abs, page_cross_penalty=False)
_table[0x4F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x50] = Opcode(mnemonic="BVC", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bvc_rel, page_cross_penalty=False)
_table[0x51] = Opcode(mnemonic="EOR", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_eor_idy, page_cross_penalty=True)
_table[0x52] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x53] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x54] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x55] = Opcode(mnemonic="EOR", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_eor_zpx, page_cross_penalty=False)
_table[0x56] = Opcode(mnemonic="LSR", mode=AddrMode.ZPX, length=2, base_cycles=6, handler=_lsr_zpx, page_cross_penalty=False)
_table[0x57] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x58] = Opcode(mnemonic="CLI", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_cli_imp, page_cross_penalty=False)
_table[0x59] = Opcode(mnemonic="EOR", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_eor_aby, page_cross_penalty=True)
_table[0x5A] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x5B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x5C] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x5D] = Opcode(mnemonic="EOR", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_eor_abx, page_cross_penalty=True)
_table[0x5E] = Opcode(mnemonic="LSR", mode=AddrMode.ABX, length=3, base_cycles=7, handler=_lsr_abx, page_cross_penalty=False)
_table[0x5F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x60] = Opcode(mnemonic="RTS", mode=AddrMode.IMP, length=1, base_cycles=6, handler=_rts_imp, page_cross_penalty=False)
_table[0x61] = Opcode(mnemonic="ADC", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_adc_idx, page_cross_penalty=False)
_table[0x62] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x63] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x64] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x65] = Opcode(mnemonic="ADC", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_adc_zp, page_cross_penalty=False)
_table[0x66] = Opcode(mnemonic="ROR", mode=AddrMode.ZP, length=2, base_cycles=5, handler=_ror_zp, page_cross_penalty=False)
_table[0x67] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x68] = Opcode(mnemonic="PLA", mode=AddrMode.IMP, length=1, base_cycles=4, handler=_pla_imp, page_cross_penalty=False)
_table[0x69] = Opcode(mnemonic="ADC", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_adc_imm, page_cross_penalty=False)
_table[0x6A] = Opcode(mnemonic="ROR", mode=AddrMode.ACC, length=1, base_cycles=2, handler=_ror_acc, page_cross_penalty=False)
_table[0x6B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x6C] = Opcode(mnemonic="JMP", mode=AddrMode.IND, length=3, base_cycles=5, handler=_jmp_ind, page_cross_penalty=False)
_table[0x6D] = Opcode(mnemonic="ADC", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_adc_abs, page_cross_penalty=False)
_table[0x6E] = Opcode(mnemonic="ROR", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_ror_abs, page_cross_penalty=False)
_table[0x6F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x70] = Opcode(mnemonic="BVS", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bvs_rel, page_cross_penalty=False)
_table[0x71] = Opcode(mnemonic="ADC", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_adc_idy, page_cross_penalty=True)
_table[0x72] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x73] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x74] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x75] = Opcode(mnemonic="ADC", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_adc_zpx, page_cross_penalty=False)
_table[0x76] = Opcode(mnemonic="ROR", mode=AddrMode.ZPX, length=2, base_cycles=6, handler=_ror_zpx, page_cross_penalty=False)
_table[0x77] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x78] = Opcode(mnemonic="SEI", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_sei_imp, page_cross_penalty=False)
_table[0x79] = Opcode(mnemonic="ADC", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_adc_aby, page_cross_penalty=True)
_table[0x7A] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x7B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x7C] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x7D] = Opcode(mnemonic="ADC", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_adc_abx, page_cross_penalty=True)
_table[0x7E] = Opcode(mnemonic="ROR", mode=AddrMode.ABX, length=3, base_cycles=7, handler=_ror_abx, page_cross_penalty=False)
_table[0x7F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x80] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x81] = Opcode(mnemonic="STA", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_sta_idx, page_cross_penalty=False)
_table[0x82] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x83] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x84] = Opcode(mnemonic="STY", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_sty_zp, page_cross_penalty=False)
_table[0x85] = Opcode(mnemonic="STA", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_sta_zp, page_cross_penalty=False)
_table[0x86] = Opcode(mnemonic="STX", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_stx_zp, page_cross_penalty=False)
_table[0x87] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x88] = Opcode(mnemonic="DEY", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_dey_imp, page_cross_penalty=False)
_table[0x89] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x8A] = Opcode(mnemonic="TXA", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_txa_imp, page_cross_penalty=False)
_table[0x8B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x8C] = Opcode(mnemonic="STY", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_sty_abs, page_cross_penalty=False)
_table[0x8D] = Opcode(mnemonic="STA", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_sta_abs, page_cross_penalty=False)
_table[0x8E] = Opcode(mnemonic="STX", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_stx_abs, page_cross_penalty=False)
_table[0x8F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x90] = Opcode(mnemonic="BCC", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bcc_rel, page_cross_penalty=False)
_table[0x91] = Opcode(mnemonic="STA", mode=AddrMode.IDY, length=2, base_cycles=6, handler=_sta_idy, page_cross_penalty=False)
_table[0x92] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x93] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x94] = Opcode(mnemonic="STY", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_sty_zpx, page_cross_penalty=False)
_table[0x95] = Opcode(mnemonic="STA", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_sta_zpx, page_cross_penalty=False)
_table[0x96] = Opcode(mnemonic="STX", mode=AddrMode.ZPY, length=2, base_cycles=4, handler=_stx_zpy, page_cross_penalty=False)
_table[0x97] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x98] = Opcode(mnemonic="TYA", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_tya_imp, page_cross_penalty=False)
_table[0x99] = Opcode(mnemonic="STA", mode=AddrMode.ABY, length=3, base_cycles=5, handler=_sta_aby, page_cross_penalty=False)
_table[0x9A] = Opcode(mnemonic="TXS", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_txs_imp, page_cross_penalty=False)
_table[0x9B] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x9C] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x9D] = Opcode(mnemonic="STA", mode=AddrMode.ABX, length=3, base_cycles=5, handler=_sta_abx, page_cross_penalty=False)
_table[0x9E] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0x9F] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xA0] = Opcode(mnemonic="LDY", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_ldy_imm, page_cross_penalty=False)
_table[0xA1] = Opcode(mnemonic="LDA", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_lda_idx, page_cross_penalty=False)
_table[0xA2] = Opcode(mnemonic="LDX", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_ldx_imm, page_cross_penalty=False)
_table[0xA3] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xA4] = Opcode(mnemonic="LDY", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_ldy_zp, page_cross_penalty=False)
_table[0xA5] = Opcode(mnemonic="LDA", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_lda_zp, page_cross_penalty=False)
_table[0xA6] = Opcode(mnemonic="LDX", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_ldx_zp, page_cross_penalty=False)
_table[0xA7] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xA8] = Opcode(mnemonic="TAY", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_tay_imp, page_cross_penalty=False)
_table[0xA9] = Opcode(mnemonic="LDA", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_lda_imm, page_cross_penalty=False)
_table[0xAA] = Opcode(mnemonic="TAX", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_tax_imp, page_cross_penalty=False)
_table[0xAB] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xAC] = Opcode(mnemonic="LDY", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_ldy_abs, page_cross_penalty=False)
_table[0xAD] = Opcode(mnemonic="LDA", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_lda_abs, page_cross_penalty=False)
_table[0xAE] = Opcode(mnemonic="LDX", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_ldx_abs, page_cross_penalty=False)
_table[0xAF] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xB0] = Opcode(mnemonic="BCS", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bcs_rel, page_cross_penalty=False)
_table[0xB1] = Opcode(mnemonic="LDA", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_lda_idy, page_cross_penalty=True)
_table[0xB2] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xB3] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xB4] = Opcode(mnemonic="LDY", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_ldy_zpx, page_cross_penalty=False)
_table[0xB5] = Opcode(mnemonic="LDA", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_lda_zpx, page_cross_penalty=False)
_table[0xB6] = Opcode(mnemonic="LDX", mode=AddrMode.ZPY, length=2, base_cycles=4, handler=_ldx_zpy, page_cross_penalty=False)
_table[0xB7] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xB8] = Opcode(mnemonic="CLV", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_clv_imp, page_cross_penalty=False)
_table[0xB9] = Opcode(mnemonic="LDA", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_lda_aby, page_cross_penalty=True)
_table[0xBA] = Opcode(mnemonic="TSX", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_tsx_imp, page_cross_penalty=False)
_table[0xBB] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xBC] = Opcode(mnemonic="LDY", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_ldy_abx, page_cross_penalty=True)
_table[0xBD] = Opcode(mnemonic="LDA", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_lda_abx, page_cross_penalty=True)
_table[0xBE] = Opcode(mnemonic="LDX", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_ldx_aby, page_cross_penalty=True)
_table[0xBF] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xC0] = Opcode(mnemonic="CPY", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_cpy_imm, page_cross_penalty=False)
_table[0xC1] = Opcode(mnemonic="CMP", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_cmp_idx, page_cross_penalty=False)
_table[0xC2] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xC3] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xC4] = Opcode(mnemonic="CPY", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_cpy_zp, page_cross_penalty=False)
_table[0xC5] = Opcode(mnemonic="CMP", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_cmp_zp, page_cross_penalty=False)
_table[0xC6] = Opcode(mnemonic="DEC", mode=AddrMode.ZP, length=2, base_cycles=5, handler=_dec_zp, page_cross_penalty=False)
_table[0xC7] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xC8] = Opcode(mnemonic="INY", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_iny_imp, page_cross_penalty=False)
_table[0xC9] = Opcode(mnemonic="CMP", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_cmp_imm, page_cross_penalty=False)
_table[0xCA] = Opcode(mnemonic="DEX", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_dex_imp, page_cross_penalty=False)
_table[0xCB] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xCC] = Opcode(mnemonic="CPY", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_cpy_abs, page_cross_penalty=False)
_table[0xCD] = Opcode(mnemonic="CMP", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_cmp_abs, page_cross_penalty=False)
_table[0xCE] = Opcode(mnemonic="DEC", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_dec_abs, page_cross_penalty=False)
_table[0xCF] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xD0] = Opcode(mnemonic="BNE", mode=AddrMode.REL, length=2, base_cycles=2, handler=_bne_rel, page_cross_penalty=False)
_table[0xD1] = Opcode(mnemonic="CMP", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_cmp_idy, page_cross_penalty=True)
_table[0xD2] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xD3] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xD4] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xD5] = Opcode(mnemonic="CMP", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_cmp_zpx, page_cross_penalty=False)
_table[0xD6] = Opcode(mnemonic="DEC", mode=AddrMode.ZPX, length=2, base_cycles=6, handler=_dec_zpx, page_cross_penalty=False)
_table[0xD7] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xD8] = Opcode(mnemonic="CLD", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_cld_imp, page_cross_penalty=False)
_table[0xD9] = Opcode(mnemonic="CMP", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_cmp_aby, page_cross_penalty=True)
_table[0xDA] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xDB] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xDC] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xDD] = Opcode(mnemonic="CMP", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_cmp_abx, page_cross_penalty=True)
_table[0xDE] = Opcode(mnemonic="DEC", mode=AddrMode.ABX, length=3, base_cycles=7, handler=_dec_abx, page_cross_penalty=False)
_table[0xDF] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xE0] = Opcode(mnemonic="CPX", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_cpx_imm, page_cross_penalty=False)
_table[0xE1] = Opcode(mnemonic="SBC", mode=AddrMode.IDX, length=2, base_cycles=6, handler=_sbc_idx, page_cross_penalty=False)
_table[0xE2] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xE3] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xE4] = Opcode(mnemonic="CPX", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_cpx_zp, page_cross_penalty=False)
_table[0xE5] = Opcode(mnemonic="SBC", mode=AddrMode.ZP, length=2, base_cycles=3, handler=_sbc_zp, page_cross_penalty=False)
_table[0xE6] = Opcode(mnemonic="INC", mode=AddrMode.ZP, length=2, base_cycles=5, handler=_inc_zp, page_cross_penalty=False)
_table[0xE7] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xE8] = Opcode(mnemonic="INX", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_inx_imp, page_cross_penalty=False)
_table[0xE9] = Opcode(mnemonic="SBC", mode=AddrMode.IMM, length=2, base_cycles=2, handler=_sbc_imm, page_cross_penalty=False)
_table[0xEA] = Opcode(mnemonic="NOP", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_nop_imp, page_cross_penalty=False)
_table[0xEB] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xEC] = Opcode(mnemonic="CPX", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_cpx_abs, page_cross_penalty=False)
_table[0xED] = Opcode(mnemonic="SBC", mode=AddrMode.ABS, length=3, base_cycles=4, handler=_sbc_abs, page_cross_penalty=False)
_table[0xEE] = Opcode(mnemonic="INC", mode=AddrMode.ABS, length=3, base_cycles=6, handler=_inc_abs, page_cross_penalty=False)
_table[0xEF] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xF0] = Opcode(mnemonic="BEQ", mode=AddrMode.REL, length=2, base_cycles=2, handler=_beq_rel, page_cross_penalty=False)
_table[0xF1] = Opcode(mnemonic="SBC", mode=AddrMode.IDY, length=2, base_cycles=5, handler=_sbc_idy, page_cross_penalty=True)
_table[0xF2] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xF3] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xF4] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xF5] = Opcode(mnemonic="SBC", mode=AddrMode.ZPX, length=2, base_cycles=4, handler=_sbc_zpx, page_cross_penalty=False)
_table[0xF6] = Opcode(mnemonic="INC", mode=AddrMode.ZPX, length=2, base_cycles=6, handler=_inc_zpx, page_cross_penalty=False)
_table[0xF7] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xF8] = Opcode(mnemonic="SED", mode=AddrMode.IMP, length=1, base_cycles=2, handler=_sed_imp, page_cross_penalty=False)
_table[0xF9] = Opcode(mnemonic="SBC", mode=AddrMode.ABY, length=3, base_cycles=4, handler=_sbc_aby, page_cross_penalty=True)
_table[0xFA] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xFB] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xFC] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)
_table[0xFD] = Opcode(mnemonic="SBC", mode=AddrMode.ABX, length=3, base_cycles=4, handler=_sbc_abx, page_cross_penalty=True)
_table[0xFE] = Opcode(mnemonic="INC", mode=AddrMode.ABX, length=3, base_cycles=7, handler=_inc_abx, page_cross_penalty=False)
_table[0xFF] = Opcode(mnemonic="???", mode=AddrMode.IMP, length=1, base_cycles=0, handler=_illegal, page_cross_penalty=False)

# Cast away None after fill
OPCODES: list[Opcode] = _table  # type: ignore[assignment]
