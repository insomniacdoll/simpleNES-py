"""OAM DMA state machine — Phase 5 simplified."""


class OAMDMAState:
    """OAM DMA: triggered by CPU write to $4014.  Atomic execution in Scheduler."""

    __slots__ = ("active", "page")

    def __init__(self):
        self.active = False
        self.page = 0

    def trigger(self, value: int) -> None:
        """CPU wrote $4014. Initiate DMA."""
        self.active = True
        self.page = value & 0xFF

    def reset(self) -> None:
        """Reset DMA state machine."""
        self.active = False
        self.page = 0
