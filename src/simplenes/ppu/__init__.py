"""PPU module — auto-selects Cython or pure Python backend."""
import os

_backend = os.environ.get("SIMPLENES_BACKEND", "")

if _backend == "python":
    # Force pure Python (CI / oracle validation)
    from simplenes.ppu.ppu import PPU  # noqa: F401
elif _backend == "cython":
    # Force Cython; must fail loudly if not compiled
    from simplenes.ppu._ppu_cy import PPUCy as PPU  # noqa: F401
else:
    try:
        from simplenes.ppu._ppu_cy import PPUCy as PPU  # noqa: F401
    except ImportError:
        from simplenes.ppu.ppu import PPU  # noqa: F401

__all__ = ["PPU"]
