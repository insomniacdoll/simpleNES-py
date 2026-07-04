"""Nestest trace parsing and ROM loading helpers.

Used by test_nestest integration test to compare the emulator trace
against a known-good execution trace.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass


@dataclass
class NestestLogEntry:
    """Parsed single line from nestest.log."""

    pc: int
    opcode: int
    mnemonic: str
    operand_str: str
    a: int
    x: int
    y: int
    p: int
    sp: int
    cycle: int

    @classmethod
    def parse_line(cls, line: str) -> NestestLogEntry | None:
        """Parse a single nestest log line.

        Format (78 chars per line):
            C000  4C F5 C5  JMP $C5F5                       A:00 X:00 Y:00 P:24 SP:FD CYC:0

        Returns None if the line does not match the expected format.
        """
        line = line.rstrip("\n")
        if len(line) < 73:
            return None

        try:
            # PC at positions 0-3 (hex)
            pc = int(line[0:4], 16)

            # Opcode + operand bytes at positions 6-15
            raw_bytes = line[6:15].strip()
            parts = raw_bytes.split()
            if not parts:
                return None
            opcode = int(parts[0], 16) if parts else 0

            # Mnemonic + operand at positions 16-47
            instr = line[16:47].strip()
            if " " in instr:
                mnemonic, operand_part = instr.split(" ", 1)
                operand_str = operand_part
            else:
                mnemonic = instr
                operand_str = ""

            # Extract register values using known field widths/positions
            def _hex_after(label: str, s: str) -> int:
                idx = s.find(label)
                if idx < 0:
                    return 0
                val_start = idx + len(label)
                val_end = val_start + 2
                return int(s[val_start:val_end], 16)

            def _int_after(label: str, s: str) -> int:
                idx = s.find(label)
                if idx < 0:
                    return 0
                val_start = idx + len(label)
                # find end of number
                val_end = val_start
                while val_end < len(s) and s[val_end].isdigit():
                    val_end += 1
                return int(s[val_start:val_end])

            a = _hex_after("A:", line)
            x = _hex_after("X:", line)
            y = _hex_after("Y:", line)
            p = _hex_after("P:", line)
            sp = _hex_after("SP:", line)
            cycle = _int_after("CYC:", line)

            return cls(
                pc=pc,
                opcode=opcode,
                mnemonic=mnemonic,
                operand_str=operand_str,
                a=a, x=x, y=y, p=p, sp=sp, cycle=cycle,
            )
        except (ValueError, IndexError):
            return None


def parse_nestest_log(log_path: pathlib.Path) -> list[NestestLogEntry]:
    """Parse a nestest.log file into a list of entries."""
    entries = []
    with open(log_path, "r") as f:
        for line in f:
            entry = NestestLogEntry.parse_line(line)
            if entry is not None:
                entries.append(entry)
    return entries


ROM_PATH = pathlib.Path(__file__).parent.parent / "roms" / "nestest.nes"


def get_nestest_rom_path() -> pathlib.Path | None:
    """Return path to nestest.nes if it exists, else None."""
    rom = ROM_PATH
    if rom.exists():
        return rom
    return None


def load_nestest_rom(path: pathlib.Path) -> bytes:
    """Load raw nestest ROM bytes from path."""
    with open(path, "rb") as f:
        return f.read()
