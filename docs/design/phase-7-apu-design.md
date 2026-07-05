# Phase 7：APU 音频处理单元详细实现设计

## Summary

Phase 0–6 已完成：CPU 指令执行、PPU 背景+精灵渲染、controller 输入、OAM DMA、pygame 图形前端。当前 APU 为静音 stub——寄存器读写和时钟接口存在，但无实际音频输出。

Phase 7 实现 NES 2A03 APU 的核心音频通道和 frame counter。通道以 **0–15 DAC level** 输出，线性混音器归一到 `[0, 1]`。APU 内部降采样到 44.1 kHz 后以 mono float 输出供前端消费。DMC 和非线性混音留待后续。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/apu/apu.py` | **重写** — 从 stub 升级为完整 APU |
| `src/simplenes/apu/pulse.py` | **新建** — 矩形波通道（Pulse 1/2 共享实现） |
| `src/simplenes/apu/triangle.py` | **新建** — 三角波通道 |
| `src/simplenes/apu/noise.py` | **新建** — 噪声通道 |
| `src/simplenes/apu/frame_counter.py` | **新建** — APU Frame Counter（4-step / 5-step） |
| `src/simplenes/apu/envelope.py` | **新建** — 包络发生器（Pulse/Noise 共用） |
| `src/simplenes/apu/length_counter.py` | **新建** — 长度计数器（Pulse/Triangle/Noise 共用） |
| `src/simplenes/apu/mixer.py` | **新建** — 线性混音器 |
| `src/simplenes/apu/__init__.py` | **修改** — 暴露 APU 类 |
| `src/simplenes/bus/cpu_bus.py` | **不变** |
| `src/simplenes/scheduler.py` | **不变** |
| `src/simplenes/interrupts.py` | **不变** |
| `src/simplenes/machine.py` | **修改** — 暴露 `audio_sample_rate`、`read_audio_samples()`；`reset()` 顺序调整为 PPU/APU/DMA/CPU |
| `tests/unit/test_apu.py` | **新建** — APU 通道与寄存器单元测试 |

---

## Architecture Decisions

### AD-7.1: 按 CPU cycle 推进，APU 内部降采样到 44.1 kHz

```python
NTSC CPU 时钟:       1_789_773 Hz
目标采样率:             44_100 Hz
每 sample CPU cycles:  1_789_773 / 44_100 ≈ 40.584
```

Fractional accumulator 降采样：

```python
CYCLES_PER_SAMPLE = CPU_CLOCK_HZ / AUDIO_SAMPLE_RATE

if self._cycle_accum >= self.CYCLES_PER_SAMPLE:
    self._cycle_accum -= self.CYCLES_PER_SAMPLE
    if self._sample_count:
        self._sample_buffer.append(self._sample_sum / self._sample_count)
    self._sample_sum = 0.0
    self._sample_count = 0
```

- 使用 `if`（非 `while`），因为每次只加 1 cycle，不可能越过两次阈值。
- `_sample_count` guard 防止除零。
- 每帧输出 ≈ 735 samples（44,100/60）。
- Buffer `deque(maxlen=4096)` ≈ 93 ms 缓冲。

### AD-7.2: 通道输出为 0–15 DAC level

各通道输出 **非负整数 0–15**，代表 NES 2A03 DAC 振幅。混音器负责将这些值转换为归一化 float `[0, 1]`。

- Pulse: `envelope_output`（当 duty bit = 1 时），否则 `0`
- Triangle: waveform 值 `0–15`
- Noise: `envelope_output`（当 LFSR bit0 = 0 时），否则 `0`

### AD-7.3: 线性混音器

```python
def mix(p1: int, p2: int, tri: int, noise: int) -> float:
    pulse = 0.00752 * (p1 + p2)
    tnd   = 0.00851 * tri + 0.00494 * noise
    return min(1.0, pulse + tnd)
```

输出范围 `[0.0, 1.0]`。若前端需有符号，由音频消费端转换。

### AD-7.4: DMC non-goal

DMC ($4010-$4013) 不实现。`$4015` bit 4、bit 7 始终为 0。

### AD-7.5: Frame Counter 完整模式

- **4-step** (`$4017 bit 7 = 0`): 7457/14913/22371/29829。若未 inhibit 则产生 IRQ。
- **5-step** (`$4017 bit 7 = 1`): 7457/14913/22371/37281。无 IRQ。
- 写 `$4017` 且 bit 7 = 1 时 immediate 时钟 quarter + half frame。

---

## Data Model Changes

### 1. `clock_cpu_cycle()` (整体流程)

```python
def clock_cpu_cycle(self) -> None:
    fc = self._frame_counter
    fc.tick()

    # ----- Quarter-frame -----
    if fc.quarter_frame:
        self._pulse1.tick_envelope()
        self._pulse2.tick_envelope()
        self._noise.tick_envelope()
        self._triangle.tick_linear_counter()

    # ----- Half-frame -----
    if fc.half_frame:
        self._pulse1.tick_length_sweep()
        self._pulse2.tick_length_sweep()
        self._triangle.tick_length()
        self._noise.tick_length()

    # ----- Every-cycle timer -----
    self._pulse1.tick_timer()
    self._pulse2.tick_timer()
    self._triangle.tick_timer()
    self._noise.tick_timer()

    # ----- Frame IRQ -----
    if fc.irq:
        self.interrupts.irq_apu_frame = True

    # ----- Mix + downsample -----
    out = mix(self._pulse1.output, self._pulse2.output,
              self._triangle.output, self._noise.output)
    self._sample_sum += out
    self._sample_count += 1
    self._cycle_accum += 1.0
    if self._cycle_accum >= self.CYCLES_PER_SAMPLE:
        self._cycle_accum -= self.CYCLES_PER_SAMPLE
        if self._sample_count:
            self._sample_buffer.append(self._sample_sum / self._sample_count)
        self._sample_sum = 0.0
        self._sample_count = 0
```

### 2. FrameCounter (inhibit 内置)

```python
class FrameCounter:
    _STEPS_4 = (7457, 14913, 22371, 29829)
    _STEPS_5 = (7457, 14913, 22371, 37281)
    _WRAP_4 = 29830
    _WRAP_5 = 37282

    def __init__(self):
        self._cycle = 0
        self._mode_5step = False
        self._irq_inhibit = False
        self.quarter_frame = False
        self.half_frame = False
        self.irq = False

    def tick(self):
        self.quarter_frame = False
        self.half_frame = False
        self.irq = False
        self._cycle += 1

        steps = self._STEPS_5 if self._mode_5step else self._STEPS_4
        wrap  = self._WRAP_5 if self._mode_5step else self._WRAP_4

        if self._cycle in steps:
            self.quarter_frame = True
        if self._cycle in (steps[1], steps[3]):
            self.half_frame = True

        if not self._mode_5step and not self._irq_inhibit:
            if self._cycle == steps[3]:
                self.irq = True

        if self._cycle >= wrap:
            self._cycle = 0

    def write(self, value) -> tuple[bool, bool]:
        """Process $4017 write.  Returns (quarter, half) immediate clock flags."""
        self._mode_5step = bool(value & 0x80)
        self._irq_inhibit = bool(value & 0x40)
        if self._irq_inhibit:
            self.irq = False

        self._cycle = 0
        self.quarter_frame = False
        self.half_frame = False

        if self._mode_5step:
            return True, True        # immediate quarter + half
        return False, False
```

### 3. $4017 write in APU（消费 immediate flags）

```python
def write_register(self, address: int, value: int) -> None:
    ...
    elif reg == 0x17:
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
```

### 4. PulseChannel

每个 channel 暴露 `output: int` property（DAC 0–15）和 `length_active: bool` property（供 APU `read_status` 使用，不暴露内部 `LengthCounter` 对象）。

```
输出（DAC level 0-15）:
  0 如果 length==0 或 sweep 静音
  envelope.output() 如果 duty_table[duty] bit 为 1，否则 0

length_active property:
  True if length_counter.value > 0

寄存器:
  $4000/$4004: duty bit6-7, halt LC+env loop bit5, const bit4, volume/period bit0-3
  $4001/$4005: sweep enabled bit7, period bit4-6, negate bit3, shift bit0-2
  $4002/$4006: timer low
  $4003/$4007: length index bit3-7, timer high bit0-2
               设定时: restart sequencer, reload length, envelope.restart()
```

关键行为：
- 写 `$4003/$4007` → `envelope.restart()` + `seq = 0`
- Sweep **仅 half-frame** 触发（`tick_length_sweep`）
- Sweep period ≥ $800 或 negate overflow → 静音
- Length halt = `$4000 bit 5`
- `$4000/$4004 write` → `envelope.write_control()`（无 start）

### 5. TriangleChannel

```
输出（DAC level 0-15）:
  0 如果 length==0 或 linear_counter==0
  waveform_step（0-15, 32-step up/down）

length_active property:
  True if length_counter.value > 0

寄存器:
  $4008: control/halt bit7, linear reload value bit0-6
  $400A: timer low
  $400B: length index bit3-7, timer high bit0-2
         设定时: reload length, 置位 linear_reload_flag
```

Linear counter (quarter-frame):

```python
def tick_linear_counter(self):
    if self._linear_reload_flag:
        self._linear_counter = self._linear_reload_value
    elif self._linear_counter > 0:
        self._linear_counter -= 1
    if not self._control_flag:
        self._linear_reload_flag = False
```

- `_control_flag` = `$4008 bit 7`，同时 halt length counter
- `_linear_reload_flag`：`$400B` write 置位，quarter-frame + control_flag=0 清除

### 6. NoiseChannel

```
输出（DAC level 0-15）:
  0 如果 length==0
  envelope.output() 如果 (lfsr & 1) == 0，否则 0

length_active property:
  True if length_counter.value > 0

LFSR (15-bit, on timer expiry):
  mode 0: feedback = bit0 ^ bit1
  mode 1: feedback = bit0 ^ bit6
  lfsr = (lfsr >> 1) | (feedback << 14)

寄存器:
  $400C: halt LC+env_loop bit5, const bit4, volume/period bit0-3
  $400E: loop_noise bit7, noise period index bit0-3
  $400F: length index bit3-7
         设定时: reload length, envelope.restart()
```

噪声周期表 (NTSC):

```
[4, 8, 16, 32, 64, 96, 128, 160, 202, 254, 380, 508, 762, 1016, 2034, 4068]
```

### 7. Envelope

```python
class Envelope:
    def write_control(self, value):
        self._loop = bool(value & 0x20)
        self._constant = bool(value & 0x10)
        self._volume = value & 0x0F

    def restart(self):
        self._start = True

    def tick(self):
        # ... divider-based decay, reset 15 on start, loop back to 15

    def output(self) -> int:
        return self._volume if self._constant else self._decay
```

### 8. LengthCounter

```python
class LengthCounter:
    def __init__(self):
        self._counter = 0
        self._enabled = True
        self._halt = False

    def set_halt(self, halt: bool):
        self._halt = halt

    def tick(self):
        if self._counter > 0 and self._enabled and not self._halt:
            self._counter -= 1

    @property
    def value(self) -> int:
        return self._counter
```

### 9. NESMachine

```python
def reset(self) -> None:
    self._ppu.reset()           # order: PPU first
    self._apu.reset()
    self._oam_dma.reset()
    self._cpu.reset()           # CPU last (samples interrupts after)

@property
def audio_sample_rate(self) -> int:
    return 44_100

def read_audio_samples(self, max_count: int = 4096) -> list[float]:
    return self._apu.read_samples(max_count)
```

### 10. APU 顶层

```python
class APU:
    CPU_CLOCK_HZ = 1_789_773
    AUDIO_SAMPLE_RATE = 44_100
    CYCLES_PER_SAMPLE = CPU_CLOCK_HZ / AUDIO_SAMPLE_RATE

    __slots__ = (
        "interrupts",
        "_pulse1", "_pulse2", "_triangle", "_noise",
        "_frame_counter",
        "_sample_buffer",          # deque[float], maxlen=4096
        "_cycle_accum", "_sample_sum", "_sample_count",
    )

    def __init__(self, interrupts, *, region=None):
        ...

    def reset(self):
        self._pulse1.reset(); self._pulse2.reset()
        self._triangle.reset(); self._noise.reset()
        self._frame_counter = FrameCounter()
        self._sample_buffer.clear()
        self._cycle_accum = 0.0
        self._sample_sum = 0.0
        self._sample_count = 0
        self.interrupts.irq_apu_frame = False

    def read_status(self) -> int:
        """$4015 read — channel active bits + frame IRQ."""
        status = 0
        if self._pulse1.length_active:    status |= 0x01
        if self._pulse2.length_active:    status |= 0x02
        if self._triangle.length_active:  status |= 0x04
        if self._noise.length_active:     status |= 0x08
        # bit 4 = DMC active (0 in Phase 7)
        if self.interrupts.irq_apu_frame: status |= 0x40
        # bit 7 = DMC IRQ (0 in Phase 7)
        self.interrupts.irq_apu_frame = False   # reading $4015 clears frame IRQ
        return status

    def write_register(self, address: int, value: int) -> None:
        reg = address & 0x1F
        if reg <= 0x03:           self._pulse1.write(reg, value)
        elif reg <= 0x07:         self._pulse2.write(reg - 4, value)
        elif reg <= 0x0B:         self._triangle.write(reg - 8, value)
        elif reg <= 0x0F:         self._noise.write(reg - 0x0C, value)
        # $4010-$4013: DMC ignored
        elif reg == 0x15:
            self._pulse1.set_enabled(bool(value & 0x01))
            self._pulse2.set_enabled(bool(value & 0x02))
            self._triangle.set_enabled(bool(value & 0x04))
            self._noise.set_enabled(bool(value & 0x08))
        elif reg == 0x17:
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

    def read_samples(self, max_count: int) -> list[float]:
        count = min(max_count, len(self._sample_buffer))
        return [self._sample_buffer.popleft() for _ in range(count)]
```

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| 通道 muted | 输出 DAC 0 |
| $4015 写 0 | length counters 清零，enabled=False |
| $4015 读 | 清除 frame IRQ（bit 6） |
| $4017 写 bit7=1 | 切换到 5-step，immediate quarter+half clock via write return flags |
| $4017 写 bit6=1 | 清除 frame IRQ 并 inhibit 后续 |
| Sweep 溢出 | 通道静音（DAC 0） |
| Envelope restart | $4003/$4007/$400F → envelope.restart() |
| Noise output | `(lfsr & 1) == 0` 时输出 envelope，否则 0 |
| Triangle linear reload | $400B write → reload flag；quarter-frame 执行 reload |
| 降采样除零 guard | `if self._sample_count:` 防止 |
| Frame buffer overflow | deque(maxlen=4096) drops oldest |

---

## Non-Goals (Phase 7)

- DMC
- 非线性混音查找表
- 高通/低通滤波
- PAL APU
- 前端音频播放（Phase 7 仅暴露 core audio samples）

---

## Tests

新建 `tests/unit/test_apu.py`（共 23 个）：

### 寄存器读写 (5)

```python
test_pulse_duty_volume_write()        # $4000 duty/vol/envelope control
test_pulse_sweep_register_write()     # $4001 sweep params
test_triangle_linear_control_write()  # $4008 control + reload value
test_noise_period_register_write()    # $400E mode + period index
test_4015_write_enables_channels()    # $4015 enable/disable
```

### 通道 DAC 输出 (5)

```python
test_pulse_output_when_muted_is_zero()
test_pulse_output_follows_duty_and_envelope()
test_triangle_waveform_output_ramps()
test_triangle_linear_counter_halts_output_to_zero()
test_noise_outputs_zero_when_lfsr_bit0_set()
```

### 包络 + 长度计数器 (5)

```python
test_envelope_constant_volume()
test_envelope_decay_and_loop()
test_envelope_restart_only_on_length_timer_high_write()  # $4003/$4007/$400F
test_length_counter_load_and_decrement()
test_length_counter_halt_prevents_decrement()
```

### Frame Counter (5)

```python
test_frame_counter_4step_irq_fires()
test_frame_counter_4step_irq_inhibited()     # bit 6=1 → no IRQ
test_frame_counter_5step_no_irq()
test_frame_counter_5step_immediate_clocks()  # write $4017 bit7=1 → q+h
test_frame_counter_mode_switch_resets_cycle()
```

### 集成 / 混音器 / 降采样 (3)

```python
test_4015_read_returns_channel_status_and_clears_irq()
test_mixer_returns_zero_when_all_muted()
test_downsampler_outputs_about_44100hz()
```

---

## Implementation Plan

1. `envelope.py` + `length_counter.py`
2. `frame_counter.py`（返回 immediate flags）
3. `pulse.py`（DAC 0-15 + `length_active` property）
4. `triangle.py`（DAC 0-15 + `length_active` + linear counter）
5. `noise.py`（DAC 0-15 + `length_active` + LFSR）
6. `mixer.py`（归一化 [0,1]）
7. 重写 `apu.py`（组合 + 降采样 + $4017 immediate）
8. `machine.py`（reset 顺序 + 音频 API）
9. `tests/unit/test_apu.py`（23 tests）
10. `ruff + pytest` 回归

---

## Risks

- R-7.1: 44.1kHz 降采样精度可接受，fractional accumulator 无 drift
- R-7.2: DMC 缺失不影响多数 NROM BGM
- R-7.3: 线性混音器满足 Phase 7 目标
- R-7.4: Sweep 覆盖基本静音规则

## Verification Criteria

1. `ruff` clean, 现有 204 tests pass
2. 新增 `test_apu.py` 23 tests pass
3. 60 帧 NROM → `read_audio_samples()` ≈ 735 samples
4. 寄存器写 → DAC 输出可观测
5. FrameCounter 4-step IRQ, 5-step no IRQ, inhibit 阻止 IRQ
6. $4017 immediate clock 在写入后立即生效
7. APU reset 正确清除状态
