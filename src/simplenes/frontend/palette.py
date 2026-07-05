"""NES 2C02 NTSC palette — 64 RGB colours + framebuffer conversion."""

PALETTE_RGB: list[tuple[int, int, int]] = [
    (84, 84, 84),       # $00 — grey
    (0, 30, 116),        # $01 — dark blue
    (8, 16, 144),        # $02 — blue
    (48, 0, 136),        # $03 — dark purple
    (68, 0, 100),        # $04 — purple
    (92, 0, 48),         # $05 — dark magenta
    (84, 4, 0),          # $06 — dark red
    (60, 24, 0),         # $07 — brown
    (32, 42, 0),         # $08 — dark green
    (8, 58, 0),          # $09 — green
    (0, 64, 0),          # $0A — dark cyan-green
    (0, 60, 0),          # $0B — green-cyan
    (0, 50, 60),         # $0C — dark cyan
    (0, 0, 0),           # $0D — black (unused)
    (0, 0, 0),           # $0E — black
    (0, 0, 0),           # $0F — black
    (152, 150, 152),     # $10 — light grey
    (8, 76, 196),        # $11 — light blue
    (48, 50, 236),       # $12 — blue
    (92, 30, 228),       # $13 — purple
    (136, 20, 176),      # $14 — magenta
    (160, 20, 100),      # $15 — pink
    (152, 34, 32),       # $16 — red
    (120, 60, 0),        # $17 — orange
    (84, 90, 0),         # $18 — yellow-green
    (40, 114, 0),        # $19 — green
    (8, 124, 0),         # $1A — cyan-green
    (0, 118, 40),        # $1B — turquoise
    (0, 102, 120),       # $1C — cyan
    (0, 0, 0),           # $1D — black (unused)
    (0, 0, 0),           # $1E — black
    (0, 0, 0),           # $1F — black
    (236, 238, 236),     # $20 — white
    (76, 154, 236),      # $21 — pale blue
    (120, 124, 236),     # $22 — light blue
    (176, 98, 236),      # $23 — light purple
    (228, 84, 236),      # $24 — pink
    (236, 88, 180),      # $25 — light pink
    (236, 106, 100),     # $26 — salmon
    (212, 136, 32),      # $27 — light orange
    (160, 170, 0),       # $28 — yellow
    (116, 196, 0),       # $29 — light green
    (76, 208, 32),       # $2A — pale green
    (56, 204, 108),      # $2B — mint
    (56, 180, 204),      # $2C — pale cyan
    (60, 60, 60),        # $2D — dark grey
    (0, 0, 0),           # $2E — black
    (0, 0, 0),           # $2F — black
    (236, 238, 236),     # $30 — white
    (168, 204, 236),     # $31 — pale blue
    (188, 188, 236),     # $32 — lavender
    (212, 178, 236),     # $33 — light lavender
    (236, 174, 236),     # $34 — light pink
    (236, 174, 212),     # $35 — peach
    (236, 180, 176),     # $36 — light salmon
    (228, 196, 144),     # $37 — beige
    (204, 210, 120),     # $38 — pale yellow
    (180, 222, 120),     # $39 — light lime
    (168, 226, 144),     # $3A — pale green
    (152, 226, 180),     # $3B — mint
    (160, 214, 228),     # $3C — pale cyan
    (160, 162, 160),     # $3D — light grey 2
    (0, 0, 0),           # $3E — black
    (0, 0, 0),           # $3F — black
]


def palette_to_rgb(index: int) -> tuple[int, int, int]:
    """Map palette index (0–63) to 8-bit RGB tuple."""
    return PALETTE_RGB[index & 0x3F]


def framebuffer_to_rgba(framebuffer: memoryview) -> bytes:
    """Convert palette-index framebuffer (256×240) to RGBA bytes (256×240×4).

    Input must be at least 61440 bytes (256×240).
    """
    buf = bytearray(256 * 240 * 4)
    for i in range(256 * 240):
        r, g, b = PALETTE_RGB[framebuffer[i] & 0x3F]
        offset = i * 4
        buf[offset] = r
        buf[offset + 1] = g
        buf[offset + 2] = b
        buf[offset + 3] = 255
    return bytes(buf)
