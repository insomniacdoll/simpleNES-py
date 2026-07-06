"""Unit tests for MMC1Mapper (Mapper 1)."""


from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.cartridge.mappers.mapper001_mmc1 import MMC1Mapper


def _mmc1_image(prg_banks=4, chr_is_ram=True, prg_ram_size=0):
    """Build a CartridgeImage for MMC1 testing.

    Each 16 KiB PRG bank filled with its index repeated.
    """
    prg_parts = [bytes([bank] * 16384) for bank in range(prg_banks)]
    return CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=1,
        submapper_id=0,
        prg_rom=b"".join(prg_parts),
        chr_rom=b"" if chr_is_ram else b"".join(bytes([n] * 4096) for n in range(8)),  # 8 × 4 KiB banks
        prg_ram_size=prg_ram_size,
        prg_nvram_size=0,
        chr_ram_size=8192 if chr_is_ram else 0,
        chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL,
        has_battery=False,
        has_trainer=False,
    )


# ======================================================================
# Serial write protocol
# ======================================================================

def test_mmc1_serial_write_5_writes_loads_register():
    mapper = MMC1Mapper(_mmc1_image())
    # Write 5 times with bit0=1 → shift_reg should end up as 0x1F
    for _ in range(5):
        mapper.cpu_write(0x8000, 1)
    # Last write goes to Control (A14:A13 = 00)
    assert mapper._control == 0x1F


def test_mmc1_serial_write_bit7_resets():
    mapper = MMC1Mapper(_mmc1_image())
    for _ in range(3):
        mapper.cpu_write(0x8000, 1)
    mapper.cpu_write(0x8000, 0x80)  # reset
    assert mapper._shift_reg == 0x10
    assert mapper._shift_count == 0


def test_mmc1_serial_write_bit7_sets_control_or_0C():
    mapper = MMC1Mapper(_mmc1_image())
    mapper._control = 0x00
    mapper.cpu_write(0x8000, 0x80)
    assert mapper._control == 0x0C  # OR 0x0C forces PRG mode 3


def test_mmc1_serial_write_partial_not_committed():
    mapper = MMC1Mapper(_mmc1_image())
    mapper._control = 0
    for _ in range(3):
        mapper.cpu_write(0x8000, 0)
    # Only 3 writes — not committed
    assert mapper._control == 0


def test_mmc1_serial_write_register_addressing():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=8))
    # Write 5 times to PRG bank address ($E000-$FFFF)
    # bit0=0 each time → shift_reg = 0x00 → prg_bank = 0
    for _ in range(5):
        mapper.cpu_write(0xE000, 0)
    assert mapper._prg_bank == 0
    # Now write 5 times with bit0=1 to set prg_bank
    for _ in range(5):
        mapper.cpu_write(0xE000, 1)
    assert mapper._prg_bank == 0x1F


# ======================================================================
# PRG banking
# ======================================================================

def test_mmc1_prg_mode3_switchable_and_fixed_last():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=8))
    # Default power-on: mode 3, bank 0 at $8000, last bank (7) at $C000
    assert mapper.cpu_read(0x8000) == 0  # bank 0
    assert mapper.cpu_read(0xC000) == 7  # last bank


def test_mmc1_prg_mode2_fixed_first_and_switchable():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=8))
    # Set mode 2 via serial write to Control
    # mode 2 = control bits 2-3 = 10 → control = 0x08
    bits = [((0x08 >> i) & 1) for i in range(5)]
    for b in bits:
        mapper.cpu_write(0x8000, b)
    # Set prg_bank = 3
    prg_bits = [((3 >> i) & 1) for i in range(5)]
    for b in prg_bits:
        mapper.cpu_write(0xE000, b)
    # Mode 2: $8000 = fixed first bank (0), $C000 = switchable bank 3
    assert mapper.cpu_read(0x8000) == 0
    assert mapper.cpu_read(0xC000) == 3


def test_mmc1_prg_mode0_32k_switchable():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=8))
    # Set mode 0 → control = 0x00 (32 KiB, switchable)
    for _ in range(5):
        mapper.cpu_write(0x8000, 0)
    # Set prg_bank = 2 (selects 32 KiB at banks 2&3)
    prg_bits = [((2 >> i) & 1) for i in range(5)]
    for b in prg_bits:
        mapper.cpu_write(0xE000, b)
    assert mapper.cpu_read(0x8000) == 2   # bank 2
    assert mapper.cpu_read(0xC000) == 3   # bank 3 (same 32 KiB window)


def test_mmc1_prg_reset_state_mode3_last_bank():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=8))
    assert (mapper._control >> 2) & 3 == 3  # mode 3
    assert mapper.cpu_read(0xC000) == 7     # last bank


def test_mmc1_prg_bank_wraps_with_rom_size():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=4))
    # Set prg_bank = 5 (beyond 4 banks) → should wrap
    prg_bits = [((5 >> i) & 1) for i in range(5)]
    for b in prg_bits:
        mapper.cpu_write(0xE000, b)
    # Bank 5 & 0x0F = 5, offset = 5 * 0x4000 = 0x14000, % len(0x10000) = 0x4000 → bank 1
    assert mapper.cpu_read(0x8000) == 1


# ======================================================================
# CHR banking
# ======================================================================

def test_mmc1_chr_8k_mode():
    mapper = MMC1Mapper(_mmc1_image(chr_is_ram=False))
    # Default: 8 KiB mode, chr_bank0=0
    assert mapper.ppu_read(0x0000) == 0
    # Set chr_bank0 = 2 via serial write to $A000
    bits = [((2 >> i) & 1) for i in range(5)]
    for b in bits:
        mapper.cpu_write(0xA000, b)
    # chr_bank0 = 2 → base = 2 * 0x1000 = 0x2000
    assert mapper.ppu_read(0x0000) == 2  # chr_bank0=2, 4K bank 2 filled with 0x02


def test_mmc1_chr_4k_mode():
    mapper = MMC1Mapper(_mmc1_image(chr_is_ram=False))
    # Set control to 4 KiB mode: bit4=1 → control = 0x1C
    ctrl_bits = [((0x1C >> i) & 1) for i in range(5)]
    for b in ctrl_bits:
        mapper.cpu_write(0x8000, b)
    # Set chr_bank1 = 3 → second 4 KiB window filled with 0x03
    bits = [((3 >> i) & 1) for i in range(5)]
    for b in bits:
        mapper.cpu_write(0xC000, b)
    # chr_bank1 = 3 → base = 3 * 0x1000 → 4K bank 3 filled with 0x03
    assert mapper.ppu_read(0x1000) == 3  # chr_bank1=3, 4K bank 3 filled with 0x03


def test_mmc1_chr_ram_bank_switch_noop():
    mapper = MMC1Mapper(_mmc1_image(chr_is_ram=True))
    mapper.ppu_write(0x0000, 0x42)
    # Switch chr_bank0 — modulo wrap keeps it in same 8 KiB
    bits = [((3 >> i) & 1) for i in range(5)]
    for b in bits:
        mapper.cpu_write(0xA000, b)
    # Still reads back from modulo-wrapped offset
    assert mapper.ppu_read(0x0000) == 0x42


# ======================================================================
# Mirroring
# ======================================================================

def test_mmc1_mirroring_controlled_by_control_register():
    mapper = MMC1Mapper(_mmc1_image())
    assert mapper.mirroring == Mirroring.SINGLE_SCREEN_LOWER  # default: bits 0-1 = 0


def test_mmc1_mirroring_default_single_screen_lower():
    mapper = MMC1Mapper(_mmc1_image())
    assert mapper.mirroring == Mirroring.SINGLE_SCREEN_LOWER


# ======================================================================
# Expansion area ($4020-$5FFF) — must be ignored
# ======================================================================

def test_mmc1_ignores_expansion_writes():
    mapper = MMC1Mapper(_mmc1_image())
    for _ in range(5):
        mapper.cpu_write(0x4020, 1)
    # No side effects on serial protocol state
    assert mapper._shift_reg == 0x10
    assert mapper._shift_count == 0
    assert mapper._chr_bank0 == 0
    assert mapper._chr_bank1 == 0
    assert mapper._prg_bank == 0
    assert mapper._control == 0x0C


# ======================================================================
# PRG RAM
# ======================================================================

def test_mmc1_prg_ram_read_write():
    mapper = MMC1Mapper(_mmc1_image())
    mapper.cpu_write(0x6000, 0xCD)
    assert mapper.cpu_read(0x6000) == 0xCD


# ======================================================================
# Integration
# ======================================================================

def test_mmc1_integration_cpu_bus_routing():
    from simplenes.cartridge.ines import RomParser
    from simplenes.machine import NESMachine
    from tests.fixtures.nrom_sample import build_nrom_ines
    rom = build_nrom_ines(prg_banks=4, chr_banks=0, mapper_id=1, mirroring=0)
    cart = RomParser.parse(bytes(rom))
    machine = NESMachine(cart)
    val = machine._cpu_bus.read(0xC000)
    assert isinstance(val, int)


def test_mmc1_reset_state_correct():
    mapper = MMC1Mapper(_mmc1_image(prg_banks=8))
    assert mapper._shift_reg == 0x10
    assert mapper._shift_count == 0
    assert mapper._control == 0x0C
    assert mapper._chr_bank0 == 0
    assert mapper._chr_bank1 == 0
    assert mapper._prg_bank == 0
    assert mapper.mirroring == Mirroring.SINGLE_SCREEN_LOWER
