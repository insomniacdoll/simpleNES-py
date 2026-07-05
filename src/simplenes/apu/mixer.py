"""APU linear mixer — combines 4 channel DAC levels into normalized [0,1] output."""


def mix(pulse1: int, pulse2: int, triangle: int, noise: int) -> float:
    """Mix pulse (square), triangle, and noise DAC levels into one float sample.

    Channels output 0-15 DAC.  Coefficients approximate NES 2A03 linear range.
    """
    pulse_out = 0.00752 * (pulse1 + pulse2)
    tnd_out = 0.00851 * triangle + 0.00494 * noise
    return min(1.0, pulse_out + tnd_out)
