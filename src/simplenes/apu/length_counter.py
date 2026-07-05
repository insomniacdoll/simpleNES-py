"""APU length counter — shared by Pulse, Triangle, and Noise channels."""

LENGTH_TABLE = [
    10, 254, 20, 2, 40, 4, 80, 6, 160, 8, 60, 10, 14, 12, 26, 14,
    12, 16, 24, 18, 48, 20, 96, 22, 192, 24, 72, 26, 16, 28, 32, 30,
]


class LengthCounter:
    __slots__ = ("_counter", "_enabled", "_halt")

    def __init__(self) -> None:
        self._counter = 0
        self._enabled = True
        self._halt = False

    def write(self, index: int) -> None:
        """Load counter from length table entry."""
        if self._enabled:
            self._counter = LENGTH_TABLE[index & 0x1F]

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self._counter = 0

    def set_halt(self, halt: bool) -> None:
        self._halt = halt

    def tick(self) -> None:
        """Decrement on half-frame, unless halted or disabled."""
        if self._counter > 0 and self._enabled and not self._halt:
            self._counter -= 1

    @property
    def value(self) -> int:
        return self._counter
