"""Helper to build minimal legal .nes files for testing."""


def build_nrom_ines(
    *,
    prg_rom: bytes = b"",
    chr_rom: bytes = b"",
    prg_banks: int = 1,       # 16 KiB banks
    chr_banks: int = 1,       # 8 KiB banks
    mapper_id: int = 0,
    mirroring: int = 0,        # 0=horizontal, 1=vertical, 0x08=four-screen
    has_battery: bool = False,
    prg_ram_banks: int = 0,    # header[8]
    flags7: int = 0,
) -> bytearray:
    """Build a minimal iNES 1.0 ROM.

    Args:
        prg_rom: PRG ROM contents. If empty, filled with 0xEA (NOPs).
        chr_rom: CHR ROM contents. If empty and chr_banks==0, CHR RAM.
        prg_banks: Number of 16 KiB PRG ROM banks.
        chr_banks: Number of 8 KiB CHR ROM banks. 0 = CHR RAM.
        mapper_id: iNES mapper number.
        mirroring: bit0=vertical, bit3=four-screen.
        has_battery: Battery-backed PRG RAM.
        prg_ram_banks: Number of 8 KiB PRG RAM banks (header byte 8).
        flags7: header[7] value (bits 4-7 = mapper upper nibble, bits 2-3 for NES 2.0).
    """
    flags6 = (mapper_id << 4) | mirroring
    if has_battery:
        flags6 |= 0x02

    flags7 = (mapper_id & 0xF0) | (flags7 & 0x0F)

    if not prg_rom:
        prg_rom = b"\xEA" * (prg_banks * 16384)
    if not chr_rom and chr_banks > 0:
        chr_rom = b"\x00" * (chr_banks * 8192)

    header = bytearray(16)
    header[0:4] = b"NES\x1a"
    header[4] = prg_banks & 0xFF
    header[5] = chr_banks & 0xFF
    header[6] = flags6
    header[7] = flags7
    header[8] = prg_ram_banks & 0xFF

    result = bytearray()
    result.extend(header)
    result.extend(prg_rom)
    result.extend(chr_rom)
    return result


def build_nes2_rom() -> bytearray:
    """Build a minimal NES 2.0 ROM (for rejection testing)."""
    header = bytearray(16)
    header[0:4] = b"NES\x1a"
    header[4] = 1  # 1 PRG bank
    header[5] = 1  # 1 CHR bank
    header[6] = 0
    header[7] = 0x08  # NES 2.0 indicator
    header[8] = 0
    header[9] = 0  # NES 2.0: submapper etc.

    result = bytearray()
    result.extend(header)
    result.extend(b"\xEA" * 16384)
    result.extend(b"\x00" * 8192)
    return result
