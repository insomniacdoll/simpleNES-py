# Phase 9：性能优化 实现设计

## Summary

Phase 0–8 已完成 CPU、PPU、APU、5 个 Mapper、Pygame 前端。当前正确性优先的纯 Python 参考实现需要通过 profiling 引导的性能优化达到实时运行目标。

Phase 9 的核心范围：
- **Benchmark 基础设施**：可重复的性能度量与回归检测
- **Profiling 驱动优化**：识别热路径，逐项优化并验证
- **Python 层优化**：减少对象分配、热路径扁平化、缓存中间结果
- **不引入 Cython/Rust**：保持纯 Python 参考实现的正确性 oracle 身份

验收：NROM 游戏达到 60 FPS 稳定实时，复杂 Mapper（MMC1/MMC3）游戏接近或达到实时。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/ppu.py` | **修改** — 缓存 palette 索引（无 bus 调用的 fast path） |
| `src/simplenes/bus/ppu_bus.py` | **修改** — 新增 `peek_palette()` 和 `palette_cache` 同步；轻量级 `observe_ppu_address()` 跳过 |
| `src/simplenes/apu/apu.py` | **修改** — deque → 预分配 ring buffer |
| `src/simplenes/scheduler.py` | **修改** — 内联 PPU clock 循环 |
| `src/simplenes/cartridge/mapper.py` | 不变（不需要修改；PPUBus 使用 `getattr`，无需 Protocol 声明）|
| `src/simplenes/cartridge/mappers/mapper004_mmc3.py` | **修改** — `has_ppu_observer = True` |
| `benchmarks/` | **新建** — benchmark suite（独立于 tests/） |
| `pyproject.toml` | **修改** — 增加 `pytest-benchmark` dev 依赖 |

### 不变模块

`cpu.py`、`opcodes.py`、`machine.py`、`mapper000_nrom.py`、`mapper001_mmc1.py`、`mapper002_uxrom.py`、`mapper003_cnrom.py`、`cartridge/image.py`、`cartridge/ines.py`、`input/*`、`dma/*`、`frontend/*`、`timing.py`、`interrupts.py`、`errors.py` 逻辑不变（`mapper004_mmc3.py` 仅加 1 行 class attribute）。

---

## Architecture Decisions

### AD-9.1：优化准入原则

```
正确性基线 → Profiling → 识别热点 → 优化 → 回归测试 → 再次 Profiling
```

- 每次优化前记录 benchmark 基线。
- 每次优化后运行完整测试套件（`pytest tests/ -q`）确认无回归。
- 被优化的函数不得改变对外行为，仅改变内部实现。
- `NESMachine` 对外 API 保持不变。
- **PPU.clock() 的内部时序顺序不得改变**。所有优化只替换等价实现。

### AD-9.2：Benchmark 基础设施

将 benchmark 放在独立目录 `benchmarks/`，与 `tests/` 分离，避免默认测试运行被污染。

```text
benchmarks/
├── __init__.py
├── conftest.py             # 共享 fixture: CartridgeImage, NESMachine
├── test_bench_ppu.py       # PPU 渲染性能
├── test_bench_cpu.py       # CPU 指令执行性能
├── test_bench_apu.py       # APU 时钟性能
├── test_bench_bus.py       # Bus 读写性能 (PPU 模式表 + nametable)
├── test_bench_scheduler.py # Scheduler.run_frame() 端到端
├── test_bench_mappers.py   # 各 Mapper ppu_read/write 性能
└── test_bench_pixel.py     # 单像素 compositing 微 benchmark
```

每个 benchmark 函数使用 `pytest-benchmark` 的 `benchmark` fixture：

```python
def test_bench_ppu_clock_visible_scanline(benchmark, ppu):
    """Measure PPU.clock() throughput on visible scanlines."""
    ppu.scanline = 100
    ppu.dot = 1
    ppu.write_register(0x2001, 0x0E)  # enable background
    benchmark(ppu.clock)
```

benchmark 目录 `benchmarks/` 已在 `testpaths = ["tests"]` 之外，默认不会被 pytest 收集，因此无需额外配置。运行 benchmark 使用专用命令：

```bash
uv run pytest benchmarks/ --benchmark-only     # 运行 benchmark
```

对比优化前后：

```bash
uv run pytest benchmarks/ --benchmark-only --benchmark-autosave
# ... 优化实现 ...
uv run pytest benchmarks/ --benchmark-only --benchmark-compare=0001
```

### AD-9.3：PPU 热路径优化

PPU.clock() 是最高频调用（约 89K 次/frame），每一 dot 都执行。以下按影响排序。

**PPU.clock() 内部时序不得重排。** 原有的 dot 推进、scanline 推进、VBlank 检测、NMI 触发顺序保持完全不变。

#### 9.3.1 快速 palette 读取（高影响）

当前每个像素都通过 `self.bus.read(0x3F00 | ...)` 读 palette，经过完整的 `PPUBus.read()` 路径（含 `observe_ppu_address()`、nametable mirroring 在 `$2000-$3EFF` 分支、palette mirror 字典查找）。palette 在 pixel output 期间仅读取，且写入只能来自 CPU 侧 `$2007`。

**方案：PPUBus 维护 palette cache 并提供 fast API**

PPUBus 新增方法直接返回 palette 值，不经过 `read()` 的完整路径：

```python
class PPUBus:
    __slots__ = (
        "_mapper", "_nametables", "_palette_ram",
        "_palette_cache",     # bytearray(32), 同步于 palette 写入
        "_observe_ppu",
    )

    def __init__(self, mapper):
        ...
        self._palette_cache = bytearray(32)
        self._sync_palette_cache()

    def peek_palette(self, index: int) -> int:
        """返回 palette RAM 中 index 位置的值（0-31），应用 mirror 规则。

        供 PPU pixel output 使用，不触发 observe_ppu_address()。
        """
        idx = index & 0x1F
        if idx >= 0x10 and (idx & 3) == 0:
            idx &= 0x0F
        return self._palette_cache[idx] & 0x3F

    def _write_palette(self, address: int, value: int) -> None:
        idx = self._palette_index(address)
        self._palette_ram[idx] = value & 0xFF
        self._palette_cache[idx] = value & 0x3F   # 同步更新 cache

    def _sync_palette_cache(self) -> None:
        """整体刷新 cache（仅 construction/reset 时调用）。"""
        for i in range(32):
            idx = i
            if idx >= 0x10 and (idx & 3) == 0:
                idx &= 0x0F
            self._palette_cache[i] = self._palette_ram[idx] & 0x3F
```

PPU pixel output 改为：

```python
# _output_background_pixel():
palette_idx = self.bus.peek_palette(0)                       # backdrop
palette_idx = self.bus.peek_palette((attr << 2) | pixel)     # colored

# _composite_sprite_pixel():
palette_idx = self.bus.peek_palette(0x10 | ((attr & 3) << 2) | pixel)

# _output_backdrop_pixel():
palette_idx = self.bus.peek_palette(0)
```

**架构合规性**：palette state 属于 PPUBus；PPU 通过 `bus.peek_palette()` 访问，不绕过 bus 层直接读 `_palette_ram`。`peek_palette()` 不触发 `observe_ppu_address()`，无 IRQ 副作用。

**Mirror 同步语义**：`_write_palette()` 只同步被写入的 canonical index。`peek_palette()` 内部会先 mirror 输入 index（`$3F10→$3F00` 等），因此读取语义始终正确。`_palette_cache` 不保证 mirror slot 本身被即时同步——即 `_palette_cache[0x10]` 可能不是最新值，但 `peek_palette(0x10)` 返回值一定正确。

**`_palette_index()` 同时改为直接计算**（消除字典查找）：

```python
def _palette_index(self, address: int) -> int:
    idx = address & 0x1F
    if idx >= 0x10 and (idx & 3) == 0:
        idx &= 0x0F
    return idx
```

移除 `PALETTE_MIRRORS` 类变量。

#### 9.3.2 `observe_ppu_address()` 轻量跳过（中影响）

PPUBus.read/write 无条件调用 `self._mapper.observe_ppu_address(address)`，即使 mapper 无 IRQ（NROM/UxROM/CNROM/MMC1）。

方案：使用 class attribute 标记（非 property，避免每次访问都执行 Python 属性查找），PPUBus 在 `__init__` 中缓存：

```python
# mapper004_mmc3.py — 仅 MMC3 显式声明：
class MMC3Mapper:
    has_ppu_observer = True

# ppu_bus.py __init__ — 用 getattr 安全回退，不依赖继承：
self._observe_ppu = (
    self._mapper.observe_ppu_address
    if getattr(self._mapper, "has_ppu_observer", False)
    else None
)

# ppu_bus.py read/write：
if self._observe_ppu is not None:
    self._observe_ppu(address)
```

#### 9.3.3 `_update_rendering_flags()` 延迟计算（低影响）

当前每 dot 调用 `_update_rendering_flags()`。`_rendering` / `_bg_enabled` 仅随 `mask` 写入变化。改为仅 `write_register(1)` 时调用，`clock()` 中删除该调用。

**测试/benchmark 更新**：当前 tests 和 benchmarks 中存在直接赋值 `ppu.mask = 0x1E` 的模式，这绕过 `write_register()`，不会触发 `_update_rendering_flags()`。实现时需将所有 `ppu.mask = ...` 替换为 `ppu.write_register(0x2001, value)`。

---

### AD-9.4：APU 热路径优化

#### 9.4.1 FIFO ring buffer 替代 deque（中影响）

当前 `deque(maxlen=4096)` 在满时涉及 `popleft` + `append` 两个操作，有对象分配开销。

**设计：FIFO 语义的预分配 ring buffer**

```python
class APU:
    _SAMPLE_BUFFER_SIZE = 4096

    __slots__ = (
        ...,
        "_sample_buffer",       # list[float]，预分配 4096
        "_sample_write",        # int，下一个写入位置
        "_sample_read",         # int，下一个读取位置
        "_sample_available",    # int，当前可读样本数
    )

    def __init__(self, ...):
        self._sample_buffer = [0.0] * self._SAMPLE_BUFFER_SIZE
        self._sample_write = 0
        self._sample_read = 0
        self._sample_available = 0

    def _push_sample(self, val: float) -> None:
        """写入一个样本。若满则覆盖最旧的。"""
        self._sample_buffer[self._sample_write] = val
        self._sample_write = (self._sample_write + 1) % self._SAMPLE_BUFFER_SIZE
        if self._sample_available < self._SAMPLE_BUFFER_SIZE:
            self._sample_available += 1
        else:
            self._sample_read = (self._sample_read + 1) % self._SAMPLE_BUFFER_SIZE

    def read_samples(self, max_count: int) -> list[float]:
        """按 FIFO 顺序消费最多 max_count 个样本。"""
        count = min(max_count, self._sample_available)
        result = []
        r = self._sample_read
        for _ in range(count):
            result.append(self._sample_buffer[r])
            r = (r + 1) % self._SAMPLE_BUFFER_SIZE
        self._sample_read = r
        self._sample_available -= count
        return result
```

**语义保持**：与现有 `deque.popleft()` FIFO 消费完全一致。

**测试更新**：现有白盒测试 `assert len(apu._sample_buffer) == 0` 需改为 `assert apu._sample_available == 0`（预分配 list 的 `len` 恒为 4096），或通过 `read_samples()` 返回值验证。

---

### AD-9.5：Scheduler 循环优化

PPU dots per CPU cycle 对 NTSC 恒为 3。内层循环展开消除 Python range iterator 开销：

```python
# 改为：
for _ in range(cycles):
    ppu.clock(); ppu.clock(); ppu.clock()
    apu.clock_cpu_cycle()
```

---

### AD-9.6：性能回归检测

使用 `pytest-benchmark` 的 `--benchmark-compare` 机制，不在测试代码中硬编码 `stats.ops` 阈值（因为不同硬件性能不同）。CI 流程：

1. 记录 baseline：`pytest benchmarks/ --benchmark-only --benchmark-autosave`
2. 每次 PR 运行：`pytest benchmarks/ --benchmark-only --benchmark-compare=baseline`
3. 人工审查 `pytest-benchmark compare` 输出的 regression 报告。

---

## Data Model Changes

### 1. PPUBus 新增 slot

```python
__slots__ = (
    "_mapper", "_nametables", "_palette_ram",
    "_palette_cache",      # bytearray(32), 与 _palette_ram 同步
    "_observe_ppu",        # Optional[Callable] — None if mapper has no observer
)
```

### 2. PPUBus 新增方法

```python
def peek_palette(self, index: int) -> int:
    """Fast palette read for PPU pixel output. No mapper observer, no mirroring eval."""
    ...

def _sync_palette_cache(self) -> None:
    """Full sync from _palette_ram to _palette_cache. Called once during construction."""
    ...
```

### 3. PPUBus 移除

```python
PALETTE_MIRRORS = {...}   # 移除，_palette_index 改为直接计算
```

### 4. Mapper `has_ppu_observer` 声明

仅 MMC3Mapper 显式声明：

```python
# mapper004_mmc3.py：
has_ppu_observer = True
```

其他 mapper 无需任何改动。PPUBus 使用 `getattr(self._mapper, "has_ppu_observer", False)` 安全回退。

`mapper.py`（Protocol）**不修改**——`getattr` 回退机制使得 Protocol 不需要声明该字段。

### 5. APU 替换 deque

```python
__slots__ = (
    ...existing...,
    "_sample_buffer",       # list[float]，预分配 4096
    "_sample_write",        # int
    "_sample_read",         # int
    "_sample_available",    # int
)
# 移除：
#   _sample_buffer (deque)
#   _sample_sum, _sample_count, _cycle_accum 保留不变
```

### 6. PPU slot（无变化）

PPU 本次不新增 `_palette_cache`（该 cache 归 PPUBus 所有）。

---

## Control Flow

### PPU.clock()（时序不变）

PPU.clock() 的原有控制流顺序**完全不变**。唯一的内部替换：

- `self.bus.read(0x3F00 | ...)` → `self.bus.peek_palette(...)`
- `self._update_rendering_flags()` 调用从 `clock()` 中移除（移至 `write_register(1)`）

其他所有步骤（dot/scanline advance、VBlank 检测、background pipeline、sprite compositing、counter 推进）顺序不变。

### PPUBus.read()

```
read(address):
  addr &= 0x3FFF
  if _observe_ppu: _observe_ppu(addr)     # 仅 MMC3 时非 None
  if addr < 0x2000: return mapper.ppu_read(addr)
  if addr < 0x3F00: return _read_nametable(addr)
  return _read_palette(addr)
```

`_read_palette()` 内部使用计算后的 `_palette_index()`（无字典查找）。

### PPUBus.write()

`_write_palette()` 内部增加同步 `_palette_cache[idx] = value & 0x3F`。

### APU.clock_cpu_cycle() → _push_sample()

```
... mix + accumulate ...
if self._cycle_accum >= self.CYCLES_PER_SAMPLE:
    self._cycle_accum -= self.CYCLES_PER_SAMPLE
    if self._sample_count:
        self._push_sample(self._sample_sum / self._sample_count)
    self._sample_sum = 0.0
    self._sample_count = 0
```

### APU.read_samples()

```python
def read_samples(self, max_count: int) -> list[float]:
    """Consume up to max_count floats in FIFO order."""
    count = min(max_count, self._sample_available)
    result = [0.0] * count
    r = self._sample_read
    for i in range(count):
        result[i] = self._sample_buffer[r]
        r = (r + 1) % self._SAMPLE_BUFFER_SIZE
    self._sample_read = r
    self._sample_available -= count
    return result
```

---

## Edge Cases

| 场景 | 处理 |
|------|------|
| Mid-frame palette 写入（通过 $2007） | PPUBus `_write_palette()` 实时同步 `_palette_cache[idx]`，无延迟 |
| CPU 侧写 `$2006/$2007` 到 `$3F00-$3F1F` 在渲染前 | 同上一行——每次 palette 写入立刻更新 cache |
| MMC3 IRQ observer 必须在 nametable 访问时工作 | `has_ppu_observer=True` 时 PPUBus 仍对所有地址调用 `observe_ppu` |
| `peek_palette()` 被非 pixel 路径误用 | 该方法仅被 PPU pixel output 调用，CPUBus 和 `_read_ppudata()` 仍走完整 `PPUBus.read()` |
| APU ring buffer 满时覆盖旧样本 | `_push_sample()` 推进 `_sample_read`，FIFO 语义保持连续 |
| APU `read_samples()` 返回后样本消费完毕 | `_sample_available` 递减，下次调用返回空列表 |
| `reset()` 后 ring buffer 状态 | `_sample_read=0, _sample_write=0, _sample_available=0`；同步 palette cache 通过 `_sync_palette_cache()` |

---

## Implementation Plan

### Step 1: Benchmark 基础设施

1. 添加 `pytest-benchmark` 到 dev 依赖。
2. `benchmarks/` 不在 `testpaths = ["tests"]` 中，默认 `uv run pytest tests/ -q` 不会收集 benchmark，无需额外配置。
3. 创建 `benchmarks/` 目录和 `conftest.py`。
4. 实现共享 fixture：`nrom_image()`、`ppu(nrom_image)`、`nes_machine(nrom_image)`。
5. 实现首批 benchmark：`test_bench_ppu.py`、`test_bench_scheduler.py`。
6. 记录 baseline：`uv run pytest benchmarks/ --benchmark-only --benchmark-autosave`

### Step 2: PPUBus palette fast path

1. `ppu_bus.py`：
   - 计算式 `_palette_index()` 替换字典查找，移除 `PALETTE_MIRRORS`。
   - 新增 `_palette_cache = bytearray(32)`，在 `__init__` 调用 `_sync_palette_cache()`。
   - 新增 `peek_palette(index)`。
   - `_write_palette()` 内部增加 `_palette_cache[idx] = value & 0x3F`。
2. `ppu.py`：
   - `_output_background_pixel()`、`_output_backdrop_pixel()`、`_composite_sprite_pixel()` 中 `self.bus.read(0x3F00|...)` 改为 `self.bus.peek_palette(...)`。
3. 运行 `uv run pytest tests/ -q` 确认无回归。
4. 运行 `uv run pytest benchmarks/ --benchmark-only --benchmark-compare=0001` 对比收益。

### Step 3: observe_ppu_address 轻量跳过

1. `mapper004_mmc3.py` 加 `has_ppu_observer = True`。
2. `ppu_bus.py __init__` 使用 `getattr(self._mapper, "has_ppu_observer", False)` 设置 `_observe_ppu`。
3. NROM/UxROM/CNROM/MMC1 无需任何改动。
4. `read()` / `write()` 改为条件调用。
5. 运行 `uv run pytest tests/unit/test_mmc3.py -q` 确认 IRQ 行为不变。

### Step 4: APU ring buffer

1. `apu.py` 实现上述 `_push_sample()` 和 `read_samples()` 的 FIFO ring buffer。
2. `reset()` 重置 `_sample_read/_sample_write/_sample_available`。
3. 更新 APU 测试：`assert len(apu._sample_buffer) == 0` → `assert apu._sample_available == 0`。
4. 运行 `uv run pytest tests/unit/test_apu.py -q` 确认无回归。
5. Benchmark 对比。

### Step 5: Scheduler 内联 + 渲染标志延迟

1. `scheduler.py` 内联 3 次 `ppu.clock()`。
2. `ppu.py` 的 `write_register(reg==1)` 中调用 `_update_rendering_flags()`，`clock()` 中移除该调用。
3. 将 tests 中所有直接 `ppu.mask = ...` 替换为 `ppu.write_register(0x2001, value)`。
4. 将 benchmarks 中对应的 `ppu.mask = 0x0E` 也改为 `ppu.write_register(0x2001, 0x0E)`。
5. 运行 `uv run pytest tests/ -q` 确认无回归。

### Step 6: 验收

1. 运行全部测试：
   ```bash
   uv run ruff check src/ tests/
   uv run pytest tests/ -q
   ```
2. 运行 benchmark 并对比。

---

## Verification Commands

```bash
# 完整回归测试（不运行 benchmark）
uv run ruff check src/ tests/
uv run pytest tests/ -q

# Benchmark 基线记录
uv run pytest benchmarks/ --benchmark-only --benchmark-autosave

# Benchmark 对比（优化后）
uv run pytest benchmarks/ --benchmark-only --benchmark-compare=0001

# APU 专项测试
uv run pytest tests/unit/test_apu.py -q

# MMC3 IRQ 专项测试
uv run pytest tests/unit/test_mmc3.py -q
```

---

## Risks / Open Questions

| 风险 | 缓解 |
|------|------|
| `peek_palette()` 绕过 `observe_ppu_address()` 导致 MMC3 IRQ 偏差 | `peek_palette()` 仅用于 pixel output，与 PPU 地址线无关；MMC3 A12 观察仍通过正常的 pattern table / nametable 访问完成 |
| `has_ppu_observer` 用 class attribute 而非 property 在继承链中不够灵活 | 所有 Mapper 独立 class，不共享父类；`getattr` 回退免去每个 class 显式声明的需要 |
| Benchmark 数值在不同硬件上差异大 | 使用 `--benchmark-compare` 做相对变化检测，不设绝对阈值 |
| APU ring buffer 并发访问 | 单线程模型，无并发问题 |
| `pytest-benchmark` 版本兼容性 | 固定 `pytest-benchmark>=4` |
| palette cache 在 `reset()` 后需重新同步 | PPUBus 的 `__init__` 已调用 `_sync_palette_cache()`；若以后 PPUBus 支持 reset，需同步清空 nametables 和 palette cache |

### Open Questions

1. **是否需要 `__slots__` 统一审查？** 当前 PPU/APU/Bus/Mapper 已使用 `__slots__`。本次新增 `_palette_cache` (33 bytes) 和 APU ring buffer 字段，内存影响可忽略。

2. **APU ring buffer 大小是否可调？** 4096 样本 ≈ 93ms @44100Hz，足够前端消费。如需更大，改为可配置常量即可。

3. **是否需要 PEP 578 audit hooks 或 memory profiling？** 本次不涉及；若后续引入 Cython 才需要内存布局分析。

4. **`_update_rendering_flags()` 延迟计算是否影响任何测试？** 需在 Step 5 验证；若有测试依赖每 dot 更新 `_rendering` 标志，相应调整测试（不应有，因为标志仅在 mask 变化或 clock 采样时对外可见）。

---

## Non-Goals

- 不引入 Cython / Rust / C 扩展
- 不改变 `NESMachine` 对外 API
- 不改变 PPU.clock() 内部时序顺序
- 不改变 sprite 0 hit、scroll、NMI 逻辑
- 不引入 JIT
- 不移除或重写任何现有功能
