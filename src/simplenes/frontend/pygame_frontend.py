"""Pygame frontend — window, input, scaling, frame pacing, screenshot."""

from __future__ import annotations

from datetime import datetime

from simplenes.frontend.palette import framebuffer_to_rgba
from simplenes.machine import NESMachine


class PygameFrontend:
    __slots__ = (
        "_scale", "_title", "_should_close",
        "_pg",           # lazy-loaded pygame module reference
        "_screen",       # pygame.Surface
        "_clock",        # pygame.time.Clock
        "_keymap",       # dict[int, int] — pygame keycode → NES button bit
    )

    def __init__(self, *, scale: int = 2, title: str = "simpleNES") -> None:
        if not isinstance(scale, int) or not (1 <= scale <= 4):
            raise ValueError(f"scale must be 1-4, got {scale}")
        self._scale = scale
        self._title = title
        self._should_close = False

        import pygame
        self._pg = pygame
        self._pg.init()
        self._pg.display.set_caption(title)
        self._screen = self._pg.display.set_mode(
            (256 * scale, 240 * scale)
        )
        self._clock = self._pg.time.Clock()
        self._keymap = self._build_keymap(self._pg)

    # ----------------------------------------------------------------
    # Protocol methods
    # ----------------------------------------------------------------

    def should_close(self) -> bool:
        return self._should_close

    def poll_input(self) -> int:
        keys = self._pg.key.get_pressed()
        state = 0
        for keycode, bit in self._keymap.items():
            if keys[keycode]:
                state |= bit
        return state

    def present(self, framebuffer: memoryview) -> None:
        rgba = framebuffer_to_rgba(framebuffer)
        surface = self._pg.image.frombuffer(rgba, (256, 240), "RGBA")
        if self._scale != 1:
            surface = self._pg.transform.scale(
                surface, (256 * self._scale, 240 * self._scale)
            )
        self._screen.blit(surface, (0, 0))
        self._pg.display.flip()

    def close(self) -> None:
        """Idempotent close — does nothing if already closed."""
        if self._should_close:
            return
        self._should_close = True
        self._pg.quit()

    # ----------------------------------------------------------------
    # Event handling (public — called by runner)
    # ----------------------------------------------------------------

    def process_events(self, framebuffer: memoryview) -> None:
        """Process pygame events: QUIT closes window; F12 takes screenshot."""
        for event in self._pg.event.get():
            if event.type == self._pg.QUIT:
                self.close()
            elif event.type == self._pg.KEYDOWN:
                if event.key == self._pg.K_F12:
                    self.take_screenshot(framebuffer)

    def tick(self, fps: int = 60) -> None:
        """Frame pacing: limit to `fps` frames per second."""
        self._clock.tick(fps)

    # ----------------------------------------------------------------
    # Screenshot
    # ----------------------------------------------------------------

    def take_screenshot(self, framebuffer: memoryview) -> None:
        """Save current framebuffer as a PNG screenshot."""
        rgba = framebuffer_to_rgba(framebuffer)
        surface = self._pg.image.fromstring(rgba, (256, 240), "RGBA")
        filename = datetime.now().strftime("screenshot-%Y-%m-%d-%H%M%S.png")
        self._pg.image.save(surface, filename)

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------

    @staticmethod
    def _build_keymap(pg) -> dict:
        return {
            pg.K_z:      0x01,   # A
            pg.K_x:      0x02,   # B
            pg.K_RSHIFT: 0x04,   # Select
            pg.K_RETURN: 0x08,   # Start
            pg.K_UP:     0x10,   # Up
            pg.K_DOWN:   0x20,   # Down
            pg.K_LEFT:   0x40,   # Left
            pg.K_RIGHT:  0x80,   # Right
        }


def run(machine: NESMachine, frontend: PygameFrontend, *, fps: int = 60) -> None:
    """Main emulation loop: input → emulate → present → pace.

    Stops when frontend.should_close() returns True (window closed, etc.).
    """
    while not frontend.should_close():
        frontend.process_events(machine.framebuffer)
        if frontend.should_close():
            break

        state1 = frontend.poll_input()
        machine.set_controller_state(1, state1)
        machine.set_controller_state(2, 0)

        machine.run_frame()
        frontend.present(machine.framebuffer)
        frontend.tick(fps)
