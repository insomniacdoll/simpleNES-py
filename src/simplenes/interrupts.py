"""Shared interrupt lines between CPU, PPU, APU and Mapper components."""

from dataclasses import dataclass


@dataclass
class InterruptLines:
    """Shared interrupt lines between components.

    PPU drives nmi_pending.
    APU drives irq_apu_frame and irq_dmc.
    Mapper drives irq_mapper.
    CPU samples at proper boundaries.
    """

    nmi_pending: bool = False
    irq_apu_frame: bool = False
    irq_dmc: bool = False
    irq_mapper: bool = False

    @property
    def irq_active(self) -> bool:
        """True if any IRQ source is asserting."""
        return self.irq_apu_frame or self.irq_dmc or self.irq_mapper

    def clear_irqs(self) -> None:
        """Reset all IRQ flags. Caller must re-assert if still valid."""
        self.irq_apu_frame = False
        self.irq_dmc = False
        self.irq_mapper = False
