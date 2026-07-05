"""Unit tests for PygameFrontend.  Skipped if pygame is not installed."""

import os

import pytest

# Must set dummy driver BEFORE importing pygame
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

pygame = pytest.importorskip("pygame", reason="pygame not installed")

from simplenes.frontend.pygame_frontend import PygameFrontend  # noqa: E402


def test_pygame_frontend_rejects_invalid_scale():
    with pytest.raises(ValueError):
        PygameFrontend(scale=0)
    with pytest.raises(ValueError):
        PygameFrontend(scale=5)


def test_pygame_frontend_initially_not_closed():
    frontend = PygameFrontend(scale=1)
    try:
        assert not frontend.should_close()
    finally:
        frontend.close()


def test_pygame_frontend_close_sets_flag():
    frontend = PygameFrontend(scale=1)
    frontend.close()
    assert frontend.should_close()
    # idempotent: second call does not raise
    frontend.close()


def test_present_constructs_correct_rgba_buffer(monkeypatch):
    frontend = PygameFrontend(scale=1)
    try:
        fb = memoryview(bytearray(256 * 240))

        called_args = {}

        def fake_frombuffer(rgba_bytes, size, format):
            called_args["size"] = size
            called_args["format"] = format
            called_args["len"] = len(rgba_bytes)
            return pygame.Surface(size)

        monkeypatch.setattr(frontend._pg.image, "frombuffer", fake_frombuffer)
        monkeypatch.setattr(frontend._pg.display, "flip", lambda: None)
        monkeypatch.setattr(frontend._pg.transform, "scale",
                            lambda s, sz: s)

        frontend.present(fb)
        assert called_args["size"] == (256, 240)
        assert called_args["format"] == "RGBA"
        assert called_args["len"] == 256 * 240 * 4
    finally:
        frontend.close()
