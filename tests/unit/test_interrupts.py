"""Unit tests for InterruptLines."""

from simplenes.interrupts import InterruptLines


def test_default_all_false():
    """All interrupt lines start as False."""
    lines = InterruptLines()
    assert lines.nmi_pending is False
    assert lines.irq_apu_frame is False
    assert lines.irq_dmc is False
    assert lines.irq_mapper is False
    assert lines.irq_active is False


def test_nmi_pending():
    """nmi_pending is independent of IRQ lines."""
    lines = InterruptLines()
    lines.nmi_pending = True
    assert lines.nmi_pending is True
    assert lines.irq_active is False  # NMI is not IRQ


def test_irq_active_any_source():
    """Any IRQ source makes irq_active True."""
    lines = InterruptLines()
    lines.irq_apu_frame = True
    assert lines.irq_active is True

    lines.irq_apu_frame = False
    lines.irq_dmc = True
    assert lines.irq_active is True

    lines.irq_dmc = False
    lines.irq_mapper = True
    assert lines.irq_active is True


def test_clear_irqs():
    """clear_irqs() resets all IRQ flags to False."""
    lines = InterruptLines(irq_apu_frame=True, irq_dmc=True, irq_mapper=True)
    lines.clear_irqs()
    assert lines.irq_apu_frame is False
    assert lines.irq_dmc is False
    assert lines.irq_mapper is False


def test_irq_active_false_after_clear():
    """irq_active is False after clear_irqs()."""
    lines = InterruptLines(irq_mapper=True)
    assert lines.irq_active is True
    lines.clear_irqs()
    assert lines.irq_active is False
