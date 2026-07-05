"""Unit tests for palette + HeadlessFrontend.  Always runs, no pygame needed."""

from simplenes.frontend.palette import (
    PALETTE_RGB, palette_to_rgb, framebuffer_to_rgba,
)
from simplenes.frontend.headless import HeadlessFrontend


# ======================================================================
# Palette
# ======================================================================

def test_palette_length_64():
    assert len(PALETTE_RGB) == 64


def test_palette_index_wraparound():
    assert palette_to_rgb(0) == palette_to_rgb(64)
    assert palette_to_rgb(1) == palette_to_rgb(65)


def test_palette_rgb_range():
    for r, g, b in PALETTE_RGB:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255


def test_framebuffer_to_rgba_length():
    fb = memoryview(bytearray(256 * 240))
    rgba = framebuffer_to_rgba(fb)
    assert len(rgba) == 256 * 240 * 4


def test_framebuffer_to_rgba_alpha_opaque():
    fb = memoryview(bytearray(256 * 240))
    rgba = framebuffer_to_rgba(fb)
    for i in range(0, len(rgba), 4):
        assert rgba[i + 3] == 255


# ======================================================================
# HeadlessFrontend
# ======================================================================

def test_headless_initially_not_closed():
    frontend = HeadlessFrontend()
    assert not frontend.should_close()


def test_headless_poll_input_defaults_zero():
    frontend = HeadlessFrontend()
    assert frontend.poll_input() == 0


def test_headless_set_input_roundtrips():
    frontend = HeadlessFrontend()
    frontend.set_input(0xAB)
    assert frontend.poll_input() == 0xAB


def test_headless_present_does_not_raise():
    frontend = HeadlessFrontend()
    fb = memoryview(bytearray(256 * 240))
    frontend.present(fb)


def test_headless_stop_sets_should_close():
    frontend = HeadlessFrontend()
    frontend.stop()
    assert frontend.should_close()


def test_headless_close_sets_should_close():
    frontend = HeadlessFrontend()
    frontend.close()
    assert frontend.should_close()
