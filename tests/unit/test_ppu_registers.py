"""Unit tests for PPU registers: PPUCTRL/PPUMASK/PPUSTATUS/PPUSCROLL/PPUADDR/PPUDATA/OAM/NMI."""


from simplenes.ppu.ppu import PPU
from simplenes.bus.ppu_bus import PPUBus
from simplenes.cartridge.image import Mirroring
from simplenes.interrupts import InterruptLines


class _FakeMapper:
    mirroring = Mirroring.HORIZONTAL

    def observe_ppu_address(self, address):
        pass

    def ppu_read(self, address):
        return 0

    def ppu_write(self, address, value):
        pass


def _make_ppu():
    """Create a PPU wired to a simple PPUBus for register testing."""
    mapper = _FakeMapper()
    bus = PPUBus(mapper)
    interrupts = InterruptLines()
    return PPU(bus=bus, interrupts=interrupts)


# ======================================================================
# PPUCTRL
# ======================================================================

def test_ppuctrl_name_table_bits():
    """PPUCTRL bits 1-0 → t bits 11-10."""
    ppu = _make_ppu()
    ppu.t = 0x0000
    ppu.write_register(0x2000, 0x03)  # name table = $2C00 (bits 1-0 = 11)
    assert (ppu.t >> 10) & 3 == 3


def test_ppuctrl_increment_mode():
    """PPUCTRL bit 2 controls PPUDATA increment."""
    ppu = _make_ppu()
    ppu.write_register(0x2000, 0x00)  # increment = 1
    assert ppu._increment() == 1

    ppu.write_register(0x2000, 0x04)  # increment = 32
    assert ppu._increment() == 32


# ======================================================================
# PPUSTATUS
# ======================================================================

def test_ppustatus_vblank_flag():
    """VBlank flag set at scanline 241 dot 1, cleared at 261 dot 1."""
    ppu = _make_ppu()

    # Advance to scanline 241, dot 1
    ppu.scanline = 241
    ppu.dot = 0
    ppu.clock()  # dot 1
    assert ppu.status & 0x80

    # Advance to scanline 261, dot 1
    ppu.scanline = 261
    ppu.dot = 0
    ppu.clock()
    assert (ppu.status & 0xE0) == 0


def test_ppustatus_clear_on_read():
    """Reading PPUSTATUS clears VBlank flag and write_toggle."""
    ppu = _make_ppu()
    ppu.status = 0xFF
    ppu.write_toggle = True

    result = ppu.read_register(0x2002)
    assert (result & 0xE0) == 0xE0           # top 3 bits returned
    assert (ppu.status & 0x80) == 0          # VBlank cleared
    assert ppu.write_toggle is False


def test_prerender_clears_status_flags():
    """Pre-render line (261) dot 1 clears status bits 7/6/5."""
    ppu = _make_ppu()
    ppu.status = 0xE0
    ppu.scanline = 261
    ppu.dot = 0
    ppu.clock()
    assert (ppu.status & 0xE0) == 0


# ======================================================================
# NMI
# ======================================================================

def _simulate_vblank(ppu):
    """Advance PPU to the VBlank boundary (scanline 241, dot 1)."""
    ppu.scanline = 241
    ppu.dot = 0
    ppu.clock()


def test_ppuctrl_nmi_enable():
    """VBlank with PPUCTRL bit 7 set triggers NMI."""
    ppu = _make_ppu()
    ppu.write_register(0x2000, 0x80)  # NMI enable

    assert ppu.interrupts.nmi_pending is False
    _simulate_vblank(ppu)
    assert ppu.interrupts.nmi_pending is True


def test_ppuctrl_nmi_disable():
    """VBlank with PPUCTRL bit 7 clear does NOT trigger NMI."""
    ppu = _make_ppu()
    ppu.write_register(0x2000, 0x00)  # NMI disabled

    _simulate_vblank(ppu)
    assert ppu.interrupts.nmi_pending is False


def test_ppuctrl_nmi_late_enable():
    """Enabling NMI during VBlank (VBlank already set) fires immediately."""
    ppu = _make_ppu()

    # Trigger VBlank first (NMI not enabled yet)
    ppu.write_register(0x2000, 0x00)
    _simulate_vblank(ppu)
    assert ppu.interrupts.nmi_pending is False
    assert ppu.status & 0x80  # VBlank still set

    # Now enable NMI while VBlank is active → should fire
    ppu.write_register(0x2000, 0x80)
    assert ppu.interrupts.nmi_pending is True


def test_ppustatus_read_prevents_late_nmi():
    """Reading PPUSTATUS during VBlank clears the rising edge condition."""
    ppu = _make_ppu()
    ppu.write_register(0x2000, 0x00)
    _simulate_vblank(ppu)
    assert ppu.status & 0x80  # VBlank set

    # Read PPUSTATUS → clears VBlank, updates _nmi_prev to False
    ppu.read_register(0x2002)
    assert (ppu.status & 0x80) == 0

    # Enable NMI after VBlank cleared → should NOT fire
    ppu.write_register(0x2000, 0x80)
    assert ppu.interrupts.nmi_pending is False


def test_vblank_nmi_timing():
    """NMI fires only once per VBlank; clearing nmi_pending allows next frame."""
    ppu = _make_ppu()
    ppu.write_register(0x2000, 0x80)

    # First VBlank
    _simulate_vblank(ppu)
    assert ppu.interrupts.nmi_pending is True

    # Simulate CPU servicing NMI (clearing pending)
    ppu.interrupts.nmi_pending = False

    # Same VBlank, enable again → no new edge (already set)
    ppu._update_nmi()
    assert ppu.interrupts.nmi_pending is False

    # Next frame: clear VBlank, then set again
    ppu.scanline = 261
    ppu.dot = 0
    ppu.clock()  # clears VBlank
    ppu.scanline = 241
    ppu.dot = 0
    ppu.clock()  # re-sets VBlank
    assert ppu.interrupts.nmi_pending is True


# ======================================================================
# PPUADDR
# ======================================================================

def test_ppuaddr_first_write():
    """PPUADDR first write → t bits 14-8, toggle=1."""
    ppu = _make_ppu()
    ppu.t = 0
    ppu.write_register(0x2006, 0x3F)  # high byte (low 6 bits)
    assert ppu.t == 0x3F00
    assert ppu.write_toggle is True


def test_ppuaddr_second_write():
    """PPUADDR second write → t bits 7-0, v=t, toggle=0."""
    ppu = _make_ppu()
    ppu.t = 0
    ppu.write_register(0x2006, 0x12)  # high
    ppu.write_register(0x2006, 0x34)  # low
    assert ppu.t == 0x1234
    assert ppu.v == 0x1234
    assert ppu.write_toggle is False


# ======================================================================
# PPUSCROLL
# ======================================================================

def test_ppuscroll_first_write():
    """PPUSCROLL first write → coarse X + fine_x."""
    ppu = _make_ppu()
    ppu.t = 0x0000
    ppu.write_register(0x2005, 0x7D)
    # value=0x7D → coarse_X = 0x7D >> 3 = 0x0F, fine_X = 0x7D & 7 = 5
    assert ppu.fine_x == 5
    assert ppu.t == 0x000F
    assert ppu.write_toggle is True


def test_ppuscroll_second_write():
    """PPUSCROLL second write → coarse Y + fine Y, toggle back."""
    ppu = _make_ppu()
    ppu.t = 0x0000
    ppu.write_register(0x2005, 0x00)  # first: coarse X=0
    ppu.write_register(0x2005, 0xE5)  # second: value=0xE5
    # fine Y = 0xE5 & 7 = 5 → bits 14-12 of t
    # coarse Y = (0xE5 & 0xF8) >> 3 = 0x1C → bits 9-5 of t
    # fine_y_bits = (5 << 12) = 0x5000
    # coarse_y_bits = (0x1C << 5) = 0x0380
    assert ppu.t == 0x5380
    assert ppu.write_toggle is False


def test_ppuscroll_toggle():
    """Two PPUSCROLL writes reset toggle."""
    ppu = _make_ppu()
    ppu.write_register(0x2005, 0)
    ppu.write_register(0x2005, 0)
    assert ppu.write_toggle is False


def test_ppuscroll_ppuaddr_interleave():
    """PPUSCROLL and PPUADDR share write_toggle."""
    ppu = _make_ppu()
    ppu.write_register(0x2005, 0)   # first scroll
    assert ppu.write_toggle is True
    ppu.write_register(0x2006, 0)   # treated as second write → toggle=0
    assert ppu.write_toggle is False


# ======================================================================
# PPUDATA
# ======================================================================

def test_ppudata_read_buffer():
    """PPUDATA read from $0000-$3EFF returns delayed read_buffer."""
    ppu = _make_ppu()
    ppu.bus.write(0x2000, 0xAB)  # write to nametable
    ppu.v = 0x2000

    # First read returns old read_buffer (0), fills buffer with 0xAB
    val1 = ppu.read_register(0x2007)
    assert val1 == 0
    # Second read returns 0xAB (from buffer), fills buffer with next byte
    val2 = ppu.read_register(0x2007)
    assert val2 == 0xAB


def test_ppudata_read_palette():
    """PPUDATA read from $3F00+ returns immediately, buffer filled from nametable beneath."""
    ppu = _make_ppu()
    ppu.bus.write(0x3F00, 0x0F)   # palette entry
    ppu.bus.write(0x2F00, 0x55)   # nametable mirror beneath palette area
    ppu.v = 0x3F00

    result = ppu.read_register(0x2007)
    assert result == 0x0F          # immediate return
    assert ppu.read_buffer == 0x55  # buffer from nametable mirror


def test_ppudata_write_increment():
    """PPUDATA write increments v by 1 or 32."""
    ppu = _make_ppu()
    ppu.v = 0x2000

    # Increment = 1 (default)
    ppu.write_register(0x2007, 0x42)
    assert ppu.v == 0x2001

    # Increment = 32
    ppu.write_register(0x2000, 0x04)
    ppu.write_register(0x2007, 0x42)
    assert ppu.v == 0x2021


def test_ppudata_read_increment():
    """PPUDATA read increments v."""
    ppu = _make_ppu()
    ppu.v = 0x2000
    ppu.bus.write(0x2000, 0x00)  # ensure data exists

    ppu.read_register(0x2007)     # v += 1
    assert ppu.v == 0x2001


# ======================================================================
# OAM
# ======================================================================

def test_oamaddr_write():
    """OAMADDR ($2003) sets oam_address."""
    ppu = _make_ppu()
    ppu.write_register(0x2003, 0xAB)
    assert ppu.oam_address == 0xAB


def test_oamdata_read():
    """OAMDATA ($2004) reads from OAM at oam_address."""
    ppu = _make_ppu()
    ppu.oam[0x10] = 0xEE
    ppu.oam_address = 0x10
    assert ppu.read_register(0x2004) == 0xEE


def test_oamdata_write_inc():
    """OAMDATA write updates OAM and increments address."""
    ppu = _make_ppu()
    ppu.oam_address = 0x00
    ppu.write_register(0x2004, 0x77)
    assert ppu.oam[0x00] == 0x77
    assert ppu.oam_address == 0x01


# ======================================================================
# PPUMASK
# ======================================================================

def test_ppumask_store():
    """PPUMASK write stores value."""
    ppu = _make_ppu()
    ppu.write_register(0x2001, 0x1E)
    assert ppu.mask == 0x1E


# ======================================================================
# NMI integration: PPU → InterruptLines → CPU
# ======================================================================

def test_vblank_nmi_flow():
    """Full pipeline: PPU VBlank → nmi_pending → CPU NMI service → vector jump."""
    from simplenes.cpu.cpu import CPU
    from simplenes.bus.cpu_bus import CPUBus
    from simplenes.apu.apu import APU
    from simplenes.cartridge.ines import RomParser
    from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper
    from simplenes.dma.oam_dma import OAMDMAState
    from simplenes.input.controller import Controller
    from tests.fixtures.nrom_sample import build_nrom_ines

    interrupts = InterruptLines()
    ppu_bus = PPUBus(_FakeMapper())

    # Build CPU with a ROM that has known NMI vector
    prg_size = 32768
    raw = build_nrom_ines(prg_rom=bytes(bytearray(prg_size)), prg_banks=2)
    prg = bytearray(raw[16:16 + prg_size])  # PRG ROM only, not CHR
    # NMI vector at offset prg_size-6: $FFFA=lo, $FFFB=hi
    prg[-6 + 0] = 0xCD  # NMI vector lo
    prg[-6 + 1] = 0xAB  # NMI vector hi
    raw = build_nrom_ines(prg_rom=bytes(prg), prg_banks=2)

    image = RomParser.parse(bytes(raw))
    mapper = NROMMapper(image)
    ppt = PPU(bus=ppu_bus, interrupts=interrupts)
    apu = APU(interrupts=interrupts)
    bus = CPUBus(ppu=ppt, apu=apu, mapper=mapper,
                 controller1=Controller(), controller2=Controller(),
                 oam_dma_state=OAMDMAState())
    cpu = CPU(bus=bus, interrupts=interrupts)
    cpu.reset()

    # Enable NMI in PPU
    cpu.bus.write(0x2000, 0x80)  # PPUCTRL bit 7 = NMI enable

    # Trap PC before NMI by executing a NOP
    cpu.bus.write(cpu.pc, 0xEA)  # NOP at current PC

    # Advance PPU to VBlank
    ppt.scanline = 241
    ppt.dot = 0
    ppt.clock()  # sets VBlank flag + nmi_pending

    assert interrupts.nmi_pending is True

    # Execute one CPU instruction → NMI should be served
    cycles = cpu.step_instruction()
    # 2 (NOP) + 7 (NMI) = 9
    assert cycles == 9
    assert cpu.pc == 0xABCD  # jumped to NMI vector
    assert interrupts.nmi_pending is False  # cleared by CPU service
    # SP decreased by 3 (push word + push status)
    assert cpu.sp == 0xFA
    # I flag set after NMI
    assert cpu.p & cpu.FLAG_I
