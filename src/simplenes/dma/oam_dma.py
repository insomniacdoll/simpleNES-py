"""OAM DMA state machine."""

from dataclasses import dataclass


@dataclass
class OAMDMAState:
    active: bool = False
    page: int = 0          # CPU page ($XX00-$XXFF)
    address: int = 0       # current byte within page
    data: int = 0          # byte being transferred
    dummy_cycle: bool = True
    read_phase: bool = True

    def trigger(self, value: int) -> None:
        """CPU wrote $4014. Initiate DMA."""
        self.active = True
        self.page = value & 0xFF
        self.address = 0
        self.dummy_cycle = True
        self.read_phase = True

    def reset(self) -> None:
        """Reset DMA state machine."""
        self.active = False
        self.page = 0
        self.address = 0
        self.data = 0
        self.dummy_cycle = True
        self.read_phase = True
