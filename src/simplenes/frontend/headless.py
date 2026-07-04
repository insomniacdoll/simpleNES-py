"""Headless frontend for CI, testing, traces, benchmarks."""


class HeadlessFrontend:
    def __init__(self) -> None:
        self._should_close = False
        self._input_state = 0

    def should_close(self) -> bool:
        return self._should_close

    def poll_input(self) -> int:
        return self._input_state

    def present(self, framebuffer: memoryview) -> None:
        pass  # no rendering

    def close(self) -> None:
        self._should_close = True

    def stop(self) -> None:
        self._should_close = True

    def set_input(self, state: int) -> None:
        self._input_state = state & 0xFF
