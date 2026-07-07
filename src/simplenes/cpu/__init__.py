"""CPU module — auto-selects Cython or pure Python backend."""
import os

from simplenes.cpu.cpu import CpuTraceEntry, CpuTraceLogger  # noqa: F401

_backend = os.environ.get("SIMPLENES_BACKEND", "")

if _backend == "python":
    from simplenes.cpu.cpu import CPU  # noqa: F401
elif _backend == "cython":
    from simplenes.cpu._cpu_cy import CPUCy as CPU  # noqa: F401
else:
    try:
        from simplenes.cpu._cpu_cy import CPUCy as CPU  # noqa: F401
    except ImportError:
        from simplenes.cpu.cpu import CPU  # noqa: F401

__all__ = ["CPU", "CpuTraceEntry", "CpuTraceLogger"]
