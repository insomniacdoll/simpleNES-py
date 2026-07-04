"""Exception hierarchy for SimpleNES emulator."""


class SimpleNESError(Exception):
    """Base exception for all SimpleNES errors."""


# ROM / Cartridge exceptions
class RomError(SimpleNESError):
    """Base exception for ROM-related errors."""


class InvalidRomError(RomError):
    """ROM format is invalid or unsupported."""


class UnsupportedMapperError(RomError):
    """Requested mapper is not implemented.

    Always constructed with the offending mapper_id for inspection:
        raise UnsupportedMapperError(mapper_id)
    """

    def __init__(self, mapper_id: int) -> None:
        super().__init__(f"Unsupported mapper: {mapper_id}")
        self.mapper_id = mapper_id


class UnsupportedNES2Error(RomError):
    """NES 2.0 format is not yet supported."""


# Emulation exceptions
class EmulationError(SimpleNESError):
    """Base exception for runtime emulation errors."""


class CPUBusError(EmulationError):
    """Invalid CPU bus access."""


class PPUBusError(EmulationError):
    """Invalid PPU bus access."""


class IllegalOpcodeError(EmulationError):
    """CPU encountered an illegal/unofficial opcode.

    Carries the offending opcode and PC for debugging.
    """

    def __init__(self, opcode: int, pc: int) -> None:
        super().__init__(f"Illegal opcode ${opcode:02X} at ${pc:04X}")
        self.opcode = opcode
        self.pc = pc
