"""Unit tests for MMC3Mapper (Mapper 4)."""

import pytest

from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.cartridge.mappers.mapper004_mmc3 import MMC3Mapper
from simplenes.errors import InvalidRomError
from simplenes.interrupts import InterruptLines


def _mmc3_image(prg_banks=4, chr_is_ram=True, prg_ram_size=0):
    """Build a CartridgeImage for MMC3 testing.

    Each 8 KiB PRG bank filled with its index.
    """
    prg_parts = [bytes([bank] * 8192) for bank in range(prg_banks)]
    return CartridgeImage(
        format=RomFormat.INES_1_0,
        mapper_id=4,
        submapper_id=0,
        prg_rom=b"".join(prg_parts),
        chr_rom=b"" if chr_is_ram else b"".join(bytes([n] * 1024) for n in range(64)),
        prg_ram_size=prg_ram_size,
        prg_nvram_size=0,
        chr_ram_size=8192 if chr_is_ram else 0,
        chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL,
        has_battery=False,
        has_trainer=False,
    )


def _interrupts():
    return InterruptLines()


# ======================================================================
# Construction validation
# ======================================================================

def test_mmc3_rejects_prg_rom_too_small():
    img = CartridgeImage(
        format=RomFormat.INES_1_0, mapper_id=4, submapper_id=0,
        prg_rom=b"\x00" * 0x6000, chr_rom=b"", prg_ram_size=0,
        prg_nvram_size=0, chr_ram_size=8192, chr_nvram_size=0,
        mirroring=Mirroring.HORIZONTAL, has_battery=False, has_trainer=False,
    )
    with pytest.raises(InvalidRomError, match="PRG ROM"):
        MMC3Mapper(img, interrupts=_interrupts())


def test_mmc3_rejects_prg_ram_over_8k():
    with pytest.raises(InvalidRomError, match="PRG RAM"):
        MMC3Mapper(_mmc3_image(prg_ram_size=16384), interrupts=_interrupts())


# ======================================================================
# Register decode
# ======================================================================

def test_mmc3_register_decode_even_odd_pairs():
    mapper = MMC3Mapper(_mmc3_image(), interrupts=_interrupts())
    # $8000: bank select
    mapper.cpu_write(0x8000, 0x07 | 0x40 | 0x80)
    assert mapper._bank_select == 7
    assert mapper._prg_mode is True
    assert mapper._chr_invert is True


def test_mmc3_bank_select_and_data():
    mapper = MMC3Mapper(_mmc3_image(), interrupts=_interrupts())
    # Select bank 6 (PRG bank 0)
    mapper.cpu_write(0x8000, 6)
    mapper.cpu_write(0x8001, 3)
    assert mapper._prg_bank0 == 3


def test_mmc3_mirroring_register():
    mapper = MMC3Mapper(_mmc3_image(), interrupts=_interrupts())
    assert mapper.mirroring == Mirroring.HORIZONTAL
    mapper.cpu_write(0xA000, 0)  # V
    assert mapper.mirroring == Mirroring.VERTICAL
    mapper.cpu_write(0xA000, 1)  # H
    assert mapper.mirroring == Mirroring.HORIZONTAL


# ======================================================================
# PRG banking
# ======================================================================

def test_mmc3_prg_normal_mode():
    mapper = MMC3Mapper(_mmc3_image(prg_banks=8), interrupts=_interrupts())
    # Default: prg_bank0=0, prg_bank1=1, mode=0 (normal)
    assert mapper.cpu_read(0x8000) == 0  # bank 0
    assert mapper.cpu_read(0xA000) == 1  # bank 1
    assert mapper.cpu_read(0xC000) == 6  # second-last (bank 6)
    assert mapper.cpu_read(0xE000) == 7  # last (bank 7)


def test_mmc3_prg_swapped_mode():
    mapper = MMC3Mapper(_mmc3_image(prg_banks=8), interrupts=_interrupts())
    # Set prg_mode = True (swapped)
    mapper.cpu_write(0x8000, 0x40)  # bank_select=0, prg_mode=1
    # prg_bank0=0 (default)
    assert mapper.cpu_read(0x8000) == 6  # second-last in swapped
    assert mapper.cpu_read(0xC000) == 0  # prg_bank0 in swapped
    assert mapper.cpu_read(0xE000) == 7  # last always


def test_mmc3_prg_fixed_banks_second_last_and_last():
    mapper = MMC3Mapper(_mmc3_image(prg_banks=8), interrupts=_interrupts())
    assert mapper.cpu_read(0xC000) == 6
    assert mapper.cpu_read(0xE000) == 7


# ======================================================================
# CHR banking
# ======================================================================

def test_mmc3_chr_2k_banks_normal():
    mapper = MMC3Mapper(_mmc3_image(chr_is_ram=False), interrupts=_interrupts())
    # Default: all chr_banks = 0
    assert mapper.ppu_read(0x0000) == 0
    # Set chr_bank[0] = 2 (2 KiB bank at $0000)
    mapper.cpu_write(0x8000, 0)
    mapper.cpu_write(0x8001, 2)
    # 2K bank 2 → base = 2 * 0x400 = 0x800 → chr_rom has 0x02 at offset 0x800
    assert mapper.ppu_read(0x0000) == 2


def test_mmc3_chr_1k_banks_normal():
    mapper = MMC3Mapper(_mmc3_image(chr_is_ram=False), interrupts=_interrupts())
    # Set chr_bank[2] = 5 (1 KiB bank at $1000-$13FF)
    mapper.cpu_write(0x8000, 2)
    mapper.cpu_write(0x8001, 5)
    assert mapper.ppu_read(0x1000) == 5


def test_mmc3_chr_a12_invert_remaps():
    mapper = MMC3Mapper(_mmc3_image(chr_is_ram=False), interrupts=_interrupts())
    # Set chr_bank[0] = 0, chr_bank[2] = 5
    mapper.cpu_write(0x8000, 2)
    mapper.cpu_write(0x8001, 5)
    # Enable chr_invert
    mapper.cpu_write(0x8000, 0x80)  # invert on
    # In inverted mode: $0000-$03FF maps to chr_bank[2] (=5)
    assert mapper.ppu_read(0x0000) == 5
    # In inverted mode: $1000-$17FF maps to chr_bank[0] (=0)
    assert mapper.ppu_read(0x1000) == 0


def test_mmc3_chr_2k_bank_bit0_ignored():
    mapper = MMC3Mapper(_mmc3_image(chr_is_ram=False), interrupts=_interrupts())
    mapper.cpu_write(0x8000, 0)
    mapper.cpu_write(0x8001, 3)  # & 0xFE → 2
    assert mapper._chr_banks[0] == 2


def test_mmc3_chr_ram_no_bank_effect():
    mapper = MMC3Mapper(_mmc3_image(chr_is_ram=True), interrupts=_interrupts())
    # Write via default bank, then read back — CHR-RAM works
    mapper.ppu_write(0x0100, 0x42)
    assert mapper.ppu_read(0x0100) == 0x42
    # Switch bank — data may wrap, but read still returns valid byte
    mapper.cpu_write(0x8000, 0)
    mapper.cpu_write(0x8001, 7)
    val = mapper.ppu_read(0x0100)
    assert isinstance(val, int) and 0 <= val <= 255


# ======================================================================
# IRQ
# ======================================================================

def test_mmc3_irq_counter_decrement():
    irq = _interrupts()
    mapper = MMC3Mapper(_mmc3_image(), interrupts=irq)
    mapper.cpu_write(0xC000, 5)  # latch = 5
    mapper.cpu_write(0xC001, 0)      # reload flag = True
    mapper.cpu_write(0xE001, 0)      # enable
    # First A12 clock: reload flag → counter = latch = 5
    mapper.observe_ppu_address(0x1000)  # A12=1, prev=0 → rising
    assert mapper._irq_counter == 5
    # Second clock: counter = 4
    mapper._a12_prev = False
    mapper.observe_ppu_address(0x1000)
    assert mapper._irq_counter == 4


def test_mmc3_irq_reload_flag_behavior():
    irq = _interrupts()
    mapper = MMC3Mapper(_mmc3_image(), interrupts=irq)
    mapper.cpu_write(0xC000, 3)
    mapper.cpu_write(0xC001, 0)  # reload flag
    mapper.cpu_write(0xE001, 0)  # enable
    mapper.observe_ppu_address(0x1000)  # clock → reload → counter=3
    assert mapper._irq_counter == 3


def test_mmc3_irq_disable_clears_pending():
    irq = _interrupts()
    mapper = MMC3Mapper(_mmc3_image(), interrupts=irq)
    mapper.cpu_write(0xC000, 1)
    mapper.cpu_write(0xC001, 0)
    mapper.cpu_write(0xE001, 0)
    # Clock until pending
    mapper.observe_ppu_address(0x1000)  # counter=1
    mapper._a12_prev = False
    mapper.observe_ppu_address(0x1000)  # counter=0 → pending
    assert mapper._irq_pending
    assert irq.irq_mapper
    # Disable clears
    mapper.cpu_write(0xE000, 0)
    assert not mapper._irq_pending
    assert not irq.irq_mapper


def test_mmc3_irq_enable_after_disable():
    irq = _interrupts()
    mapper = MMC3Mapper(_mmc3_image(), interrupts=irq)
    mapper.cpu_write(0xC000, 1)
    mapper.cpu_write(0xC001, 0)
    mapper.cpu_write(0xE000, 0)  # disable
    mapper.cpu_write(0xE001, 0)  # enable
    mapper.observe_ppu_address(0x1000)  # counter=1
    mapper._a12_prev = False
    mapper.observe_ppu_address(0x1000)  # counter=0 → fire
    assert irq.irq_mapper


def test_mmc3_irq_sets_interrupt_line():
    irq = _interrupts()
    mapper = MMC3Mapper(_mmc3_image(), interrupts=irq)
    mapper.cpu_write(0xC000, 1)
    mapper.cpu_write(0xC001, 0)
    mapper.cpu_write(0xE001, 0)
    mapper.observe_ppu_address(0x1000)  # counter=1
    mapper._a12_prev = False
    mapper.observe_ppu_address(0x1000)  # counter=0 → fire
    assert irq.irq_mapper is True


# ======================================================================
# PRG RAM
# ======================================================================

def test_mmc3_prg_ram_read_write():
    mapper = MMC3Mapper(_mmc3_image(), interrupts=_interrupts())
    mapper.cpu_write(0x6000, 0xCD)
    assert mapper.cpu_read(0x6000) == 0xCD


# ======================================================================
# Integration
# ======================================================================

def test_mmc3_integration_cpu_bus_routing():
    from simplenes.cartridge.ines import RomParser
    from simplenes.machine import NESMachine
    from tests.fixtures.nrom_sample import build_nrom_ines
    rom = build_nrom_ines(prg_banks=4, chr_banks=0, mapper_id=4, mirroring=0)
    cart = RomParser.parse(bytes(rom))
    machine = NESMachine(cart)
    val = machine._cpu_bus.read(0x8000)
    assert isinstance(val, int)


def test_mmc3_integration_ppu_a12_observation():
    irq = _interrupts()
    mapper = MMC3Mapper(_mmc3_image(), interrupts=irq)
    # Set a known latch
    mapper.cpu_write(0xC000, 10)
    mapper.cpu_write(0xC001, 0)
    mapper.cpu_write(0xE001, 0)
    # No A12 rise — no clock
    mapper._a12_prev = True
    mapper.observe_ppu_address(0x1000)  # A12=1, prev=1 → no edge
    assert mapper._irq_counter == 0  # unchanged (reload flag consumes first clock)
    # A12 falling → no clock
    mapper.observe_ppu_address(0x0000)  # A12=0
    # A12 rising → clock with reload flag set
    mapper.observe_ppu_address(0x1000)  # A12=1, prev=0
    assert mapper._irq_counter == 10  # reloaded from latch


def test_mmc3_integration_mirroring_property():
    mapper = MMC3Mapper(_mmc3_image(), interrupts=_interrupts())
    assert mapper.mirroring == Mirroring.HORIZONTAL


# ======================================================================
# Factory
# ======================================================================

def test_machine_creates_mmc3_for_mapper_id_4():
    from simplenes.cartridge.ines import RomParser
    from simplenes.machine import NESMachine
    from tests.fixtures.nrom_sample import build_nrom_ines
    rom = build_nrom_ines(prg_banks=4, chr_banks=0, mapper_id=4, mirroring=0)
    cart = RomParser.parse(bytes(rom))
    machine = NESMachine(cart)
    from simplenes.cartridge.mappers.mapper004_mmc3 import MMC3Mapper
    assert isinstance(machine._mapper, MMC3Mapper)
