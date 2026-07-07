"""In-place build of Cython PPU extension for benchmarking.

Usage:
    uv run python scripts/build_cython.py
"""
from Cython.Build import cythonize
from setuptools import Extension, setup

extensions = [
    Extension(
        "simplenes.ppu._ppu_cy",
        ["src/simplenes/ppu/_ppu_cy.pyx"],
    ),
]

setup(
    package_dir={"": "src"},
    name="simplenes-cython-extensions",
    ext_modules=cythonize(
        extensions,
        language_level="3",
        annotate=True,
        build_dir="build/cython",
        compiler_directives={
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
    ),
    script_args=["build_ext", "--inplace"],
)
