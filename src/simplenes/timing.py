"""Clock timing constants and NTSC/PAL/DENDY region definitions."""

from dataclasses import dataclass
from enum import Enum, auto


class Region(Enum):
    NTSC = auto()
    PAL = auto()
    DENDY = auto()


@dataclass(frozen=True, slots=True)
class TimingConstants:
    cpu_clock_hz: int           # NTSC: 1_789_773
    ppu_dots_per_scanline: int  # 341
    scanlines_per_frame: int    # 262
    ppu_dots_per_cpu_cycle: int # 3


NTSC_TIMING = TimingConstants(
    cpu_clock_hz=1_789_773,
    ppu_dots_per_scanline=341,
    scanlines_per_frame=262,
    ppu_dots_per_cpu_cycle=3,
)
