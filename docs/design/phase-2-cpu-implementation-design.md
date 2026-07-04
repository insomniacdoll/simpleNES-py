# Phase 2: CPU 详细实现设计

## Summary

本文档基于 `docs/architecture.md` 的架构设计，产出 Phase 2（6502 CPU 官方指令集）的详细实现级设计。Phase 0/1 已完成项目骨架、iNES parser、NROM mapper、CPU/PPU bus 及 stub 组件，Phase 2 将 CPU 从 stub 升级为完整的 6502 实现。

设计范围：
- 151 条官方 opcode（全部 13 种寻址模式）
- 状态寄存器 flags（NV-BDIZC）
- 栈操作
- RESET / NMI / IRQ / BRK 中断处理
- 精确 cycle 计数（branch extra cycle、page crossing extra cycle）
- 2A03 decimal mode（SED/CLD 不影响算术运算）
- JMP indirect page-wrap bug（6502 经典 bug：`JMP ($xxFF)` 在同一页内 wrap）
- Zero-page wrap
- nestest trace 基础设施

**绝对不进入 Phase 3（PPU 寄存器）。**

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/errors.py` | 修改 — 新增 `IllegalOpcodeError` |
| `src/simplenes/cpu/cpu.py` | 重写 — 从 stub 变为完整 CPU 实现 |
| `src/simplenes/cpu/opcodes.py` | **新建** — opcode 表、寻址模式、指令实现 |
| `src/simplenes/cpu/__init__.py` | 修改 — 重导出 CPU + 新增 trace 类型 |
| `tests/unit/test_cpu.py` | **新建** — 寻址模式、指令、flags、cycle 单元测试 |
| `tests/unit/test_cpu_interrupts.py` | **新建** — 中断（RESET/NMI/IRQ/BRK）单元测试 |
| `tests/traces/` | **新建** — nestest trace log 测试目录 |
| `tests/integration/test_nestest.py` | **新建** — nestest ROM trace 对拍集成测试 |
| `tests/fixtures/nestest_helper.py` | **新建** — nestest trace 解析与 ROM 加载助手 |
| `docs/design/phase-2-cpu-implementation-design.md` | **新建** — 本文档 |

---

## File Layout (Phase 2)

```text
src/simplenes/cpu/
    __init__.py       # re-exports CPU, CpuTraceEntry, CpuTraceLogger
    cpu.py            # CPU class: registers, step_instruction, reset, interrupts, trace
    opcodes.py        # opcode table, 13 addressing modes, 151 instruction handlers

tests/
    unit/
        test_cpu.py           # unit: addressing modes, instructions, flags, cycles
        test_cpu_interrupts.py # unit: RESET / NMI / IRQ / BRK
    traces/
        nestest.log           # golden trace file (committed)
    integration/
        test_nestest.py       # nestest trace comparison
    fixtures/
        nestest_helper.py     # trace parser, ROM loader
```

---

## Interface & API Design

---

### 1. `simplenes/cpu/cpu.py` — CPU Class

```python
"""6502 CPU (Ricoh 2A03). Phase 2 full implementation."""

from typing import Optional

from simplenes.interrupts import InterruptLines


class CPU:
    """6502 CPU (Ricoh 2A03).

    Registers:
        A  — accumulator (8-bit)
        X  — index X (8-bit)
        Y  — index Y (8-bit)
        SP — stack pointer (8-bit, initially $FD)
        PC — program counter (16-bit)
        P  — processor status (NV-BDIZC)

    Status flags (bit position):
        7: N (Negative)
        6: V (Overflow)
        5: - (unused, always 1 in PHP/BRK push)
        4: B (Break command — only in stack representations, not a real register bit)
        3: D (Decimal — ignored on 2A03, arithmetic always binary)
        2: I (Interrupt Disable)
        1: Z (Zero)
        0: C (Carry)
    """

    __slots__ = (
        "bus", "interrupts",
        "a", "x", "y",
        "sp", "pc",
        "p",
        "total_cycles", "halted",
        "_trace_logger",
        "_page_crossed",
        "_irq_prev",
        "_last_pc", "_last_opcode",
    )

    # Stack page base
    STACK_BASE = 0x0100

    # Reset / interrupt vectors
    VECTOR_NMI   = 0xFFFA
    VECTOR_RESET = 0xFFFC
    VECTOR_IRQ   = 0xFFFE

    # Status flag masks
    FLAG_C = 0x01  # Carry
    FLAG_Z = 0x02  # Zero
    FLAG_I = 0x04  # Interrupt Disable
    FLAG_D = 0x08  # Decimal
    FLAG_B = 0x10  # Break
    FLAG_U = 0x20  # Unused (always set)
    FLAG_V = 0x40  # Overflow
    FLAG_N = 0x80  # Negative

    def __init__(self, bus, interrupts: InterruptLines) -> None: ...

    # --- Public API ---
    def reset(self) -> None:
        """Power-on reset: reads reset vector, initializes state.

        Reset sequence:
        1. SP -= 3 (dummy pushes on real 6502)
        2. I flag set
        3. PC = read_word($FFFC)
        4. total_cycles = 7  (matching standard nestest.log convention)
        """

    def step_instruction(self) -> int:
        """Execute one complete instruction.

        Returns:
            Total CPU cycles consumed (instruction cycles + any interrupt
            service cycles from NMI/IRQ).

        Sequence:
        1. Save pc_before; fetch opcode via _read_pc() (PC advances by 1)
        2. Look up Opcode metadata, read operand bytes from raw memory for trace
        3. Set _last_pc / _last_opcode for IllegalOpcodeError reporting
        4. Capture trace entry from pre-instruction state (if enabled)
        5. Dispatch opcode handler -- handler consumes operands via _read_pc()
        6. Service pending interrupts (NMI first, then IRQ) -- adds to total
        7. Update total_cycles, return total
        """

    # --- Trace ---
    def set_trace_enabled(self, enabled: bool) -> None: ...
    def set_trace_callback(self, callback) -> None: ...

    # --- Internal helpers (called by opcode handlers) ---
    def _read(self, addr: int) -> int: ...
    def _write(self, addr: int, value: int) -> None: ...
    def _read_pc(self) -> int: ...
    def _read_word(self, addr: int) -> int: ...
    def _push(self, value: int) -> None: ...
    def _pull(self) -> int: ...
    def _push_word(self, value: int) -> None: ...
    def _pull_word(self) -> int: ...

    # --- Flag helpers ---
    def _set_nz(self, value: int) -> None:
        """Set N and Z flags based on value."""
    def _set_carry(self, value: bool) -> None: ...
    def _set_overflow(self, value: bool) -> None: ...
    def _add_with_carry(self, value: int) -> None:
        """ADC core: A = A + value + C, set NZCV."""
    def _sub_with_carry(self, value: int) -> None:
        """SBC core: A = A - value - (1-C), set NZCV."""
    def _compare(self, reg: int, value: int) -> None:
        """CMP/CPX/CPY core: set NZC from reg - value."""
```

**验收**：
- `reset()` 后 PC = `read_word($FFFC)`，SP = `$FD`，P = `$24`（I + U flags set）
- `step_instruction()` 执行一条指令，返回精确 cycle 数
- NMI pending-event（service 后清除）vs IRQ 电平触发正确区分

---

### 2. `simplenes/cpu/cpu.py` — Trace Infrastructure

```python
from dataclasses import dataclass


@dataclass(slots=True)
class CpuTraceEntry:
    """Single instruction trace snapshot, captured before instruction execution.

    All register / flag / cycle fields reflect CPU state at the moment the
    opcode is fetched, before the instruction is executed.
    """
    pc: int
    opcode: int
    operand_bytes: tuple[int, ...]  # raw operand bytes, e.g. (0xF5, 0xC5) for JMP
    mnemonic: str                   # 3-char, e.g. "LDA"
    operand_str: str                # formatted, e.g. "$C5F5" or "#$AA"
    a: int
    x: int
    y: int
    p: int                          # status as 8-bit value
    sp: int
    cycle: int                      # cumulative cycle count (before this instruction)
    ppu_scanline: int = 0           # reserved for Phase 3+
    ppu_dot: int = 0                # reserved for Phase 3+

    def format_nestest_line(self) -> str:
        """Format as a nestest-compatible trace line.

        Standard nestest format:
        C000  4C F5 C5  JMP $C5F5                       A:00 X:00 Y:00 P:24 SP:FD CYC:0

        Phase 2: ppu_scanline/ppu_dot omitted from output (reserved fields).
        """
        ...


class CpuTraceLogger:
    """Optional logger that records instruction trace entries."""

    __slots__ = ("entries", "enabled")

    def __init__(self) -> None:
        self.entries: list[CpuTraceEntry] = []
        self.enabled = False

    def capture(self, cpu, pc: int, opcode: int,
                operand_bytes: tuple[int, ...], entry: "Opcode") -> None:
        """Create a CpuTraceEntry from pre-instruction CPU state and append.

        Called by step_instruction() BEFORE the handler executes.
        Uses the explicit ``pc`` argument (pc_before) as the trace PC,
        NOT the CPU's current ``self.pc`` (which has already advanced by 1).
        """
        ...

    def clear(self) -> None:
        self.entries.clear()
```

**nestest trace 格式与 capture 时机**：

- **Capture 时机**: 在指令执行 **之前**，所有寄存器 / flags / cycle 为该指令执行前的 CPU 状态。
- **`CpuTraceEntry` 字段来源**:
  - `pc`: `pc_before`（指令执行前 PC）
  - `opcode`: 刚 fetch 的 opcode 字节
  - `operand_bytes`: 从 `OPCODES[opcode].length` 读取的后续字节
  - `mnemonic` / `operand_str`: 由 `format_nestest_line()` 根据 `Opcode` metadata + `operand_bytes` 格式化
  - `cycle`: `total_cycles`（指令执行前）
  - `a/x/y/p/sp`: 指令执行前寄存器状态

- P 值必须包含 B flag 的正确状态：
  - BRK 推栈时 P |= FLAG_B | FLAG_U → $3x
  - IRQ/NMI 推栈时 P = (original P & ~FLAG_B) | FLAG_U → $2x
  - PHP 推栈时 P |= FLAG_B | FLAG_U → $3x
  - PLP 拉栈时保留 B bit 和 U bit

---

### 3. `simplenes/cpu/opcodes.py` — 寻址模式

```python
"""6502 addressing modes and instruction implementations.

All 13 official addressing modes:
    1. IMPLIED          — no operand
    2. ACCUMULATOR      — A register
    3. IMMEDIATE        — #$NN
    4. ZERO_PAGE        — $NN
    5. ZERO_PAGE_X      — $NN,X
    6. ZERO_PAGE_Y      — $NN,Y
    7. RELATIVE         — branch offset (signed)
    8. ABSOLUTE         — $NNNN
    9. ABSOLUTE_X       — $NNNN,X
    10. ABSOLUTE_Y      — $NNNN,Y
    11. INDIRECT        — ($NNNN)  [JMP only]
    12. INDIRECT_X      — ($NN,X)
    13. INDIRECT_Y      — ($NN),Y
"""

from enum import IntEnum


class AddrMode(IntEnum):
    IMP = 0
    ACC = 1       # Accumulator
    IMM = 2       # Immediate
    ZP  = 3       # Zero Page
    ZPX = 4       # Zero Page, X
    ZPY = 5       # Zero Page, Y
    REL = 6       # Relative (branch)
    ABS = 7       # Absolute
    ABX = 8       # Absolute, X
    ABY = 9       # Absolute, Y
    IND = 10      # Indirect (JMP)
    IDX = 11      # Indexed Indirect ($NN,X)
    IDY = 12      # Indirect Indexed ($NN),Y
```

**寻址模式方法（定义在 `CPU` 类中，`opcodes.py` 仅定义表）**：

实际实现：`opcodes.py` 中的 handler 函数接收 `cpu` 实例，各自调用地址解析。

```python
# 寻址模式解析约定：
# 每个 handler 内部自行调用 cpu._read_pc() 读取操作数，
# 然后解析地址、执行读写、更新标志和寄存器。

# 示例 — LDA immediate handler:
def _lda_imm(cpu: "CPU") -> int:
    """LDA #$NN — 2 cycles"""
    cpu.a = cpu._read_pc()
    cpu._set_nz(cpu.a)
    return 2

# 示例 — LDA absolute handler:
def _lda_abs(cpu: "CPU") -> int:
    """LDA $NNNN — 4 cycles"""
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    addr = (hi << 8) | lo
    cpu.a = cpu._read(addr)
    cpu._set_nz(cpu.a)
    return 4

# 示例 — LDA absolute,X handler:
def _lda_abx(cpu: "CPU") -> int:
    """LDA $NNNN,X — 4 cycles (+1 if page crossed)"""
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    addr = ((hi << 8) | lo) + cpu.x
    cpu._page_crossed = (addr >> 8) != hi
    addr &= 0xFFFF
    cpu.a = cpu._read(addr)
    cpu._set_nz(cpu.a)
    return 4 + (1 if cpu._page_crossed else 0)

# 示例 — RMW absolute,X handler (e.g. ASL $NNNN,X):
def _asl_abx(cpu: "CPU") -> int:
    """ASL $NNNN,X — 7 cycles"""
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    addr = (((hi << 8) | lo) + cpu.x) & 0xFFFF
    value = cpu._read(addr)
    cpu._write(addr, value)   # dummy write (RMW cycle)
    cpu.p = (cpu.p & ~cpu.FLAG_C) | ((value >> 7) & 1)
    value = (value << 1) & 0xFF
    cpu._set_nz(value)
    cpu._write(addr, value)
    return 7
```

**寻址模式 `_page_crossed` 规则**：

| 模式 | page-cross 检测 | 适用场景 |
|------|----------------|----------|
| ABSOLUTE_X | addr_base + X → hi byte 是否改变 | LDA/LDX/LDY/ADC/SBC/CMP/ORA/AND/EOR 的 abs,x |
| ABSOLUTE_Y | addr_base + Y → hi byte 是否改变 | LDA/LDX 的 abs,y + ADC/SBC/CMP/ORA/AND/EOR 的 abs,y |
| INDIRECT_Y | `(zp),Y` 最终地址 page-crossed | LDA/ADC/SBC/CMP/ORA/AND/EOR 的 (zp),y |

**RMW 指令 page-cross 不产生额外 cycle**：RMW abs,x 总是 7 cycles，不会因 page crossing 变成 8。

---

### 4. `simplenes/cpu/opcodes.py` — 指令实现（按类别分组）

#### 4.1 Load/Store (31 opcodes)

| 指令 | IMM | ZP | ZPX | ZPY | ABS | ABX | ABY | IDX | IDY |
|------|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| **LDA** | A9,2 | A5,3 | B5,4 | — | AD,4 | BD,4* | B9,4* | A1,6 | B1,5* |
| **LDX** | A2,2 | A6,3 | — | B6,4 | AE,4 | — | BE,4* | — | — |
| **LDY** | A0,2 | A4,3 | B4,4 | — | AC,4 | BC,4* | — | — | — |

| 指令 | ZP | ZPX | ZPY | ABS | ABX | ABY | IDX | IDY |
|------|-----|-----|-----|-----|-----|-----|-----|-----|
| **STA** | 85,3 | 95,4 | — | 8D,4 | 9D,5 | 99,5 | 81,6 | 91,6 |
| **STX** | 86,3 | — | 96,4 | 8E,4 | — | — | — | — |
| **STY** | 84,3 | 94,4 | — | 8C,4 | — | — | — | — |

> `*` = +1 cycle if page crossed

**实现要点**：
- LDA/LDX/LDY → `_set_nz(value)`
- STA → 不修改任何 flag
- STA abs,x 和 abs,y 总是 5 cycles（不管是否 page-cross，因为所有 store abs,x/y 都执行 dummy read）
- STX zp,y / STY zp,x 总是 4 cycles

#### 4.2 Arithmetic — ADC / SBC (16 opcodes)

| 指令 | IMM | ZP | ZPX | ABS | ABX | ABY | IDX | IDY |
|------|-----|-----|-----|-----|-----|-----|-----|-----|
| **ADC** | 69,2 | 65,3 | 75,4 | 6D,4 | 7D,4* | 79,4* | 61,6 | 71,5* |
| **SBC** | E9,2 | E5,3 | F5,4 | ED,4 | FD,4* | F9,4* | E1,6 | F1,5* |

**ADC 核心逻辑**：
```python
def _add_with_carry(self, value: int) -> None:
    """A = A + value + C. Sets N, Z, V, C."""
    result = self.a + value + (self.p & self.FLAG_C)
    # V flag: overflow when signs of inputs are same but result sign differs
    self.p = (self.p & ~self.FLAG_V) | (
        ((self.a ^ result) & (value ^ result) & 0x80) >> 1
    )
    self.p = (self.p & ~self.FLAG_C) | (1 if result > 0xFF else 0)
    self.a = result & 0xFF
    self._set_nz(self.a)
```

**SBC 核心逻辑**：
```python
def _sub_with_carry(self, value: int) -> None:
    """A = A - value - (1-C). Equivalent to A + ~value + C."""
    self._add_with_carry(value ^ 0xFF)
```

> 2A03 decimal mode：即使 D flag = 1，ADC/SBC 也执行二进制运算。不进行 BCD 修正。

#### 4.3 INC/DEC (12 opcodes)

| 指令 | ZP | ZPX | ABS | ABX | IMP |
|------|-----|-----|-----|-----|-----|
| **INC** | E6,5 | F6,6 | EE,6 | FE,7 | — |
| **DEC** | C6,5 | D6,6 | CE,6 | DE,7 | — |
| **INX** | — | — | — | — | E8,2 |
| **INY** | — | — | — | — | C8,2 |
| **DEX** | — | — | — | — | CA,2 |
| **DEY** | — | — | — | — | 88,2 |

**RMW 周期约定**（以 INC zp 为例，5 cycles = read + write_modify + write）：
1. Read operand from zp address
2. Write same value back (dummy RMW write)
3. Write incremented value

**实现**：
```python
def _inc_zp(cpu: "CPU") -> int:
    addr = cpu._read_pc()  # zero-page address
    value = cpu._read(addr)
    cpu._write(addr, value)  # dummy write
    value = (value + 1) & 0xFF
    cpu._write(addr, value)
    cpu._set_nz(value)
    return 5
```

#### 4.4 Logic — AND/ORA/EOR (24 opcodes)

| 指令 | IMM | ZP | ZPX | ABS | ABX | ABY | IDX | IDY |
|------|-----|-----|-----|-----|-----|-----|-----|-----|
| **AND** | 29,2 | 25,3 | 35,4 | 2D,4 | 3D,4* | 39,4* | 21,6 | 31,5* |
| **ORA** | 09,2 | 05,3 | 15,4 | 0D,4 | 1D,4* | 19,4* | 01,6 | 11,5* |
| **EOR** | 49,2 | 45,3 | 55,4 | 4D,4 | 5D,4* | 59,4* | 41,6 | 51,5* |

**实现**：`A = A OP value; _set_nz(A)`

#### 4.5 Shift/Rotate — ASL/LSR/ROL/ROR (20 opcodes)

| 指令 | ACC | ZP | ZPX | ABS | ABX |
|------|-----|-----|-----|-----|-----|
| **ASL** | 0A,2 | 06,5 | 16,6 | 0E,6 | 1E,7 |
| **LSR** | 4A,2 | 46,5 | 56,6 | 4E,6 | 5E,7 |
| **ROL** | 2A,2 | 26,5 | 36,6 | 2E,6 | 3E,7 |
| **ROR** | 6A,2 | 66,5 | 76,6 | 6E,6 | 7E,7 |

**ASL 实现**：
```python
def _asl_acc(cpu: "CPU") -> int:
    cpu.p = (cpu.p & ~cpu.FLAG_C) | ((cpu.a >> 7) & 1)
    cpu.a = (cpu.a << 1) & 0xFF
    cpu._set_nz(cpu.a)
    return 2
```

#### 4.6 BIT (2 opcodes)

| 指令 | ZP | ABS |
|------|-----|-----|
| **BIT** | 24,3 | 2C,4 |

**BIT 逻辑**：
- Z = (A & M) == 0
- N = M bit 7
- V = M bit 6

#### 4.7 Comparison — CMP/CPX/CPY (14 opcodes)

| 指令 | IMM | ZP | ZPX | ABS | ABX | ABY | IDX | IDY |
|------|-----|-----|-----|-----|-----|-----|-----|-----|
| **CMP** | C9,2 | C5,3 | D5,4 | CD,4 | DD,4* | D9,4* | C1,6 | D1,5* |
| **CPX** | E0,2 | E4,3 | — | EC,4 | — | — | — | — |
| **CPY** | C0,2 | C4,3 | — | CC,4 | — | — | — | — |

**实现**：`result = reg - value; Z = (result == 0); N = result bit 7; C = (reg >= value)`

#### 4.8 Branch (8 opcodes)

| 指令 | Opcode | 条件 |
|------|--------|------|
| BCC | 90 | C == 0 |
| BCS | B0 | C == 1 |
| BEQ | F0 | Z == 1 |
| BMI | 30 | N == 1 |
| BNE | D0 | Z == 0 |
| BPL | 10 | N == 0 |
| BVC | 50 | V == 0 |
| BVS | 70 | V == 1 |

**Branch cycle 规则**：
- Not taken: 2 cycles
- Taken, same page: 3 cycles
- Taken, different page: 4 cycles

```python
def _branch(cpu: "CPU") -> int:
    offset = cpu._read_pc()
    # offset is signed 8-bit
    old_pc = cpu.pc
    cpu.pc = (cpu.pc + (offset if offset < 0x80 else offset - 0x100)) & 0xFFFF
    cycles = 3  # taken
    if (old_pc ^ cpu.pc) & 0xFF00:
        cycles = 4  # page crossed
    return cycles
```

#### 4.9 Jump (3 opcodes)

| 指令 | ABS | IND |
|------|-----|-----|
| **JMP** | 4C,3 | 6C,5 |
| **JSR** | 20,6 | — |
| **RTS** | 60,6 | — |

**JMP indirect bug**：
```python
def _jmp_ind(cpu: "CPU") -> int:
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    ptr = (hi << 8) | lo
    # 6502 bug: if lo == 0xFF, hi byte is read from ptr & 0xFF00, not ptr + 1
    if lo == 0xFF:
        lo_eff = cpu._read(ptr)
        hi_eff = cpu._read(ptr & 0xFF00)
    else:
        lo_eff = cpu._read(ptr)
        hi_eff = cpu._read(ptr + 1)
    cpu.pc = (hi_eff << 8) | lo_eff
    return 5
```

**JSR 实现**：
```python
def _jsr_abs(cpu: "CPU") -> int:
    lo = cpu._read_pc()
    hi = cpu._read_pc()
    cpu._push_word((cpu.pc - 1) & 0xFFFF)  # push return addr - 1
    cpu.pc = (hi << 8) | lo
    return 6
```

**RTS 实现**：
```python
def _rts_imp(cpu: "CPU") -> int:
    cpu.pc = (cpu._pull_word() + 1) & 0xFFFF
    return 6
```

#### 4.10 Stack (4 opcodes)

| 指令 | IMP |
|------|-----|
| PHA | 48,3 |
| PHP | 08,3 |
| PLA | 68,4 |
| PLP | 28,4 |

**PHP 推栈 P 值**：`(p | FLAG_B | FLAG_U)` — push 时 B 和 U bit 都为 1。
**PLP 拉栈**：恢复所有 flags，但 B 和 U bit 来自栈值（不做 mask）。

#### 4.11 Transfer (4 opcodes)

| 指令 | IMP |
|------|-----|
| TAX | AA,2 |
| TAY | A8,2 |
| TXA | 8A,2 |
| TYA | 98,2 |
| TSX | BA,2 |
| TXS | 9A,2 |

所有 transfer 都 `_set_nz(register)`，**TXS 除外**（不修改 flags）。

#### 4.12 Flag Instructions (8 opcodes)

| 指令 | IMP |
|------|-----|
| CLC | 18,2 |
| CLD | D8,2 |
| CLI | 58,2 |
| CLV | B8,2 |
| SEC | 38,2 |
| SED | F8,2 |
| SEI | 78,2 |

> CLD 和 SED 仅设置/清除 D flag。在 2A03 上 D flag 不影响 ADC/SBC 运算。

#### 4.13 Interrupt (2 opcodes)

| 指令 | Cycles |
|------|--------|
| BRK | 00,7 |
| RTI | 40,6 |

**BRK 实现**：
```python
def _brk_imp(cpu: "CPU") -> int:
    cpu._read_pc()  # dummy read of padding byte
    cpu._push_word(cpu.pc)
    cpu._push(cpu.p | cpu.FLAG_B | cpu.FLAG_U)  # B flag set
    cpu.p |= cpu.FLAG_I
    cpu.pc = cpu._read_word(cpu.VECTOR_IRQ)
    return 7
```

**RTI 实现**：
```python
def _rti_imp(cpu: "CPU") -> int:
    cpu.p = cpu._pull()
    cpu.pc = cpu._pull_word()
    return 6
```

#### 4.14 NOP (1 opcode)

| 指令 | IMP |
|------|-----|
| NOP | EA,2 |

---

### 5. `simplenes/cpu/opcodes.py` — Opcode 查找表

```python
"""Opcode dispatch table with metadata for trace and cycle counting.

Each entry is an Opcode dataclass instance containing:
- mnemonic, addressing mode, instruction length, base cycles
- page-cross penalty flag
- handler callable
"""

from dataclasses import dataclass
from typing import Callable

# Handler signature
Handler = Callable[["CPU"], int]


@dataclass(frozen=True, slots=True)
class Opcode:
    """Static metadata for one 6502 opcode."""
    mnemonic: str           # 3-char uppercase, e.g. "LDA"
    mode: "AddrMode"        # addressing mode
    length: int             # instruction length in bytes (1=implied, 2=1-byte op, 3=2-byte op)
    base_cycles: int        # minimum cycles for this instruction
    handler: Handler        # instruction implementation
    page_cross_penalty: bool = False  # True if abs,x / abs,y / (zp),y may add +1 cycle
    # Note: base_cycles and page_cross_penalty are metadata for trace/tests.
    # The handler return value is the actual source of truth for cycle count.

# 256-entry opcode table
# Built as list[Optional[Opcode]] then cast after populate.
OPCODES: list[Opcode] = [None] * 256  # type: ignore[assignment]

# Populate official opcodes
OPCODES[0x00] = Opcode("BRK", AddrMode.IMP, 1, 7, _brk_imp)
OPCODES[0x01] = Opcode("ORA", AddrMode.IDX, 2, 6, _ora_idx)
OPCODES[0x05] = Opcode("ORA", AddrMode.ZP, 2, 3, _ora_zp)
OPCODES[0x06] = Opcode("ASL", AddrMode.ZP, 2, 5, _asl_zp)
OPCODES[0x08] = Opcode("PHP", AddrMode.IMP, 1, 3, _php_imp)
OPCODES[0x09] = Opcode("ORA", AddrMode.IMM, 2, 2, _ora_imm)
OPCODES[0x0A] = Opcode("ASL", AddrMode.ACC, 1, 2, _asl_acc)
OPCODES[0x0D] = Opcode("ORA", AddrMode.ABS, 3, 4, _ora_abs)
OPCODES[0x0E] = Opcode("ASL", AddrMode.ABS, 3, 6, _asl_abs)
# ... (all 151 official opcodes)

# Fill remaining 105 slots with the illegal-opcode sentinel
from simplenes.errors import IllegalOpcodeError

def _illegal(cpu: "CPU") -> int:
    """Illegal opcode -- raise immediately so bugs are not silently masked.

    Requires CPU to track _last_pc and _last_opcode (set in step_instruction
    before dispatch).
    """
    raise IllegalOpcodeError(cpu._last_opcode, cpu._last_pc)

for i in range(256):
    if OPCODES[i] is None:
        # Sentinel Opcode; handler raises IllegalOpcodeError.
        OPCODES[i] = Opcode("???", AddrMode.IMP, 1, 0, _illegal)
```

> **Unofficial/illegal opcode 策略**: Phase 2 默认 raise `IllegalOpcodeError` (不静默 NOP).
> 后续如需兼容使用 unofficial opcode 的 ROM, 可增加 `illegal_opcode_policy="nop"` 可选模式, 但默认必须是 `"raise"`.

---


### 6. CPU 中断处理

#### 6.1 NMI（Non-Maskable Interrupt）

- **语义**：`nmi_pending` 是 pending-event 标志（不是 hardware line）。
  PPU/外部将其设为 `True` 表示有 NMI 待处理；CPU 在 service 后将其置 `False`。
- 检测时机：每条指令执行完毕后
- 处理流程：
  1. `push_word(PC)`
  2. `push((P & ~FLAG_B) | FLAG_U)` — B flag **必须为 0**（与 BRK 不同）
  3. `P |= FLAG_I`
  4. `PC = read_word($FFFA)`
  5. `self.interrupts.nmi_pending = False`
  6. 消耗 7 cycles

```python
def _check_nmi(self) -> int:
    """Check and handle pending NMI. Returns 7 if NMI was serviced, else 0.

    nmi_pending is a pending-event flag (not an edge-triggered line).
    The CPU clears it after servicing so the same pending edge is not
    re-serviced on the next instruction.
    """
    if self.interrupts.nmi_pending:
        self._push_word(self.pc)
        self._push((self.p & ~self.FLAG_B) | self.FLAG_U)
        self.p |= self.FLAG_I
        self.pc = self._read_word(self.VECTOR_NMI)
        self.interrupts.nmi_pending = False  # clear after servicing
        return 7  # interrupt cycle count
    return 0  # no interrupt serviced
```

#### 6.2 IRQ（Maskable Interrupt）

- **电平触发**：IRQ 在 `irq_active == True` 且 `I flag == 0` 时触发
- 检测时机：每条指令执行完毕后（NMI 之后）
- 处理流程（与 BRK 相似，但 B flag 不设置）：
  1. `push_word(PC)`
  2. `push((P & ~FLAG_B) | FLAG_U)` — B flag **必须为 0**
  3. `P |= FLAG_I`
  4. `PC = read_word($FFFE)`
  5. 消耗 7 cycles

```python
def _check_irq(self) -> int:
    """Check and handle pending IRQ. Returns 7 if IRQ was serviced, else 0."""
    if self.interrupts.irq_active and not (self.p & self.FLAG_I):
        self._push_word(self.pc)
        self._push((self.p & ~self.FLAG_B) | self.FLAG_U)
        self.p |= self.FLAG_I
        self.pc = self._read_word(self.VECTOR_IRQ)
        return 7  # interrupt cycle count
    return 0  # no interrupt serviced
```

#### 6.3 RESET

```python
def reset(self) -> None:
    """Power-on reset."""
    # Simulate 3 dummy stack pushes (SP -= 3)
    self.sp = 0xFD
    self.p = self.FLAG_U | self.FLAG_I          # $24
    self.a = 0
    self.x = 0
    self.y = 0
    self.total_cycles = 7                        # standard reset cycle convention
    self.halted = False
    self._page_crossed = False
    self._irq_prev = False
    # Read reset vector
    self.pc = self._read_word(self.VECTOR_RESET)
```

---

### 7.  CPU Class 完整 `step_instruction` 流程

```python
def step_instruction(self) -> int:
    """Execute one complete instruction. Returns total cycles consumed
    (instruction cycles + any interrupt service cycles).

    Handlers consume operands via _read_pc(); step_instruction
    only fetches the opcode byte.  PC is NOT advanced past the full
    instruction in advance -- each handler manages its own PC.
    """
    # Save pre-instruction state and fetch opcode
    pc_before = self.pc
    opcode = self._read_pc()  # PC becomes pc_before + 1

    # Look up metadata
    entry = OPCODES[opcode]

    # Read operand bytes from raw memory (for trace) -- these are
    # NOT consumed from PC by step_instruction; the handler will
    # re-read them via _read_pc() during execution.
    operand_bytes = tuple(
        self._read((pc_before + i) & 0xFFFF)
        for i in range(1, entry.length)
    )

    # Track last instruction for IllegalOpcodeError reporting
    self._last_pc = pc_before
    self._last_opcode = opcode

    # Capture trace BEFORE execution (all regs are pre-instruction state)
    if self._trace_logger and self._trace_logger.enabled:
        self._trace_logger.capture(
            self, pc_before, opcode, operand_bytes, entry
        )

    # Reset page-cross flag at instruction start
    self._page_crossed = False

    # Dispatch -- handler consumes operands via _read_pc(),
    # updates flags / registers, and returns instruction cycles only.
    cycles = entry.handler(self)

    # Interrupt handling (NMI first, then IRQ) -- adds to total cycles
    interrupt_cycles = self._check_nmi()
    if interrupt_cycles == 0:
        interrupt_cycles = self._check_irq()

    total = cycles + interrupt_cycles
    self.total_cycles += total
    return total
```

---

## Edge Cases

### E-1: Zero-page wrap
`$NN,X` 和 `$NN,Y` 在 zero page 内 wrap：
```
LDX #$01
LDA $FF,X   ; 读取 $00（wrap 在 zero page 内）
```

### E-2: JMP indirect page-wrap bug
```
JMP ($12FF)  ; 6502 bug: low byte from $12FF, high byte from $1200
```

### E-3: Decimal mode on 2A03
- SED/CLD 设置/清除 D flag
- ADC/SBC **始终执行二进制运算**，不检查 D flag
- PHP/PLP 正常保存/恢复 D flag
- 这使 2A03 的 ADC/SBC 比标准 6502 简单

### E-4: BRK vs IRQ stack frame
BRK 推栈时 P 的 B flag (bit 4) = 1
IRQ/NMI 推栈时 P 的 B flag (bit 4) = 0
RTI/PLP 不区分 —— 栈上的 B bit 直接恢复到 P

### E-5: Branch page-cross detection
Branch 指令 cycle 数：
- `branch_taken = True` → 3 cycles
- `branch_taken = True 且 PC page changed` → 4 cycles

### E-6: RMW dummy write
所有 RMW 指令 (ASL/LSR/ROL/ROR/INC/DEC) 都执行一次 dummy write（写入原始值后再写入修改值）。这对 PPU 寄存器行为有影响（Phase 3 相关）。

### E-7: Stack wrap
SP 是 8-bit，在 $0100-$01FF 内自动 wrap。推栈时 SP 递减，拉栈时 SP 递增。

### E-8: P flag bit 5 (Unused)
- 硬件上该 bit 始终为 1
- PHP/BRK 推栈时 force 该 bit 为 1
- PLP/RTI 拉栈时保留栈上的该 bit 值

### E-9: Unofficial/illegal opcodes (Phase 2 行为)
- 151 条官方 opcode 实现完整逻辑
- 其余 105 条 illegal opcode → `raise IllegalOpcodeError(opcode, pc)`（不静默 NOP）
- 错误类型 `IllegalOpcodeError(EmulationError)` 携带 `opcode` 和 `pc` 字段
- Phase 2 不实现完整 unofficial opcode 逻辑；后续 Phase 可增加 `illegal_opcode_policy="nop"` 降级模式

---

## Test Strategy

### Test Matrix

| 测试文件 | 覆盖范围 | 关键断言 |
|----------|----------|----------|
| `test_cpu.py::test_addressing_modes` | 13 种寻址模式地址解析 | 地址正确、page-cross 检测、zero-page wrap |
| `test_cpu.py::test_load_store` | LDA/LDX/LDY/STA/STX/STY 所有模式 | 寄存器值、内存值、N/Z flags |
| `test_cpu.py::test_arithmetic` | ADC/SBC/INC/DEC/INX/INY/DEX/DEY | A 值、N/Z/C/V flags、decimal mode 无影响 |
| `test_cpu.py::test_logic` | AND/ORA/EOR/ASL/LSR/ROL/ROR/BIT | A 值、flags、C flag for shifts |
| `test_cpu.py::test_comparison` | CMP/CPX/CPY | N/Z/C flags（不修改寄存器） |
| `test_cpu.py::test_branch` | BCC/BCS/BEQ/BMI/BNE/BPL/BVC/BVS | branch taken/not taken、page-cross cycles |
| `test_cpu.py::test_jump` | JMP abs, JMP ind, JSR, RTS | PC 值、JMP indirect bug、stack 值 |
| `test_cpu.py::test_stack` | PHA/PHP/PLA/PLP | stack 值、SP、flags |
| `test_cpu.py::test_transfer` | TAX/TAY/TXA/TYA/TSX/TXS | 寄存器值、N/Z flags（TXS 无 flags） |
| `test_cpu.py::test_flags` | CLC/SEC/CLD/SED/CLI/SEI/CLV | 各 flag 正确设置/清除 |
| `test_cpu.py::test_cycle_count` | 每条指令 cycle 数 | 精确 cycle（含 page-cross、branch） |
| `test_cpu.py::test_jmp_indirect_bug` | JMP ($xxFF) | 高字节从同页读取 |
| `test_cpu_interrupts.py::test_reset` | CPU.reset() | PC=$FFFC vector, SP=$FD, P=$24 |
| `test_cpu_interrupts.py::test_nmi` | NMI pending-event, service 后清除 | PC=$FFFA vector, stack push, 7 cycles |
| `test_cpu_interrupts.py::test_irq` | IRQ 电平触发 | PC=$FFFE vector, I flag 阻挡, 7 cycles |
| `test_cpu_interrupts.py::test_brk` | BRK 指令 | B flag=1 in stack, PC=$FFFE vector |
| `test_cpu_interrupts.py::test_rti` | RTI 指令 | P 和 PC 从 stack 恢复 |

### nestest 集成测试

**目标**：与 nestest 黄金 trace 逐行对拍，确保 CPU 实现正确。

**流程**：
1. 加载 `nestest.nes` ROM
2. 将 PC 初始化为 `$C000`（跳过 test ROM 内置的 reset 序列）
3. 运行指定条数指令，每步生成 trace entry
4. 与 `nestest.log`（黄金 trace）逐行比较

**比较字段**：PC、A、X、Y、P、SP、CYC（不比较 opcode 字节、助记符、操作数字符串和 PPU 字段）

**验收标准**：所有官方 opcode 指令的 trace 完全一致。

**nestest 直接入口模式**（绕过真实 reset 以避免依赖 mapper 的 vector 初始化）：

```python
# nestest 直接入口 -- 不调用 cpu.reset(), 手动设置状态
# 此模式与标准 nestest.log 黄金 trace 对齐
cpu.pc = 0xC000
cpu.sp = 0xFD
cpu.p = 0x24          # I + U flags set
cpu.a = 0x00
cpu.x = 0x00
cpu.y = 0x00
cpu.total_cycles = 7   # nestest.log 首行 CYC 通常为 7
```

> **说明**: `total_cycles = 7` 对应真实 reset 序列消耗的 7 个 CPU cycles.
> 黄金 trace 中首条指令的 CYC 字段 = 7 + 首条指令 cycles.
> 如果黄金 trace 首行 CYC = 0, 则设 `total_cycles = 0`.

**注意**：nestest 不检测 unofficial opcode，Phase 2 只验证官方指令。

---

## Step-by-Step Implementation Plan

### Step 1: `simplenes/cpu/opcodes.py` — 框架 + Load/Store

1. 创建 `opcodes.py`
2. 定义 `AddrMode` IntEnum
3. 定义 helper 函数模板（`_read` → 地址解析 helper pattern）
4. 实现所有 31 条 Load/Store handlers
5. 实现 `OPCODES` 表框架（`Opcode` dataclass entries, 先只填充 Load/Store opcode）
6. 最终未定义的 105 个 illegal opcode 填充为 `_illegal` sentinel（此阶段先 fill，后续 instruction handler 逐步覆盖）

### Step 2: `simplenes/cpu/cpu.py` — CPU Class 升级

1. 扩展 `__slots__` 添加寄存器、SP、PC、P、flags
2. 实现 `_read` / `_write` / `_read_pc` / `_read_word` 总线方法
3. 实现 `_push` / `_pull` / `_push_word` / `_pull_word` 栈方法
4. 实现 `_set_nz` / `_set_carry` / `_set_overflow` flag helpers
5. 实现 `_add_with_carry` / `_sub_with_carry` / `_compare` 算术 helpers
6. 实现 `step_instruction` 主调度循环（dispatch `OPCODES[opcode].handler`, 累加 interrupt cycles 到返回值）
7. 实现 `reset()` 含 reset vector 读取

### Step 3: `simplenes/cpu/opcodes.py` — 全部指令

1. 实现 Arithmetic handlers (ADC/SBC/INC/DEC/INX/INY/DEX/DEY)
2. 实现 Logic handlers (AND/ORA/EOR/ASL/LSR/ROL/ROR/BIT)
3. 实现 Comparison handlers (CMP/CPX/CPY)
4. 实现 Branch handlers (BCC/BCS/BEQ/BMI/BNE/BPL/BVC/BVS)
5. 实现 Jump handlers (JMP abs, JMP ind, JSR, RTS)
6. 实现 Stack handlers (PHA/PHP/PLA/PLP)
7. 实现 Transfer handlers (TAX/TAY/TXA/TYA/TSX/TXS)
8. 实现 Flag handlers (CLC/SEC/CLD/SED/CLI/SEI/CLV)
9. 实现 Interrupt opcode handlers (BRK/RTI)
10. NOP

### Step 4: Interrupt handling

1. 实现 `_check_nmi()` -- NMI pending-event 检测, service 后清除, 返回 7 或 0
2. 实现 `_check_irq()` -- IRQ 电平检测 + I flag 检查, 返回 7 或 0
3. 在 `step_instruction` 末尾累加 interrupt cycles 到总 cycle 返回值

### Step 5: Trace infrastructure

1. 定义 `CpuTraceEntry` dataclass
2. 定义 `CpuTraceLogger`
3. 实现 `format_nestest_line()` — 匹配 nestest trace 格式
4. 在 `step_instruction` 中集成 single-stage pre-instruction trace capture

### Step 6: Unit tests — 寻址模式与指令

1. 创建 `tests/unit/test_cpu.py`
2. 实现 fixture：创建最小 CPUBus + CPU（含 2KB RAM + ROM stub）
3. 按 test matrix 顺序实现所有 unit test
4. 每个 test 覆盖：寄存器值、内存值、flags、cycle count

### Step 7: Unit tests — 中断

1. 创建 `tests/unit/test_cpu_interrupts.py`
2. 测试 RESET/NMI/IRQ/BRK/RTI
3. 验证 stack push 内容、vector 读取、I flag 行为

### Step 8: nestest 集成测试

1. 获取 nestest.nes ROM（不嵌入仓库；测试在 ROM 缺失时 `pytest.skip`）
2. 创建 `tests/fixtures/nestest_helper.py` — ROM 加载 + trace 解析 + skip-if-missing
3. 创建 `tests/traces/nestest.log` — 黄金 trace（从已知正确模拟器导出或社区维护版本）
4. 创建 `tests/integration/test_nestest.py` — 逐行对拍
5. 运行并修复差异

### Step 9: 验证

1. `uv run ruff check src/ tests/`
2. `uv run pytest tests/ -q`
3. nestest 100% 通过

---

## Risks / Open Questions

### R-1: nestest trace 格式精度
nestest 黄金 trace 格式对空格敏感。确保 `format_nestest_line()` 的输出与黄金 trace 完全一致（包括：PC 大写 hex、3-char mnemonic、操作数字符串、padding 空格数、寄存器值格式）。

**缓解**：使用已知正确的模拟器（Mesen/FCEUX/Nintendulator）生成黄金 trace，或使用社区维护的 nestest.log。

### R-2: Unofficial/illegal opcode 兼容性
Phase 2 默认对 105 条非法 opcode 抛出 `IllegalOpcodeError`, 不会静默 NOP。
某些 ROM 可能使用 unofficial opcode（特别是 NROM 后期游戏）, 它们会触发异常而无法运行。

**缓解**：NROM 游戏使用 unofficial opcode 的情况极少。Phase 2 只要求通过 nestest（官方指令 100%）。
nestest 仅使用官方指令, 不会触发 `IllegalOpcodeError`。后续 Phase 可增加 `illegal_opcode_policy="nop"` 可选降级模式。

### R-3: CPU trace 实时性能
nestest 运行期间 CPU trace 会分配大量 `CpuTraceEntry` 对象和字符串。

**缓解**：trace 只在 `_trace_logger.enabled == True` 时启用。production 运行时 trace 应关闭。Phase 2 中 trace 仅用于 nestest 验证。

### R-4: 2A03 与 6502 decimal mode 差异
标准 6502 在 D flag=1 时执行 BCD 算术（但 2A03 无 BCD 电路，始终二进制）。测试必须验证 SED/CLD 存在但 ADC/SBC 不做 BCD 修正。

**缓解**：为 ADC/SBC 编写显式测试：设置 D flag，验证结果不受 D flag 影响。

### R-5: PPU 状态 stub 与 CPU 配合
Phase 2 的 CPU 执行完毕后会 tick PPU（通过 Scheduler），但 PPU 仍是 Phase 1 stub。

> **注意**：当前 PPU stub 的 `clock()` 只设置 `self.status |= 0x80`（内部 VBlank flag），
> **不会** 写入 `interrupts.nmi_pending`。该集成行为在 Phase 3 PPU register/NMI 实现中完成。

**缓解**：Phase 2 CPU 单元测试直接操作 `interrupts.nmi_pending` 来验证 NMI 处理逻辑。
集成测试（PPU -> NMI -> CPU）留到 Phase 3。

### R-6: nestest 测试如何访问 CPU 内部状态
`NESMachine` 的 public API 不暴露 `_cpu` 引用，这是正确的封装设计。
nestest 测试不需要通过 `NESMachine` 启动。

**决定**：nestest 集成测试直接构造 `CPU + CPUBus + Mapper + ROM`，
不经过 `NESMachine`。这避免向 `NESMachine` 的 public API 添加仅用于测试的 debug 方法。

```python
# tests/integration/test_nestest.py
def test_nestest():
    image = load_nestest_rom()
    mapper = NROMMapper(image)
    ppu_bus = PPUBus(mapper)
    interrupts = InterruptLines()
    ppu = PPU(bus=ppu_bus, interrupts=interrupts)
    apu = APU(interrupts=interrupts)
    bus = CPUBus(ppu=ppu, apu=apu, mapper=mapper,
                 controller1=Controller(), controller2=Controller(),
                 oam_dma_state=OAMDMAState())
    cpu = CPU(bus=bus, interrupts=interrupts)
    cpu.set_trace_enabled(True)
    cpu.sp = 0xFD
    cpu.p = 0x24
    cpu.pc = 0xC000
    cpu.total_cycles = 7
    # run and compare ...
```

---

## Architecture Conformance Check

对照 `docs/architecture.md` 的 Phase 2 要求：

| 要求 | 设计涵盖 |
|------|----------|
| official opcode | ✅ 151 条全部实现 |
| illegal opcode 检测 | ✅ `IllegalOpcodeError` (新增 error 类型) |
| addressing modes | ✅ 13 种全部实现 |
| status flags | ✅ NV-BDIZC 全部正确 |
| stack | ✅ push/pull + word 操作（$0100-$01FF wrap） |
| RESET / NMI / IRQ / BRK | ✅ NMI pending-event、IRQ level-triggered、stack frame、vector |
| cycle counting | ✅ base + page-cross + branch extra |
| JMP indirect page-wrap bug | ✅ `JMP ($xxFF)` 特殊处理 |
| zero-page wrap | ✅ `$NN,X` 在 page 0 内 wrap |
| 2A03 decimal mode | ✅ ADC/SBC 不执行 BCD 修正 |
| trace 关闭时 0 开销 | ✅ via `_trace_logger.enabled` flag |
| nestest trace 对拍 | ✅ `CpuTraceEntry.format_nestest_line()` |

---

> **Revision**: v1.5 -- Review 修正: test matrix NMI 措辞, 清理未使用 imports, Opcode metadata 注释
