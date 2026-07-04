"""APU (Ricoh 2A03 audio). Silent stub for MVP."""


class APU:
    __slots__ = ("interrupts",)

    def __init__(self, interrupts, *, region=None):
        self.interrupts = interrupts

    def read_status(self) -> int:
        return 0

    def write_register(self, address: int, value: int) -> None:
        pass

    def clock_cpu_cycle(self) -> None:
        pass
