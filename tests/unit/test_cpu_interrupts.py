"""Unit tests for CPU interrupts: RESET, NMI, IRQ, BRK, RTI.

Verifies interrupt triggering, vector reads, stack frames,
flag behavior (B/U bits), service cycles, and priority.
"""


from simplenes.cpu.cpu import CPU
from simplenes.bus.cpu_bus import CPUBus
from simplenes.ppu.ppu import PPU
from simplenes.apu.apu import APU
from simplenes.cartridge.ines import RomParser
from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
from simplenes.bus.ppu_bus import PPUBus
from simplenes.dma.oam_dma import OAMDMAState
from simplenes.input.controller import Controller
from simplenes.interrupts import InterruptLines
from tests.fixtures.nrom_sample import build_nrom_ines


# ---------------------------------------------------------------------------
# Helper: build a NROM ROM with specific interrupt/vector values.
# ---------------------------------------------------------------------------

def _build_interrupt_test_rom(
    reset_lo: int = 0x00,
    reset_hi: int = 0x02,
    nmi_lo: int = 0x00,
    nmi_hi: int = 0x04,
    irq_lo: int = 0x00,
    irq_hi: int = 0x06,
    prg_banks: int = 2,
) -> bytearray:
    """Build a minimal iNES ROM with known interrupt vectors.

    Vectors are placed in the last 6 bytes of PRG ROM:
      $FFFA = nmi_lo,   $FFFB = nmi_hi
      $FFFC = reset_lo, $FFFD = reset_hi
      $FFFE = irq_lo,   $FFFF = irq_hi

    The rest of PRG ROM is filled with 0xEA (NOP).
    """
    prg_size = prg_banks * 16384
    prg = bytearray(b"\xEA" * prg_size)

    # Last 6 bytes = vectors
    base = prg_size - 6
    prg[base + 0] = nmi_lo
    prg[base + 1] = nmi_hi
    prg[base + 2] = reset_lo
    prg[base + 3] = reset_hi
    prg[base + 4] = irq_lo
    prg[base + 5] = irq_hi

    return build_nrom_ines(prg_rom=bytes(prg), prg_banks=prg_banks)


def _make_cpu(
    reset_lo: int = 0x00,
    reset_hi: int = 0x02,
    nmi_lo: int = 0x00,
    nmi_hi: int = 0x04,
    irq_lo: int = 0x00,
    irq_hi: int = 0x06,
    interrupts: InterruptLines | None = None,
) -> CPU:
    """Build a wired CPU from a ROM with custom vectors."""
    raw = _build_interrupt_test_rom(
        reset_lo=reset_lo, reset_hi=reset_hi,
        nmi_lo=nmi_lo, nmi_hi=nmi_hi,
        irq_lo=irq_lo, irq_hi=irq_hi,
    )
    image = RomParser.parse(bytes(raw))
    mapper = NROMMapper(image)
    ppu_bus = PPUBus(mapper)
    ppt = PPU(bus=ppu_bus, interrupts=InterruptLines())
    irq = interrupts if interrupts is not None else InterruptLines()
    apu = APU(interrupts=InterruptLines())
    bus = CPUBus(ppu=ppt, apu=apu, mapper=mapper,
                 controller1=Controller(), controller2=Controller(),
                 oam_dma_state=OAMDMAState())
    cpu = CPU(bus=bus, interrupts=irq)
    return cpu


# ---------------------------------------------------------------------------
# RESET
# ---------------------------------------------------------------------------

def test_reset():
    """reset() reads vector from $FFFC, sets SP=$FD, P=$24, cycles=7."""
    cpu = _make_cpu(reset_lo=0x34, reset_hi=0x12)
    cpu.reset()

    assert cpu.pc == 0x1234
    assert cpu.sp == 0xFD
    assert cpu.p == (cpu.FLAG_U | cpu.FLAG_I)  # $24
    assert cpu.total_cycles == 7
    assert cpu.halted is False


def test_reset_preserves_internal_flags():
    """reset() re-initializes a/x/y/sp and P to known state regardless of prior."""
    cpu = _make_cpu()
    cpu.a = 0xFF
    cpu.x = 0x88
    cpu.y = 0x77
    cpu.sp = 0x10
    cpu.p = 0x00
    cpu.reset()

    assert cpu.a == 0
    assert cpu.x == 0
    assert cpu.y == 0
    assert cpu.sp == 0xFD
    assert cpu.p == (cpu.FLAG_U | cpu.FLAG_I)


# ---------------------------------------------------------------------------
# NMI
# ---------------------------------------------------------------------------

def test_nmi_triggers_and_clears_pending():
    """NMI pending-event triggers interrupt, then clears the flag."""
    interrupts = InterruptLines()
    cpu = _make_cpu(nmi_lo=0xCD, nmi_hi=0xAB, interrupts=interrupts)

    cpu.reset()
    initial_sp = cpu.sp
    assert cpu.p == (cpu.FLAG_U | cpu.FLAG_I)  # I+U after reset

    # Write a NOP at the current PC so step_instruction has something to execute
    cpu.bus.write(cpu.pc, 0xEA)  # NOP

    # Set NMI pending
    interrupts.nmi_pending = True

    cycles = cpu.step_instruction()

    # NMI serviced: 2 (NOP) + 7 (NMI) = 9
    assert cycles == 9
    # nmi_pending cleared
    assert interrupts.nmi_pending is False
    # PC jumped via NMI vector
    assert cpu.pc == 0xABCD
    # SP decreased by 3 (PCL + PCH + P) — 2 for word + 1 for status
    assert cpu.sp == initial_sp - 3
    # I flag set
    assert cpu.p & cpu.FLAG_I


def test_nmi_pushes_correct_status():
    """NMI pushed status has B=0, U=1."""
    interrupts = InterruptLines()
    cpu = _make_cpu(interrupts=interrupts)
    cpu.reset()
    cpu.p = (cpu.FLAG_C | cpu.FLAG_Z | cpu.FLAG_U)  # C=1, Z=1, U=1, I=0

    cpu.bus.write(cpu.pc, 0xEA)  # NOP
    interrupts.nmi_pending = True
    cpu.step_instruction()

    # Stack after NMI: PCH at SP+3, PCL at SP+2, P at SP+1
    pushed_p = cpu.bus.read(0x0100 | ((cpu.sp + 1) & 0xFF))

    # B flag MUST be 0 for NMI
    assert (pushed_p & cpu.FLAG_B) == 0
    # U flag must be 1
    assert pushed_p & cpu.FLAG_U
    # C and Z should be preserved
    assert pushed_p & cpu.FLAG_C
    assert pushed_p & cpu.FLAG_Z


# ---------------------------------------------------------------------------
# IRQ
# ---------------------------------------------------------------------------

def test_irq_triggers_when_i_clear():
    """IRQ services when irq_active=True and I flag is 0."""
    interrupts = InterruptLines()
    cpu = _make_cpu(irq_lo=0x78, irq_hi=0x56, interrupts=interrupts)
    cpu.reset()
    cpu.p &= ~cpu.FLAG_I  # clear I flag
    initial_sp = cpu.sp

    cpu.bus.write(cpu.pc, 0xEA)  # NOP
    interrupts.irq_mapper = True

    cycles = cpu.step_instruction()

    assert cycles == 9  # 2 (NOP) + 7 (IRQ)
    assert cpu.pc == 0x5678
    assert cpu.sp == initial_sp - 3
    assert cpu.p & cpu.FLAG_I  # I flag set after service


def test_irq_blocked_when_i_set():
    """IRQ does NOT service when I flag is 1."""
    interrupts = InterruptLines()
    cpu = _make_cpu(interrupts=interrupts)
    cpu.reset()
    # reset() sets I flag
    assert cpu.p & cpu.FLAG_I
    initial_sp = cpu.sp

    cpu.bus.write(cpu.pc, 0xEA)  # NOP
    interrupts.irq_mapper = True

    cycles = cpu.step_instruction()

    assert cycles == 2  # just NOP, no interrupt service
    assert cpu.pc != 0x0600  # not the IRQ vector
    assert cpu.sp == initial_sp  # SP unchanged


def test_irq_pushes_correct_status():
    """IRQ pushed status has B=0, U=1."""
    interrupts = InterruptLines()
    cpu = _make_cpu(interrupts=interrupts)
    cpu.reset()
    cpu.p = (cpu.FLAG_V | cpu.FLAG_U)  # V=1, U=1, I=0

    cpu.bus.write(cpu.pc, 0xEA)
    interrupts.irq_mapper = True
    cpu.step_instruction()

    pushed_p = cpu.bus.read(0x0100 | ((cpu.sp + 1) & 0xFF))
    assert (pushed_p & cpu.FLAG_B) == 0  # B must be 0
    assert pushed_p & cpu.FLAG_U           # U must be 1
    assert pushed_p & cpu.FLAG_V           # V preserved


# ---------------------------------------------------------------------------
# NMI priority
# ---------------------------------------------------------------------------

def test_nmi_takes_priority_over_irq():
    """When both pending, NMI services first; IRQ still pending."""
    interrupts = InterruptLines()
    cpu = _make_cpu(nmi_lo=0x34, nmi_hi=0x12, irq_lo=0xCD, irq_hi=0xAB,
                    interrupts=interrupts)
    cpu.reset()
    cpu.p &= ~cpu.FLAG_I  # allow IRQ
    assert interrupts.nmi_pending is False
    assert interrupts.irq_active is False

    cpu.bus.write(cpu.pc, 0xEA)  # NOP
    interrupts.nmi_pending = True
    interrupts.irq_mapper = True

    cycles = cpu.step_instruction()

    # NMI serviced
    assert cycles == 9
    assert cpu.pc == 0x1234  # NMI vector

    # NMI cleared, IRQ still active
    assert interrupts.nmi_pending is False
    assert interrupts.irq_active is True


# ---------------------------------------------------------------------------
# BRK
# ---------------------------------------------------------------------------

def test_brk_jumps_to_irq_vector():
    """BRK pushes PC+2, pushes status with B=1, sets I, jumps to IRQ vector."""
    cpu = _make_cpu(irq_lo=0xEF, irq_hi=0xBE)
    cpu.reset()
    cpu.p = cpu.FLAG_U  # I=0, U=1, others 0
    initial_sp = cpu.sp

    cpu.bus.write(0x0200, 0x00)  # BRK
    cpu.bus.write(0x0201, 0x00)  # padding (ignored byte)
    cpu.pc = 0x0200

    cycles = cpu.step_instruction()

    assert cpu.pc == 0xBEEF  # from IRQ vector
    assert cycles == 7
    assert cpu.sp == initial_sp - 3
    assert cpu.p & cpu.FLAG_I   # I set
    assert cpu.p & cpu.FLAG_U   # U still set


def test_brk_pushes_b_flag_set():
    """BRK pushes status with B=1 and U=1."""
    cpu = _make_cpu()
    cpu.reset()

    cpu.bus.write(0x0200, 0x00)
    cpu.bus.write(0x0201, 0x00)
    cpu.pc = 0x0200

    cpu.step_instruction()

    pushed_p = cpu.bus.read(0x0100 | ((cpu.sp + 1) & 0xFF))
    assert pushed_p & cpu.FLAG_B  # B must be 1
    assert pushed_p & cpu.FLAG_U  # U must be 1


def test_brk_pushes_pc_plus_two():
    """BRK pushes the address of BRK instruction + 2."""
    cpu = _make_cpu()
    cpu.reset()

    cpu.bus.write(0x0200, 0x00)
    cpu.bus.write(0x0201, 0x00)
    cpu.pc = 0x0200

    cpu.step_instruction()

    # Stack after BRK: P at SP+1, PCL at SP+2, PCH at SP+3
    pushed_lo = cpu.bus.read(0x0100 | ((cpu.sp + 2) & 0xFF))
    pushed_hi = cpu.bus.read(0x0100 | ((cpu.sp + 3) & 0xFF))
    pushed_pc = (pushed_hi << 8) | pushed_lo
    assert pushed_pc == 0x0202  # 0x0200 + 2


# ---------------------------------------------------------------------------
# RTI
# ---------------------------------------------------------------------------

def test_rti_restores_p_and_pc():
    """RTI pulls P and PC from stack, without forcing U bit."""
    interrupts = InterruptLines()
    cpu = _make_cpu(interrupts=interrupts)
    cpu.reset()

    # Set up stack as if an interrupt occurred
    cpu.sp = 0xFA
    cpu.bus.write(0x01FB, 0x00)         # raw P (no U flag)
    cpu.bus.write(0x01FC, 0x34)         # PC lo
    cpu.bus.write(0x01FD, 0x12)         # PC hi

    cpu.bus.write(0x0200, 0x40)  # RTI
    cpu.pc = 0x0200

    cpu.step_instruction()

    assert cpu.p == 0x00       # raw restored, no forced U
    assert cpu.pc == 0x1234


def test_rti_preserves_stack_b_flag():
    """RTI preserves B flag from stack (no mask)."""
    interrupts = InterruptLines()
    cpu = _make_cpu(interrupts=interrupts)
    cpu.reset()

    # Simulate return from BRK: stack has B=1
    cpu.sp = 0xFA
    cpu.bus.write(0x01FB, cpu.FLAG_B | cpu.FLAG_U)  # B=1, U=1
    cpu.bus.write(0x01FC, 0x34)
    cpu.bus.write(0x01FD, 0x12)

    cpu.bus.write(0x0200, 0x40)
    cpu.pc = 0x0200
    cpu.step_instruction()

    assert cpu.p & cpu.FLAG_B  # B flag preserved
    assert cpu.p & cpu.FLAG_U  # U flag preserved
    assert cpu.pc == 0x1234
