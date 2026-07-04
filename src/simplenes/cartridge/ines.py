"""iNES 1.0 ROM parser."""

from simplenes.cartridge.image import CartridgeImage, Mirroring, RomFormat
from simplenes.errors import InvalidRomError, UnsupportedNES2Error


class RomParser:
    """Static ROM parser. No instances needed."""

    INES_MAGIC = b"NES\x1a"

    @staticmethod
    def parse(data: bytes) -> CartridgeImage:
        """Parse ROM bytes into CartridgeImage.

        Parser only extracts static ROM information.
        Mapper support validation is handled by NESMachine.

        Raises:
            InvalidRomError: Bad magic, corrupted header, truncated data.
            UnsupportedNES2Error: NES 2.0 format detected.
        """
        if len(data) < 16:
            raise InvalidRomError("ROM too small")

        if data[0:4] != RomParser.INES_MAGIC:
            raise InvalidRomError("Invalid NES header")

        prg_rom_size = data[4] * 16384
        chr_rom_size = data[5] * 8192

        flags6 = data[6]
        flags7 = data[7]

        # NES 2.0 detection
        if (flags7 & 0x0C) == 0x08:
            raise UnsupportedNES2Error("NES 2.0 is not yet supported")

        # Mirroring: four-screen bit3 has priority over vertical bit0
        if flags6 & 0x08:
            mirroring = Mirroring.FOUR_SCREEN
        elif flags6 & 0x01:
            mirroring = Mirroring.VERTICAL
        else:
            mirroring = Mirroring.HORIZONTAL

        mapper_id = (flags7 & 0xF0) | (flags6 >> 4)
        has_battery = bool(flags6 & 0x02)
        has_trainer = bool(flags6 & 0x04)

        # PRG RAM total size (iNES 1.0 header byte 8)
        prg_ram_banks = data[8]
        prg_ram_total = 8192 if prg_ram_banks == 0 else prg_ram_banks * 8192

        if prg_rom_size == 0:
            raise InvalidRomError("No PRG ROM")

        # Battery-backed PRG RAM separation
        if has_battery:
            prg_ram_size = 0
            prg_nvram_size = prg_ram_total
        else:
            prg_ram_size = prg_ram_total
            prg_nvram_size = 0

        # CHR RAM size
        if chr_rom_size == 0:
            chr_ram_size = 8192
        else:
            chr_ram_size = 0
        chr_nvram_size = 0  # Phase 1: CHR NVRAM not implemented

        # Trainer offset
        offset = 16
        if has_trainer:
            offset += 512

        # Validate total length
        expected_length = offset + prg_rom_size + chr_rom_size
        if len(data) < expected_length:
            raise InvalidRomError("ROM data truncated")

        # Force bytes to ensure CartridgeImage immutability even when
        # the caller passes a bytearray.
        prg_rom = bytes(data[offset:offset + prg_rom_size])
        chr_rom = bytes(data[offset + prg_rom_size:offset + prg_rom_size + chr_rom_size])

        return CartridgeImage(
            format=RomFormat.INES_1_0,
            mapper_id=mapper_id,
            submapper_id=0,
            prg_rom=prg_rom,
            chr_rom=chr_rom,
            prg_ram_size=prg_ram_size,
            prg_nvram_size=prg_nvram_size,
            chr_ram_size=chr_ram_size,
            chr_nvram_size=chr_nvram_size,
            mirroring=mirroring,
            has_battery=has_battery,
            has_trainer=has_trainer,
        )
