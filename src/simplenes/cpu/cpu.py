"""6502 CPU (Ricoh 2A03). Phase 2 full implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from simplenes.interrupts import InterruptLines

if TYPE_CHECKING:
    from simplenes.cpu.opcodes import Opcode


# ===================== Trace =====================


@dataclass(slots=True)
class CpuTraceEntry:
    """Single instruction trace snapshot, captured before execution."""

    pc: int
    opcode: int
    operand_bytes: tuple[int, ...]
    mnemonic: str
    operand_str: str
    a: int
    x: int
    y: int
    p: int
    sp: int
    cycle: int
    ppu_scanline: int = 0
    ppu_dot: int = 0

    def format_nestest_line(self) -> str:
        """Format as a nestest-compatible trace line."""
        # Format opcode + operand bytes
        raw_bytes = [self.opcode] + list(self.operand_bytes)
        raw_hex = " ".join(f"{b:02X}" for b in raw_bytes)
        # Right-pad raw bytes to 10 chars (3 bytes max = "XX YY ZZ")
        raw_hex = raw_hex.ljust(10)
        # Format mnemonic + operand
        instr = f"{self.mnemonic} {self.operand_str}".ljust(31)
        return (
            f"{self.pc:04X}  {raw_hex} {instr}"
            f"A:{self.a:02X} X:{self.x:02X} Y:{self.y:02X} "
            f"P:{self.p:02X} SP:{self.sp:02X} CYC:{self.cycle}"
        )


class CpuTraceLogger:
    """Optional logger that records instruction trace entries."""

    __slots__ = ("entries", "enabled")

    def __init__(self) -> None:
        self.entries: list[CpuTraceEntry] = []
        self.enabled = False

    def capture(
        self,
        cpu: "CPU",
        pc: int,
        opcode: int,
        operand_bytes: tuple[int, ...],
        entry: Opcode,
    ) -> None:
        """Create a CpuTraceEntry from pre-instruction CPU state and append."""
        # Build operand_str based on addressing mode
        operand_str = format_operand(entry, operand_bytes, pc)

        self.entries.append(
            CpuTraceEntry(
                pc=pc,
                opcode=opcode,
                operand_bytes=operand_bytes,
                mnemonic=entry.mnemonic,
                operand_str=operand_str,
                a=cpu.a,
                x=cpu.x,
                y=cpu.y,
                p=cpu.p,
                sp=cpu.sp,
                cycle=cpu.total_cycles,
            )
        )

    def clear(self) -> None:
        self.entries.clear()


def format_operand(entry: Opcode, operand_bytes: tuple[int, ...], pc: int = 0) -> str:
    """Format the operand string for nestest trace output.

    Args:
        entry: Opcode metadata (mode, length, mnemonic).
        operand_bytes: Raw operand bytes (excluding opcode).
        pc: PC of the instruction (used for relative branch target calculation).
    """
    from simplenes.cpu.opcodes import AddrMode

    mode = entry.mode
    length = entry.length

    if length == 1:
        # Implied or accumulator
        if mode == AddrMode.ACC:
            return "A"
        return ""

    if length == 2:
        op0 = operand_bytes[0]
        if mode == AddrMode.IMM:
            return f"#${op0:02X}"
        if mode in (AddrMode.ZP, AddrMode.ZPX, AddrMode.ZPY):
            suffix = ""
            if mode == AddrMode.ZPX:
                suffix = ",X"
            elif mode == AddrMode.ZPY:
                suffix = ",Y"
            return f"${op0:02X}{suffix}"
        if mode in (AddrMode.IDX, AddrMode.IDY):
            suffix = ",X)" if mode == AddrMode.IDX else "),Y"
            return f"(${op0:02X}{suffix}"
        if mode == AddrMode.REL:
            # Branch: compute target = pc + 2 + signed offset
            offset = op0 if op0 < 0x80 else op0 - 0x100
            target = (pc + 2 + offset) & 0xFFFF
            return f"${target:04X}"

    if length == 3:
        op0 = operand_bytes[0]
        op1 = operand_bytes[1]
        addr = (op1 << 8) | op0
        if mode == AddrMode.ABS:
            return f"${addr:04X}"
        if mode == AddrMode.ABX:
            return f"${addr:04X},X"
        if mode == AddrMode.ABY:
            return f"${addr:04X},Y"
        if mode == AddrMode.IND:
            return f"(${addr:04X})"

    return f"${operand_bytes[0]:02X}" if operand_bytes else ""


# ===================== CPU =====================


class CPU:
    """6502 CPU (Ricoh 2A03).

    Registers: A, X, Y, SP, PC, P (NV-BDIZC).
    """

    __slots__ = (
        "bus", "interrupts",
        "a", "x", "y",
        "sp", "pc",
        "p",
        "total_cycles", "halted",
        "_trace_logger", "_trace_callback",
        "_page_crossed",
        "_irq_prev",
        "_last_pc", "_last_opcode",
    )

    STACK_BASE = 0x0100
    VECTOR_NMI = 0xFFFA
    VECTOR_RESET = 0xFFFC
    VECTOR_IRQ = 0xFFFE

    # Status flag masks
    FLAG_C = 0x01
    FLAG_Z = 0x02
    FLAG_I = 0x04
    FLAG_D = 0x08
    FLAG_B = 0x10
    FLAG_U = 0x20
    FLAG_V = 0x40
    FLAG_N = 0x80

    def __init__(self, bus, interrupts: InterruptLines) -> None:
        self.bus = bus
        self.interrupts = interrupts
        self.a = 0
        self.x = 0
        self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.p = self.FLAG_U | self.FLAG_I  # $24
        self.total_cycles = 0
        self.halted = False
        self._trace_logger = None
        self._trace_callback = None
        self._page_crossed = False
        self._irq_prev = False
        self._last_pc = 0
        self._last_opcode = 0

    # --- Public API ---

    def reset(self) -> None:
        self.sp = 0xFD
        self.p = self.FLAG_U | self.FLAG_I  # $24
        self.a = 0
        self.x = 0
        self.y = 0
        self.total_cycles = 7
        self.halted = False
        self._page_crossed = False
        self._irq_prev = False
        self._last_pc = 0
        self._last_opcode = 0
        self.pc = self._read_word(self.VECTOR_RESET)

    def step_instruction(self) -> int:
        from simplenes.cpu.opcodes import OPCODES

        pc_before = self.pc
        opcode = self._read_pc()

        entry = OPCODES[opcode]

        # Read operand bytes from raw memory (for trace only)
        operand_bytes = tuple(
            self._read((pc_before + i) & 0xFFFF)
            for i in range(1, entry.length)
        )

        self._last_pc = pc_before
        self._last_opcode = opcode

        if self._trace_logger and self._trace_logger.enabled:
            self._trace_logger.capture(
                self, pc_before, opcode, operand_bytes, entry
            )
            if self._trace_callback is not None:
                self._trace_callback(self._trace_logger.entries[-1])

        self._page_crossed = False

        cycles = entry.handler(self)

        interrupt_cycles = self._check_nmi()
        if interrupt_cycles == 0:
            interrupt_cycles = self._check_irq()

        total = cycles + interrupt_cycles
        self.total_cycles += total
        return total

    # --- Trace ---

    def set_trace_enabled(self, enabled: bool) -> None:
        if self._trace_logger is None and enabled:
            self._trace_logger = CpuTraceLogger()
        if self._trace_logger is not None:
            self._trace_logger.enabled = enabled

    def get_trace_logger(self) -> CpuTraceLogger | None:
        return self._trace_logger

    def set_trace_callback(self, callback) -> None:
        """Register a callback invoked with each CpuTraceEntry after capture.

        The callback receives a single argument: the CpuTraceEntry.
        Automatically enables the trace logger if a callback is provided.
        Set to None to disable (logging remains active if enabled).
        """
        self._trace_callback = callback
        if callback is not None:
            self.set_trace_enabled(True)

    # --- Internal helpers ---

    def _read(self, addr: int) -> int:
        return self.bus.read(addr)

    def _write(self, addr: int, value: int) -> None:
        self.bus.write(addr, value)

    def _read_pc(self) -> int:
        val = self._read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return val

    def _read_word(self, addr: int) -> int:
        lo = self._read(addr)
        hi = self._read((addr + 1) & 0xFFFF)
        return (hi << 8) | lo

    def _push(self, value: int) -> None:
        self._write(self.STACK_BASE | self.sp, value & 0xFF)
        self.sp = (self.sp - 1) & 0xFF

    def _pull(self) -> int:
        self.sp = (self.sp + 1) & 0xFF
        return self._read(self.STACK_BASE | self.sp)

    def _push_word(self, value: int) -> None:
        self._push((value >> 8) & 0xFF)
        self._push(value & 0xFF)

    def _pull_word(self) -> int:
        lo = self._pull()
        hi = self._pull()
        return (hi << 8) | lo

    # --- Flag helpers ---

    def _set_nz(self, value: int) -> None:
        value &= 0xFF
        self.p = (self.p & ~(self.FLAG_N | self.FLAG_Z)) | (value & self.FLAG_N)
        if value == 0:
            self.p |= self.FLAG_Z

    def _add_with_carry(self, value: int) -> None:
        a = self.a
        c = self.p & self.FLAG_C
        temp = a + value + c
        # V flag: set when (A^M)&0x80==0 and (A^result)&0x80!=0
        result_u8 = temp & 0xFF
        v = (~(a ^ value) & (a ^ result_u8)) & self.FLAG_V
        carry = 1 if temp > 0xFF else 0
        self.p = (self.p & ~(self.FLAG_V | self.FLAG_C)) | v | carry
        self.a = result_u8
        self._set_nz(self.a)

    def _sub_with_carry(self, value: int) -> None:
        self._add_with_carry(value ^ 0xFF)

    def _compare(self, reg: int, value: int) -> None:
        result = (reg - value) & 0xFFFF
        self.p = (self.p & ~(self.FLAG_C | self.FLAG_Z | self.FLAG_N))
        if reg >= value:
            self.p |= self.FLAG_C
        result_u8 = result & 0xFF
        if result_u8 == 0:
            self.p |= self.FLAG_Z
        self.p |= result_u8 & self.FLAG_N

    # --- Interrupt handling ---

    def _check_nmi(self) -> int:
        if self.interrupts.nmi_pending:
            self._push_word(self.pc)
            self._push((self.p & ~self.FLAG_B) | self.FLAG_U)
            self.p |= self.FLAG_I
            self.pc = self._read_word(self.VECTOR_NMI)
            self.interrupts.nmi_pending = False
            return 7
        return 0

    def _check_irq(self) -> int:
        if self.interrupts.irq_active and not (self.p & self.FLAG_I):
            self._push_word(self.pc)
            self._push((self.p & ~self.FLAG_B) | self.FLAG_U)
            self.p |= self.FLAG_I
            self.pc = self._read_word(self.VECTOR_IRQ)
            return 7
        return 0
