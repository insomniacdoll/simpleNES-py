# Phase 9c: 60 FPS 性能优化设计（rev3）

## Summary

Phase 9b 后 `run_frame()` 平均 **35.3ms**（~28.3 ops/sec），距离 60 FPS 目标（16.67ms/frame）差距约 **2.1×**。

瓶颈：**PPU per-dot visible rendering**。6.14 万个可见像素 dot，每个 442ns，Python 解释器 + 函数调用开销占主导。

本阶段采用架构文档推荐的 Cython 路线：编译 PPU 热点为 C 扩展，Scheduler 批量推进 dot，在当前 `hatchling` 构建体系下以 `build_ext --inplace` 脚本方式实现，不替换 build backend。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/ppu.py` | **修改** — 新增 `advance_dots()`；继续作为纯 Python oracle |
| `src/simplenes/ppu/__init__.py` | **修改** — 完整 `SIMPLENES_BACKEND` fallback 逻辑 |
| `src/simplenes/ppu/_ppu_cy.pyx` | **新增** — Cython PPU 实现（含 bus callable 缓存） |
| `src/simplenes/bus/ppu_bus.py` | **不变** — 纯 Python oracle（Phase 9c 不编译 bus） |
| `src/simplenes/scheduler.py` | **修改** — 使用 `advance_dots(3)` 批量推进 |
| `src/simplenes/machine.py` | **修改** — import 路径改为 `from simplenes.ppu import PPU` |
| `benchmarks/conftest.py` | **修改** — import 路径改为 `from simplenes.ppu import PPU` |
| `tests/unit/test_ppu_cy_smoke.py` | **新增** — Cython backend 冒烟测试 |
| `tests/**/ existing` | **不变** — 继续显式导入 `simplenes.ppu.ppu.PPU` 作为 oracle |
| `pyproject.toml` | **修改** — dev optional 增加 `cython>=3.0`, `setuptools>=64` |
| `scripts/build_cython.py` | **新增** — in-place Cython 编译脚本（`build_ext --inplace`） |

---

## Architecture Decisions

### AD-9c.1：Cython 编译 PPU 热路径，保留 hatchling

遵循架构文档原生加速路线：
> 纯 Python 参考实现 → profiling → Cython/Rust 加速 PPU

策略：
- `ppu.py` 保持为纯 Python 参考实现（已有测试覆盖的 oracle）。
- 新增 `src/simplenes/ppu/_ppu_cy.pyx`，使用 Cython 编译热点方法为 C。
- **不替换 `hatchling` build backend**。Cython 编译通过独立脚本 `scripts/build_cython.py` 调用 `setuptools.setup(package_dir={"": "src"}, script_args=["build_ext", "--inplace"])` 生成 in-place `.so` 文件。
- `simplenes/ppu/__init__.py` 中根据 `SIMPLENES_BACKEND` 环境变量 + try/except 选择后端。
- 所有生产 import 改为 `from simplenes.ppu import PPU`。
- 测试显式 `from simplenes.ppu.ppu import PPU` 保证 oracle 可达。

### AD-9c.2：Import 路径层级说明

| 导入路径 | 用途 | 解析为 |
|----------|------|--------|
| `from simplenes.ppu import PPU` | 生产代码（`machine.py`, `scheduler.py`, `benchmarks/`） | Cython `PPUCy`（如已编译且未强制 python） 或 纯 Python `PPU` |
| `from simplenes.ppu.ppu import PPU` | 测试代码（保持不变） | 始终为纯 Python oracle |

PPUBus 本阶段不 Cython 编译，import 路径不变：`from simplenes.bus.ppu_bus import PPUBus`。

### AD-9c.3：Scheduler 批量推进 PPU dot

当前 scheduler 对每个 CPU cycle 调用 3 次 `ppu.clock()`。改为 `ppu.advance_dots(3)` 一次性推进：

```python
# step_instruction():
for _ in range(cycles):
    ppu.advance_dots(3)
    apu.clock_cpu_cycle()

# _execute_dma():
for _ in range(_DMA_CYCLES):
    ppu.advance_dots(self._timing.ppu_dots_per_cpu_cycle)
    apu.clock_cpu_cycle()
```

`advance_dots(n)` 内部逐个 dot 推进（保持寄存器与中断语义一致）。纯 Python 版本调用 `self.clock()` 循环，Cython 版本在紧凑 C 循环中调用 `cdef _clock_one_dot()`。

### AD-9c.4：Cython PPU 缓存 hot bus callable（关键）

仅编译 PPU 外层控制流不足以消除 `self.bus.peek_palette(...)` 的 Python object method call 开销。在 `PPUCy.__init__` 中缓存 bus 热路径 callable：

```cython
cdef object _bus_read
cdef object _bus_write
cdef object _peek_palette
```

构造时：

```python
self._bus_read = bus.read
self._bus_write = bus.write
self._peek_palette = bus.peek_palette
```

`_clock_one_dot()` 内部全程使用 `self._peek_palette(idx)` 等缓存 callable，消除每 dot 的 `self.bus.` 属性查找。

PPUBus 类完全不修改，不引入 mirroring 缓存风险。若性能仍不足，Phase 9d 单独设计 PPUBus Cython。

### AD-9c.5：不做 pattern table cache

`sprite pattern row cache` 的跨 mapper bank 正确性风险在前序 review 中已明确。Phase 9c 不引入任何 pattern cache。

---

## Cython 实现要点

### ppu.pyx 结构

```cython
# cython: language_level=3, boundscheck=False, wraparound=False, cdivision=True

cdef class PPUCy:
    # Public attributes — cdef public for Python access (tests, bus, machine)
    cdef public int control, mask, status, oam_address
    cdef public int v, t, fine_x
    cdef public bint write_toggle, odd_frame
    cdef public int read_buffer, scanline, dot, frame
    cdef public bytearray framebuffer, oam

    # Internal attributes
    cdef:
        object bus, interrupts           # kept as object; not accessed in hot path
        object _bus_read, _bus_write, _peek_palette  # cached callable refs
        bint _rendering, _bg_enabled, _nmi_prev, _sprite_zero_possible
        int _bg_shift_lo, _bg_shift_hi, _bg_attr_lo, _bg_attr_hi
        int _nt_latch, _at_latch, _pt_lo_latch, _pt_hi_latch
        int _sprite_count, _last_bg_pixel
        bytearray _secondary_oam

    def __init__(self, bus, interrupts, *, region=None):
        self.bus = bus
        self.interrupts = interrupts
        # Cache hot-path bus callable references
        self._bus_read = bus.read
        self._bus_write = bus.write
        self._peek_palette = bus.peek_palette
        # ... (rest of __init__ identical to pure Python PPU)

    cpdef void clock(self):
        self._clock_one_dot()

    cpdef void advance_dots(self, int n):
        cdef int i
        for i in range(n):
            self._clock_one_dot()

    cdef void _clock_one_dot(self):
        """Inlined dot logic — uses _peek_palette / _bus_read / _bus_write."""
        # Replicates clock() body, replacing:
        #   self.bus.peek_palette(x) → self._peek_palette(x)
        #   self.bus.read(x)         → self._bus_read(x)
        #   self.bus.write(x, v)     → self._bus_write(x, v)
        ...

    cpdef int read_register(self, int address):
        ...

    cpdef void write_register(self, int address, int value):
        ...

    cpdef void reset(self):
        ...
```

### Public API 兼容性清单

| 属性 | 类型 | 访问原因 |
|------|------|----------|
| `framebuffer` | `bytearray` | `NESMachine.framebuffer` property |
| `oam` | `bytearray` | OAM DMA 写入，测试断言 |
| `status`, `control`, `mask` | `int` | 测试断言、bus 转发 |
| `v`, `t`, `fine_x` | `int` | 测试断言（scroll/addr） |
| `scanline`, `dot`, `frame` | `int` | Scheduler `run_frame()` 判断 |
| `write_toggle` | `bool` | 测试断言 |
| `read_buffer` | `int` | PPUDATA 测试 |

| 方法 | 原因 |
|------|------|
| `clock()` | Scheduler / 测试 per-dot 推进 |
| `advance_dots(n)` | Scheduler 批量推进 |
| `read_register(addr)` | Bus 转发 |
| `write_register(addr, value)` | Bus 转发、OAM DMA |
| `reset()` | `NESMachine.reset()` |

`bus` / `interrupts` 作为构造注入依赖，不推荐外部在运行中重新赋值，不在 public 清单中。

---

## Build Infrastructure

### pyproject.toml 修改

保留 `hatchling`，仅在 optional dependencies 增加 Cython + setuptools（build 脚本依赖）：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "ruff>=0.6",
    "pytest-benchmark>=5.2.3",
    "cython>=3.0",
    "setuptools>=64",
]
cython = ["cython>=3.0", "setuptools>=64"]
```

### 编译脚本：scripts/build_cython.py

`build_ext --inplace` 生成可 import 的 `_ppu_cy.so`：

```python
"""In-place build of Cython PPU extension for benchmarking.

Usage:
    uv run python scripts/build_cython.py
"""
from Cython.Build import cythonize
from setuptools import Extension, setup

extensions = [
    Extension(
        "simplenes.ppu._ppu_cy",
        ["src/simplenes/ppu/_ppu_cy.pyx"],
    ),
]

setup(
    package_dir={"": "src"},
    name="simplenes-cython-extensions",
    ext_modules=cythonize(
        extensions,
        language_level="3",
        annotate=True,
        build_dir="build/cython",
        compiler_directives={
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
    ),
    script_args=["build_ext", "--inplace"],
)
```

运行：

```bash
uv run python scripts/build_cython.py
```

---

## Fallback 与验证策略

### 完整 `src/simplenes/ppu/__init__.py`

```python
"""PPU module — auto-selects Cython or pure Python backend."""
import os

_backend = os.environ.get("SIMPLENES_BACKEND", "")

if _backend == "python":
    # Force pure Python (CI / oracle validation)
    from simplenes.ppu.ppu import PPU
elif _backend == "cython":
    # Force Cython; must fail loudly if not compiled
    from simplenes.ppu._ppu_cy import PPUCy as PPU
else:
    try:
        from simplenes.ppu._ppu_cy import PPUCy as PPU
    except ImportError:
        from simplenes.ppu.ppu import PPU

__all__ = ["PPU"]
```

行为矩阵：

| `SIMPLENES_BACKEND` | `_ppu_cy` 已编译 | 结果 |
|---------------------|-------------------|------|
| (unset) | 是 | `PPUCy` |
| (unset) | 否 | 纯 Python `PPU` (silent fallback) |
| `python` | any | 纯 Python `PPU` |
| `cython` | 是 | `PPUCy` |
| `cython` | 否 | `ImportError` (loud failure) |

### 测试覆盖

| 测试集 | 后端 | 命令 |
|--------|------|------|
| 纯 Python oracle | `simplenes.ppu.ppu.PPU` | `uv run pytest tests/ -q` |
| Cython 集成 | `NESMachine`（auto-select） | `SIMPLENES_BACKEND=cython uv run pytest tests/ -q` |
| 冒烟对比 | 两个后端的 register state | 新增 `tests/unit/test_ppu_cy_smoke.py` |
| CI（无 Cython） | 纯 Python fallback | `SIMPLENES_BACKEND=python uv run pytest tests/ -q` |

---

## Data Model Changes

| 字段/方法 | 位置 | 变更 |
|-----------|------|------|
| `advance_dots(n: int)` | `ppu.py` | **新增** — 批量推进 n dots；纯 Python 内部调用 `self.clock()` 循环 |
| `advance_dots(n: int)` | `_ppu_cy.pyx` | **新增** — Cython 紧凑循环，调用 `_clock_one_dot()` |
| `_clock_one_dot()` | `_ppu_cy.pyx` | **新增** — `cdef` 方法，内联全部 dot 逻辑 |
| `_bus_read`, `_bus_write`, `_peek_palette` | `_ppu_cy.pyx` | **新增** — 缓存 hot bus callable，消除每 dot 属性查找 |

无新增 `__slots__` 字段。`_ppu_cy.pyx` 使用 `cdef` / `cdef public` 声明类成员。

---

## Implementation Plan

### Step 1: `__init__.py` fallback + import 路径修正

1. 编辑 `src/simplenes/ppu/__init__.py`，写入完整 `SIMPLENES_BACKEND` 逻辑（见上文）。
2. 将 `src/simplenes/machine.py` 中：
   ```python
   from simplenes.ppu.ppu import PPU
   ```
   改为：
   ```python
   from simplenes.ppu import PPU
   ```
3. 将 `benchmarks/conftest.py` 中：
   ```python
   from simplenes.ppu.ppu import PPU
   ```
   改为：
   ```python
   from simplenes.ppu import PPU
   ```
4. 运行 `uv run pytest tests/ -q`（此时无 Cython，验证 fallback 生效）。

### Step 2: `advance_dots()` 首次实现（纯 Python）

1. `ppu.py` 添加：
   ```python
   def advance_dots(self, n: int) -> None:
       for _ in range(n):
           self.clock()
   ```
2. `scheduler.py` 中：
   - `step_instruction()` 循环内 `ppu.clock(); ppu.clock(); ppu.clock()` → `ppu.advance_dots(3)`
   - `_execute_dma()` 循环内同样改为 `ppu.advance_dots(self._timing.ppu_dots_per_cpu_cycle)`
3. 运行 `uv run pytest tests/ -q`

### Step 3: pyproject.toml + 构建环境

1. `pyproject.toml` 的 `[project.optional-dependencies].dev` 中增加 `"cython>=3.0"`, `"setuptools>=64"`。
2. 创建 `scripts/build_cython.py`（`build_ext --inplace`）。
3. 运行 `uv run python scripts/build_cython.py`
4. 验证 `.so` 文件可 import：
   ```bash
   uv run python -c "from simplenes.ppu._ppu_cy import PPUCy; print('OK')"
   ```

### Step 4: Cython PPU 实现

1. 创建 `src/simplenes/ppu/_ppu_cy.pyx`，实现 `PPUCy` 类。
2. `cdef public` 字段覆盖 Public API 兼容性清单所有属性。
3. `__init__` 中缓存 `_bus_read / _bus_write / _peek_palette` callable。
4. `cdef void _clock_one_dot()` 内联全部 dot 逻辑，使用缓存的 callable。
5. `cpdef void clock()` 调用 `_clock_one_dot()`。
6. `cpdef void advance_dots(int n)` 紧凑 C 循环调用 `_clock_one_dot()`。
7. 运行 `uv run pytest tests/ -q` 验证。

### Step 5: Cython 冒烟测试

1. 新增 `tests/unit/test_ppu_cy_smoke.py`：
   - 同一 NROM fixture，纯 Python `PPU` 与 Cython `PPUCy` 各自运行 10 帧，比对 `status`、`control`、`mask`、`scanline`、`dot`、`frame`、`framebuffer` hash。
   - 验证 `clock()` 与 `advance_dots(n)` 推进等价（推进 N dots 后寄存器一致）。
   - 覆盖 PPUSTATUS VBlank/NMI 产生、PPUDATA read buffer、sprite zero hit。
   - 覆盖 OAM DMA 写入 `oam` 后 Cython PPU 状态与 oracle 一致。
2. 运行 `uv run pytest tests/ -q`

### Step 6: 验收

1. 纯 Python fallback（CI 兼容）：
   ```bash
   uv run ruff check src/ tests/
   SIMPLENES_BACKEND=python uv run pytest tests/ -q
   SIMPLENES_BACKEND=python uv run pytest benchmarks/ --benchmark-only -q
   ```
2. Cython 后端（编译后）：
   ```bash
   SIMPLENES_BACKEND=cython uv run pytest tests/ -q
   SIMPLENES_BACKEND=cython uv run pytest benchmarks/ --benchmark-only -q
   ```
3. 对照 benchmark，确认 `test_bench_run_frame` mean ≤ 16.67ms（60 FPS）。

---

## Risks

| 风险 | 缓解 |
|------|------|
| Cython `cdef public` 字段行为与纯 Python 属性不完全一致 | 冒烟测试逐字段比对 |
| Cython 编译在 CI 环境不可用 | Cython optional；CI 使用 `SIMPLENES_BACKEND=python` 强制纯 Python |
| `advance_dots` 破坏 CPU-PPU 寄存器交互时序 | 内部仍逐个 dot 推进，仅消除 scheduler 层 Python 循环 |
| `_clock_one_dot` 移植遗漏导致行为差异 | 基于纯 Python `clock()` 逐行移植；308 tests + 冒烟测试双验证 |
| 缓存 bus callable 后 PPUBus 实例切换（理论上不会发生） | `PPUBus` 构造后不替换；若 mapper 能力变更影响只通过 bus 内部 |
| `NESMachine` import 改为 `from simplenes.ppu import PPU` 遗漏文件 | `grep -r 'from simplenes.ppu.ppu import PPU' src/ benchmarks/` 扫描 |

---

## Non-Goals

- 不修改 CPU / APU / Mapper。
- 不替换 `hatchling` build backend。
- 不编译 PPUBus（Phase 9c 范围内）。
- 不做 pattern table / sprite pattern row cache。
- 不引入 Rust。
- 不提交生成的 `.so` / `.c` / `build/` artifact（本地 benchmark 用，`.gitignore` 排除）。

---

## Success Criteria

| 标准 | 指标 |
|------|------|
| **Hard** | Cython 编译后 `test_bench_run_frame` mean ≤ **16.67ms/frame**（≥60 FPS） |
| **Gate** | 所有 308 个纯 Python 测试继续通过（`SIMPLENES_BACKEND=python`） |
| **Gate** | 所有现有 tests 在 `SIMPLENES_BACKEND=cython` 下通过（直接导入 `simplenes.ppu.ppu.PPU` 的路径覆盖 oracle，通过 `NESMachine` / `from simplenes.ppu import PPU` 的路径覆盖 Cython backend） |
| **Gate** | Cython 冒烟测试 register/framebuffer 与纯 Python oracle 一致 |
| **Gate** | 纯 Python fallback 性能不退化（与 Phase 9b 持平） |
