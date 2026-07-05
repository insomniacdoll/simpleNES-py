"""Unit tests for Phase 7 APU: pulse, triangle, noise, frame counter, mixer."""

import pytest

from simplenes.apu.apu import APU
from simplenes.apu.envelope import Envelope
from simplenes.apu.frame_counter import FrameCounter
from simplenes.apu.length_counter import LENGTH_TABLE, LengthCounter
from simplenes.apu.mixer import mix
from simplenes.apu.noise import NoiseChannel
from simplenes.apu.pulse import PulseChannel
from simplenes.apu.triangle import TriangleChannel
from simplenes.interrupts import InterruptLines


def _make_apu() -> APU:
    return APU(interrupts=InterruptLines())


# ======================================================================
# Envelope
# ======================================================================

def test_envelope_constant_volume():
    env = Envelope()
    env.write_control(0x1F)  # loop=0, constant=1, volume=15
    env.tick()
    assert env.output() == 15


def test_envelope_decay_and_loop():
    env = Envelope()
    env.write_control(0x20)  # loop=1, constant=0, period=0
    env.restart()
    # First tick: start → decay=15, divider=0
    env.tick()
    assert env.output() == 15
    # Next ticks with period=0: divider wraps to 0 each time → decay every tick
    for _ in range(15):
        env.tick()
    assert env.output() == 0
    # Should loop back to 15
    env.tick()
    assert env.output() == 15


def test_envelope_restart_only_on_length_timer_high_write():
    """Envelope start flag set ONLY by restart(), NOT by write_control()."""
    env = Envelope()
    env.write_control(0x2F)  # const=0, loop=1, period=15
    # write_control does NOT start decay — decay stays at 0
    assert env.output() == 0
    # restart() sets start flag → next tick resets decay to 15
    env.restart()
    env.tick()
    assert env.output() == 15


# ======================================================================
# LengthCounter
# ======================================================================

def test_length_counter_load_and_decrement():
    lc = LengthCounter()
    lc.write(0)  # LENGTH_TABLE[0] = 10
    assert lc.value == 10
    for _ in range(10):
        lc.tick()
    assert lc.value == 0


def test_length_counter_halt_prevents_decrement():
    lc = LengthCounter()
    lc.write(0)  # value = 10
    lc.set_halt(True)
    lc.tick()
    assert lc.value == 10  # unchanged


def test_length_counter_disable_clears():
    lc = LengthCounter()
    lc.write(0)
    assert lc.value > 0
    lc.set_enabled(False)
    assert lc.value == 0
    lc.write(5)  # write with enabled=False does not reload
    assert lc.value == 0


def test_length_table_known_values():
    assert LENGTH_TABLE[0] == 10
    assert LENGTH_TABLE[1] == 254
    assert LENGTH_TABLE[31] == 30


# ======================================================================
# Frame Counter
# ======================================================================

def test_frame_counter_4step_irq_fires():
    fc = FrameCounter()
    for _ in range(29828):
        fc.tick()
    assert not fc.irq
    fc.tick()  # 29829 → should fire
    assert fc.irq


def test_frame_counter_4step_irq_inhibited():
    fc = FrameCounter()
    fc.write(0x40)  # irq_inhibit=1
    for _ in range(29830):
        fc.tick()
    assert not fc.irq


def test_frame_counter_5step_no_irq():
    fc = FrameCounter()
    fc.write(0x80)  # mode_5step=1
    for _ in range(37282):
        fc.tick()
    assert not fc.irq


def test_frame_counter_5step_immediate_clocks():
    """$4017 write with bit7=1 returns (True, True) immediate flags."""
    fc = FrameCounter()
    imm_q, imm_h = fc.write(0x80)
    assert imm_q is True
    assert imm_h is True
    imm_q, imm_h = fc.write(0x00)
    assert imm_q is False
    assert imm_h is False


def test_frame_counter_mode_switch_resets_cycle():
    fc = FrameCounter()
    for _ in range(1000):
        fc.tick()
    fc.write(0x80)
    assert fc._cycle == 0


# ======================================================================
# Mixer
# ======================================================================

def test_mixer_returns_zero_when_all_muted():
    assert mix(0, 0, 0, 0) == 0.0


def test_mixer_linear_combination():
    v = mix(10, 5, 7, 3)
    expected = 0.00752 * 15 + 0.00851 * 7 + 0.00494 * 3
    assert v == pytest.approx(expected)
    assert 0.0 < v < 0.5


def test_mixer_clamps_to_1():
    """Mixer with max DAC levels gives ~0.427 (never clips to 1.0 at full volume)."""
    v = mix(15, 15, 15, 15)
    assert v == pytest.approx(0.42735, abs=0.01)


# ======================================================================
# Pulse Channel — register writes
# ======================================================================

def test_pulse_duty_volume_write():
    ch = PulseChannel()
    ch.write(0, 0x9F)  # duty=2, halt=0, env_loop=0, const=1, vol=15
    assert ch._duty == 2
    assert ch._envelope.output() == 15


def test_pulse_sweep_register_write():
    ch = PulseChannel()
    ch.write(1, 0x87)  # sweep on, (0x87>>4)&7 = 0, negate=0, shift=7
    assert ch._sweep_enabled is True
    assert ch._sweep_period == 0
    assert ch._sweep_negate is False
    assert ch._sweep_shift == 7


def test_pulse_output_when_muted_is_zero():
    ch = PulseChannel()
    assert ch.output == 0  # length counter = 0


def test_pulse_output_follows_duty_and_envelope():
    ch = PulseChannel()
    ch.write(0, 0x9F)  # const vol=15, duty=2
    ch.write(2, 0xFF)  # timer low = 255
    ch.write(3, 0x7F)  # value>>3 = 15 → LENGTH_TABLE[15] = 14
    assert ch._length.value == 14
    # duty_table[2] = 0b01111000 — bits set for seq 3,4,5,6
    # first output >0 at seq=3: need 3 * 2047 timer ticks (timer_reload = 0x7FF)
    # Run more ticks to let timer advance sequencer past seq 0,1,2
    for _ in range(7000):
        ch.tick_timer()
    assert ch.output > 0, f"Pulse should output non-zero, got seq={ch._seq} output={ch.output}"


def test_pulse_sweep_silences():
    ch = PulseChannel()
    ch.write(0, 0x9F)
    ch.write(1, 0x87)  # sweep enabled, negate=0 (bit3=0), shift=7
    ch.write(2, 0xFF)
    ch.write(3, 0x7F)
    # Force sweep overflow: timer_reload=0x7FF, shift=1, negate=False
    # delta = 1023, new_period = 0x7FF + 1023 = 3070 >= 0x800 → silenced
    ch._timer_reload = 0x7FF
    ch._sweep_shift = 1
    ch._sweep_negate = False
    ch._sweep_enabled = True
    ch._sweep_period = 0
    ch._sweep_divider = 0
    ch._sweep_reload = False
    ch.tick_length_sweep()
    assert ch._silenced, "Sweep should silence channel when period >= $800"


def test_pulse_sweep_recovery_after_valid_write():
    """Sweep-silenced channel can recover when valid timer/sweep values are written."""
    ch = PulseChannel()
    ch.write(0, 0x9F)
    ch.write(1, 0x87)  # sweep enabled
    ch.write(2, 0xFF)
    ch.write(3, 0x7F)
    # Force sweep overflow
    ch._timer_reload = 0x7FF
    ch._sweep_shift = 1
    ch._sweep_negate = False
    ch._sweep_enabled = True
    ch._sweep_period = 0
    ch._sweep_divider = 0
    ch._sweep_reload = False
    ch.tick_length_sweep()
    assert ch._silenced
    # Write a valid timer + disable sweep → should recover
    ch.write(1, 0x00)  # sweep disabled
    assert not ch._silenced, "Channel should recover after valid write disables sweep"


def test_pulse_sweep_does_not_write_negative_timer_reload():
    """Negate sweep on a small reload should silence but NOT corrupt timer."""
    ch = PulseChannel()
    ch.write(1, 0x89)  # sweep enabled, negate, shift=1
    ch._sweep_divider = 0
    ch._sweep_reload = False
    ch.tick_length_sweep()

    assert ch._timer_reload >= 0, f"timer_reload must stay non-negative, got {ch._timer_reload}"
    assert ch._silenced


# ======================================================================
# Triangle Channel
# ======================================================================

def test_triangle_linear_control_write():
    ch = TriangleChannel()
    ch.write(0, 0xFF)  # control_flag=1, reload_value=127
    assert ch._control_flag is True
    assert ch._linear_reload_value == 127


def test_triangle_waveform_output_ramps():
    ch = TriangleChannel()
    ch.write(0, 0xFF)  # control_flag=1, reload_value=127 (non-zero!)
    ch.write(2, 0xFF)
    ch.write(3, 0x7F)  # reload length, set linear_reload_flag
    ch.tick_linear_counter()  # reload linear counter = 127
    assert ch._linear_counter > 0
    # Clock timer many times to advance waveform sequencer
    for _ in range(3000):
        ch.tick_timer()
    # Waveform should cycle — verify some non-zero output
    assert ch.output > 0, f"Triangle should output non-zero, got {ch.output}"


def test_triangle_linear_counter_halts_output_to_zero():
    ch = TriangleChannel()
    ch.write(0, 0x00)  # control_flag=0, reload_value=0
    ch.write(2, 0xFF)
    ch.write(3, 0x7F)
    ch.tick_linear_counter()  # reload → 0
    assert ch._linear_counter == 0
    assert ch.output == 0


# ======================================================================
# Noise Channel
# ======================================================================

def test_noise_period_register_write():
    ch = NoiseChannel()
    ch.write(2, 0x8F)  # mode=1, period_index=15
    assert ch._mode is True
    assert ch._timer_period == 4068


def test_noise_outputs_zero_when_lfsr_bit0_set():
    ch = NoiseChannel()
    ch._lfsr = 1  # bit0 = 1 → output should be 0
    assert ch.output == 0
    ch._lfsr = 0x4002  # bit0 = 0, need length > 0 too
    ch._length.write(0)  # load length
    ch.write(0, 0x1F)    # const vol=15
    assert ch._length.value > 0
    assert ch.output == 15


def test_noise_lfsr_mode0():
    ch = NoiseChannel()
    ch.write(2, 0x00)  # mode=0
    ch._lfsr = 0x4003  # bit0=1, bit1=1
    ch._timer = 0
    ch._timer_period = 1
    ch.tick_timer()
    # feedback = bit0 ^ bit1 = 1 ^ 1 = 0
    # lfsr = (0x4003 >> 1) | (0 << 14) = 0x2001
    assert ch._lfsr == 0x2001


def test_noise_lfsr_mode1():
    ch = NoiseChannel()
    ch.write(2, 0x80)  # mode=1
    ch._lfsr = 0x4041  # bit0=1, bit6=1
    ch._timer = 0
    ch._timer_period = 1
    ch.tick_timer()
    # feedback = bit0 ^ bit6 = 1 ^ 1 = 0
    # lfsr = (0x4041 >> 1) | (0 << 14) = 0x2020
    assert ch._lfsr == 0x2020


# ======================================================================
# APU Integration — $4015 / $4017
# ======================================================================

def test_4015_write_enables_channels():
    apu = _make_apu()
    apu.write_register(0x4015, 0x0F)  # enable all 4 channels
    apu._pulse1._length.write(0)      # load length counter
    assert apu._pulse1.length_active


def test_4015_read_returns_channel_status_and_clears_irq():
    apu = _make_apu()
    # Enable + load pulse1
    apu.write_register(0x4015, 0x01)
    apu._pulse1._length.write(0)
    status = apu.read_status()
    assert status & 0x01  # pulse1 active
    # Frame IRQ should be 0 initially
    assert not (status & 0x40)


def test_4015_read_clears_frame_irq():
    apu = _make_apu()
    apu.interrupts.irq_apu_frame = True
    status = apu.read_status()
    assert status & 0x40  # IRQ was set
    assert not apu.interrupts.irq_apu_frame  # cleared after read


def test_4017_5step_mode_sets_immediate_clocks():
    apu = _make_apu()
    # Load pulse1 so we can observe envelope tick
    apu.write_register(0x4015, 0x01)
    apu._pulse1._envelope.write_control(0x00)
    apu._pulse1._envelope.restart()
    apu._pulse1._envelope.tick()  # start → decay=15
    assert apu._pulse1._envelope.output() == 15

    # Write $4017 with bit7=1: immediate quarter-frame tick
    apu.write_register(0x4017, 0x80)
    # Envelope ticked (divider=0, period=0 → decay decrements)
    assert apu._pulse1._envelope.output() == 14


def test_4017_irq_inhibit_clears_pending_frame_irq():
    """$4017 write with bit6=1 must clear the global APU frame IRQ."""
    apu = _make_apu()
    apu.interrupts.irq_apu_frame = True
    apu.write_register(0x4017, 0x40)
    assert not apu.interrupts.irq_apu_frame


# ======================================================================
# Downsampler
# ======================================================================

def test_downsampler_outputs_about_44100hz():
    """Verify samples are produced at ~44.1kHz rate."""
    apu = _make_apu()
    # Enable pulse1 with constant volume so downsampler gets non-zero input
    apu.write_register(0x4015, 0x01)
    apu._pulse1._length.write(0)
    apu._pulse1._envelope.write_control(0x1F)  # constant vol=15

    # Run enough cycles to produce samples
    cycles_to_run = int(APU.CYCLES_PER_SAMPLE * 10)  # ~406 cycles → ~10 samples
    for _ in range(cycles_to_run):
        apu.clock_cpu_cycle()

    samples = apu.read_samples(100)
    expected = 10
    assert abs(len(samples) - expected) <= 1, (
        f"Expected ~{expected} samples, got {len(samples)}"
    )


def test_apu_reset_clears_state():
    apu = _make_apu()
    apu.write_register(0x4015, 0x01)
    apu._pulse1._length.write(0)
    apu.clock_cpu_cycle()
    apu.reset()
    assert not apu._pulse1.length_active
    assert len(apu._sample_buffer) == 0
    assert apu._cycle_accum == 0.0
    assert apu._sample_sum == 0.0
    assert apu._sample_count == 0
    assert not apu.interrupts.irq_apu_frame
