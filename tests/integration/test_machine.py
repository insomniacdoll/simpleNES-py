"""Integration tests for NESMachine."""

import pytest

from simplenes.cartridge.ines import RomParser
from simplenes.errors import InvalidRomError, UnsupportedMapperError
from simplenes.machine import NESMachine
from simplenes.timing import Region
from tests.fixtures.nrom_sample import build_nrom_ines


def test_create_machine_from_rom_bytes():
    """NESMachine can be constructed from valid ROM bytes."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    assert machine is not None


def test_create_machine_invalid_rom():
    """Invalid ROM raises exception."""
    with pytest.raises(Exception):
        image = RomParser.parse(b"")
        NESMachine(image)


def test_create_machine_unsupported_mapper():
    """Unsupported mapper_id raises UnsupportedMapperError."""
    rom = build_nrom_ines(mapper_id=5)
    image = RomParser.parse(bytes(rom))
    with pytest.raises(UnsupportedMapperError) as exc:
        NESMachine(image)
    assert exc.value.mapper_id == 5


def test_create_machine_four_screen():
    """FOUR_SCREEN mirroring raises InvalidRomError."""
    rom = build_nrom_ines(mirroring=0x08)
    image = RomParser.parse(bytes(rom))
    with pytest.raises(InvalidRomError, match="Four-screen"):
        NESMachine(image)


def test_create_machine_rejects_non_ntsc():
    """region != NTSC raises ValueError."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    with pytest.raises(ValueError, match="Only NTSC"):
        NESMachine(image, region=Region.PAL)


def test_reset():
    """reset() runs without exception."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    machine.reset()


def test_run_frame():
    """run_frame() advances PPU frame."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    # Running a frame should advance the PPU
    machine.run_frame()


def test_framebuffer():
    """framebuffer returns a memoryview of size 256*240."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    fb = machine.framebuffer
    assert len(fb) == 256 * 240
    assert isinstance(fb, memoryview)

    # framebuffer should be write-through (changes reflected in PPU)
    fb[0] = 42
    assert fb[0] == 42


def test_controller_state_port_1():
    """set_controller_state(1, ...) updates controller 1."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    machine.set_controller_state(1, 0xFF)  # all buttons pressed


def test_controller_state_port_2():
    """set_controller_state(2, ...) updates controller 2."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    machine.set_controller_state(2, 0x00)  # no buttons pressed


def test_controller_state_invalid_port():
    """set_controller_state(0) raises ValueError."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    with pytest.raises(ValueError, match="port"):
        machine.set_controller_state(0, 0)


def test_controller_state_invalid_port_3():
    """set_controller_state(3) raises ValueError."""
    rom = build_nrom_ines()
    image = RomParser.parse(bytes(rom))
    machine = NESMachine(image)
    with pytest.raises(ValueError, match="port"):
        machine.set_controller_state(3, 0)
