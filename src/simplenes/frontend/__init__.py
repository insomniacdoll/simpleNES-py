"""NES emulator frontends.

HeadlessFrontend is always available.
PygameFrontend requires pygame (pip install simplenes-py[frontend]).
"""

from simplenes.frontend.protocol import Frontend
from simplenes.frontend.headless import HeadlessFrontend
from simplenes.frontend.palette import PALETTE_RGB, framebuffer_to_rgba, palette_to_rgb

__all__ = [
    "Frontend",
    "HeadlessFrontend",
    "PALETTE_RGB",
    "framebuffer_to_rgba",
    "palette_to_rgb",
]
