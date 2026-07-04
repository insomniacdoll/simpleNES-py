"""Unit tests for error hierarchy."""

import pytest

from simplenes.errors import (
    CPUBusError,
    EmulationError,
    InvalidRomError,
    PPUBusError,
    RomError,
    SimpleNESError,
    UnsupportedMapperError,
    UnsupportedNES2Error,
)


def test_simplenes_error_is_base():
    """SimpleNESError is the root base exception."""
    assert issubclass(SimpleNESError, Exception)
    assert issubclass(RomError, SimpleNESError)
    assert issubclass(EmulationError, SimpleNESError)


def test_rom_error_hierarchy():
    """InvalidRomError → RomError → SimpleNESError."""
    e = InvalidRomError("test")
    assert isinstance(e, InvalidRomError)
    assert isinstance(e, RomError)
    assert isinstance(e, SimpleNESError)


def test_emulation_error_hierarchy():
    """CPUBusError / PPUBusError → EmulationError → SimpleNESError."""
    e = CPUBusError("test")
    assert isinstance(e, CPUBusError)
    assert isinstance(e, EmulationError)
    assert isinstance(e, SimpleNESError)

    e2 = PPUBusError("test")
    assert isinstance(e2, PPUBusError)
    assert isinstance(e2, EmulationError)


def test_can_catch_all_with_base():
    """All emulator exceptions can be caught with except SimpleNESError."""
    for exc_class in (InvalidRomError, UnsupportedMapperError, UnsupportedNES2Error,
                       CPUBusError, PPUBusError):
        try:
            raise exc_class("test")
        except SimpleNESError:
            pass  # caught correctly
        else:
            pytest.fail(f"{exc_class.__name__} not caught by SimpleNESError")


def test_unsupported_mapper_has_id():
    """UnsupportedMapperError stores the mapper_id."""
    e = UnsupportedMapperError(5)
    assert e.mapper_id == 5
    assert "5" in str(e)
