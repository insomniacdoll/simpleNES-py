"""APU (Ricoh 2A03 audio). Phase 7: pulse, triangle, noise, frame counter, mixer."""

from simplenes.apu.frame_counter import FrameCounter
from simplenes.apu.mixer import mix
from simplenes.apu.noise import NoiseChannel
from simplenes.apu.pulse import PulseChannel
from simplenes.apu.triangle import TriangleChannel


class APU:
    CPU_CLOCK_HZ = 1_789_773
    AUDIO_SAMPLE_RATE = 44_100
    CYCLES_PER_SAMPLE = CPU_CLOCK_HZ / AUDIO_SAMPLE_RATE  # ≈ 40.584

    _SAMPLE_BUFFER_SIZE = 4096

    __slots__ = (
        "interrupts",
        "_pulse1", "_pulse2", "_triangle", "_noise",
        "_frame_counter",
        "_sample_buffer",          # list[float], pre-allocated 4096
        "_sample_write",           # int, next write position
        "_sample_read",            # int, next read position
        "_sample_available",       # int, currently readable count
        "_cycle_accum", "_sample_sum", "_sample_count",
    )

    def __init__(self, interrupts, *, region=None):
        self.interrupts = interrupts
        self._pulse1 = PulseChannel()
        self._pulse2 = PulseChannel()
        self._triangle = TriangleChannel()
        self._noise = NoiseChannel()
        self._frame_counter = FrameCounter()
        self._sample_buffer = [0.0] * self._SAMPLE_BUFFER_SIZE
        self._sample_write = 0
        self._sample_read = 0
        self._sample_available = 0
        self._cycle_accum = 0.0
        self._sample_sum = 0.0
        self._sample_count = 0

    # ----------------------------------------------------------------
    # Scheduler interface
    # ----------------------------------------------------------------

    def clock_cpu_cycle(self) -> None:
        """Advance all channels by one CPU cycle."""
        fc = self._frame_counter
        fc.tick()

        # Quarter-frame: envelope + triangle linear counter
        if fc.quarter_frame:
            self._pulse1.tick_envelope()
            self._pulse2.tick_envelope()
            self._noise.tick_envelope()
            self._triangle.tick_linear_counter()

        # Half-frame: length counter + sweep
        if fc.half_frame:
            self._pulse1.tick_length_sweep()
            self._pulse2.tick_length_sweep()
            self._triangle.tick_length()
            self._noise.tick_length()

        # Every-cycle timer
        self._pulse1.tick_timer()
        self._pulse2.tick_timer()
        self._triangle.tick_timer()
        self._noise.tick_timer()

        # Frame IRQ
        if fc.irq:
            self.interrupts.irq_apu_frame = True

        # Mix + downsample
        out = mix(self._pulse1.output, self._pulse2.output,
                  self._triangle.output, self._noise.output)
        self._sample_sum += out
        self._sample_count += 1
        self._cycle_accum += 1.0
        if self._cycle_accum >= self.CYCLES_PER_SAMPLE:
            self._cycle_accum -= self.CYCLES_PER_SAMPLE
            if self._sample_count:
                self._push_sample(self._sample_sum / self._sample_count)
            self._sample_sum = 0.0
            self._sample_count = 0

    # ----------------------------------------------------------------
    # Ring buffer
    # ----------------------------------------------------------------

    def _push_sample(self, val: float) -> None:
        """Write one sample.  Overwrites the oldest if full (FIFO drop)."""
        self._sample_buffer[self._sample_write] = val
        self._sample_write = (self._sample_write + 1) % self._SAMPLE_BUFFER_SIZE
        if self._sample_available < self._SAMPLE_BUFFER_SIZE:
            self._sample_available += 1
        else:
            self._sample_read = (self._sample_read + 1) % self._SAMPLE_BUFFER_SIZE

    # ----------------------------------------------------------------
    # Register interface (called by CPUBus)
    # ----------------------------------------------------------------

    def read_status(self) -> int:
        """$4015 read — returns channel active bits + frame IRQ, clears IRQ."""
        status = 0
        if self._pulse1.length_active:
            status |= 0x01
        if self._pulse2.length_active:
            status |= 0x02
        if self._triangle.length_active:
            status |= 0x04
        if self._noise.length_active:
            status |= 0x08
        # bit 4 = DMC active (always 0 in Phase 7)
        if self.interrupts.irq_apu_frame:
            status |= 0x40
        # bit 7 = DMC IRQ (always 0 in Phase 7)
        self.interrupts.irq_apu_frame = False  # reading $4015 clears frame IRQ
        return status

    def write_register(self, address: int, value: int) -> None:
        """APU register write ($4000-$4017)."""
        reg = address & 0x1F
        if reg <= 0x03:
            self._pulse1.write(reg, value)
        elif reg <= 0x07:
            self._pulse2.write(reg - 4, value)
        elif reg <= 0x0B:
            self._triangle.write(reg - 8, value)
        elif reg <= 0x0F:
            self._noise.write(reg - 0x0C, value)
        # $4010-$4013 DMC — ignored in Phase 7
        elif reg == 0x15:
            self._pulse1.set_enabled(bool(value & 0x01))
            self._pulse2.set_enabled(bool(value & 0x02))
            self._triangle.set_enabled(bool(value & 0x04))
            self._noise.set_enabled(bool(value & 0x08))
        elif reg == 0x17:
            if value & 0x40:
                self.interrupts.irq_apu_frame = False
            imm_q, imm_h = self._frame_counter.write(value)
            if imm_q:
                self._pulse1.tick_envelope()
                self._pulse2.tick_envelope()
                self._noise.tick_envelope()
                self._triangle.tick_linear_counter()
            if imm_h:
                self._pulse1.tick_length_sweep()
                self._pulse2.tick_length_sweep()
                self._triangle.tick_length()
                self._noise.tick_length()

    # ----------------------------------------------------------------
    # Frontend interface
    # ----------------------------------------------------------------

    def read_samples(self, max_count: int) -> list[float]:
        """Consume up to *max_count* samples in FIFO order."""
        count = min(max(0, max_count), self._sample_available)
        result = [0.0] * count
        r = self._sample_read
        for i in range(count):
            result[i] = self._sample_buffer[r]
            r = (r + 1) % self._SAMPLE_BUFFER_SIZE
        self._sample_read = r
        self._sample_available -= count
        return result

    # ----------------------------------------------------------------
    # State management
    # ----------------------------------------------------------------

    def reset(self) -> None:
        """Reset all channels and frame counter to power-on state."""
        self._pulse1.reset()
        self._pulse2.reset()
        self._triangle.reset()
        self._noise.reset()
        self._frame_counter = FrameCounter()
        self._sample_write = 0
        self._sample_read = 0
        self._sample_available = 0
        self._cycle_accum = 0.0
        self._sample_sum = 0.0
        self._sample_count = 0
        self.interrupts.irq_apu_frame = False
