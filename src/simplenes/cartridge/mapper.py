"""Mapper protocol interface."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Mapper(Protocol):
    """Protocol defining the Mapper interface."""

    def cpu_read(self, address: int) -> int: ...

    def cpu_write(self, address: int, value: int) -> None: ...

    def ppu_read(self, address: int) -> int: ...

    def ppu_write(self, address: int, value: int) -> None: ...

    def observe_ppu_address(self, address: int) -> None: ...

    @property
    def mirroring(self) -> "Mirroring": ...  # type: ignore[name-defined]  # noqa: F821
