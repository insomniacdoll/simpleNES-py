"""Frontend protocol for NES emulator frontends."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Frontend(Protocol):
    """Protocol for NES emulator frontends (headless, pygame, etc.)."""

    def should_close(self) -> bool: ...

    def poll_input(self) -> int: ...

    def present(self, framebuffer: memoryview) -> None: ...

    def close(self) -> None: ...
