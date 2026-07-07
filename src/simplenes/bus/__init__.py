"""PPUBus module — auto-selects Cython or pure Python backend."""
import os

_backend = os.environ.get("SIMPLENES_BACKEND", "")

if _backend == "python":
    # Force pure Python (CI / oracle validation)
    from simplenes.bus.ppu_bus import PPUBus  # noqa: F401
elif _backend == "cython":
    # Force Cython; must fail loudly if not compiled
    from simplenes.bus._ppu_bus_cy import PPUBusCy as PPUBus  # noqa: F401
else:
    try:
        from simplenes.bus._ppu_bus_cy import PPUBusCy as PPUBus  # noqa: F401
    except ImportError:
        from simplenes.bus.ppu_bus import PPUBus  # noqa: F401

__all__ = ["PPUBus"]
