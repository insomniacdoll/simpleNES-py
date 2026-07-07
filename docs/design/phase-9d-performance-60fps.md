# Phase 9d: 60 FPS 性能优化设计 — PPUBus + Inline Palette（rev3）

## Summary

Phase 9c Cython PPU 将 `run_frame()` 从 ~37ms 降至 ~28.2ms（1.3×），但未达 60 FPS（16.67ms/frame）。

瓶颈分析（基于 Phase 9c benchmark）：

| 组件 | 估计耗时 | 说明 |
|------|---------|------|
| PPU internal (Cython) | ~12ms | 134ns/dot × 89K dots |
| `peek_palette` (120K calls/frame) | ~4ms | 每可见 pixel 1-2 次 Python bound method 调用 |
| `bus.read` tile fetch (50K calls/frame) | ~5ms | 背景 + 精灵 tile fetch 的 Python→mapper 调用 |
| CPU + Scheduler | ~7ms | 30K 指令，纯 Python opcode dispatch + bus |

本阶段双重策略：

1. **Inline peek_palette** — 直接将 `_palette_cache` bytearray 传入 PPUCy，消除 120K Python 方法调用/frame。
2. **PPUBus Cython** — 编译 `PPUBus.read/write` 为 cpdef，加速 bus 内部 nametable/palette 路径。

注意：Phase 9d **不引入** cimport / typed bus dispatch。`PPUCy` 仍通过 cached Python callable
（`self._bus_read = bus.read`）调用 `PPUBusCy.read()`，调用入口仍为 Python bound-method。
性能收益预计在 2–4 ms/frame，主要来自 PPUBus 内部 Cython 化；进一步加速需 Phase 9e 做 CPU/CPUBus 编译。

预计 `run_frame` 降至 ~18-20ms。若仍不达 60 FPS（≤16.67ms），则进入 Phase 9e（CPU/CPUBus）。

---

## Phase Gate

| 结果 | 行动 |
|------|------|
| `run_frame` ≤ 16.67ms | **Phase 9 complete** — 60 FPS 达标 |
| 16.67ms < `run_frame` ≤ 20ms | **Phase 9e required** — 加速 CPU opcode dispatch / CPUBus |
| `run_frame` > 20ms | 回溯 profiling，重新评估瓶颈 |

无论结果如何，**Phase 9d 不应被单独 merge 为 "60 FPS complete"**，除非 hard target 实际满足。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/_ppu_cy.pyx` | **修改** — 增加 `_palette_cache` 字段，inline peek 逻辑 |
| `src/simplenes/ppu/ppu.py` | **修改** — `__init__` 增加 `palette_cache=None` 参数（忽略） |
| `src/simplenes/bus/ppu_bus.py` | **修改** — 新增 `get_palette_cache()` public 方法 |
| `src/simplenes/bus/_ppu_bus_cy.pyx` | **新增** — Cython PPUBus 实现 |
| `src/simplenes/bus/__init__.py` | **修改** — PPUBus fallback import |
| `src/simplenes/ppu/__init__.py` | **不变** — Phase 9c 已完成的 PPU fallback import |
| `src/simplenes/machine.py` | **修改** — import 路径 + 构造 PPU 时通过 `get_palette_cache()` 传入 |
| `benchmarks/conftest.py` | **修改** — import 路径同步 |
| `scripts/build_cython.py` | **修改** — 增加 `_ppu_bus_cy` 编译目标 |

---

## Architecture Decisions

### AD-9d.1：Inline peek_palette — 传入 palette_cache bytearray

当前 PPUCy 每 dot 调用：

```python
palette_idx = self._peek_palette(idx)  # Python bound method call
```

改为在构造时接收 `_palette_cache` bytearray 引用，直接在 C 层字节数组索引：

```cython
cdef bytearray _palette_cache

# __init__:
self._palette_cache = palette_cache  # bytearray(32) from PPUBus

# 替换 self._peek_palette(x) 为:
cdef int _inline_peek(self, int index):
    cdef int idx
    if self._palette_cache is not None:
        idx = index & 0x1F
        if idx >= 0x10 and (idx & 3) == 0:
            idx &= 0x0F
        return self._palette_cache[idx] & 0x3F
    return self._peek_palette(index)
```

PPUBus 保持 `palette_cache` 同步语义不变（构造时 sync，palette write 时更新）。`PPUCy` 持有同一个 `bytearray` 对象引用，写入自动可见。

纯 Python `PPU` oracle 不受影响——仅 `PPUCy` 使用 inline path。

### AD-9d.2：PPUBus Cython — 安全编译热路径

创建 `_ppu_bus_cy.pyx`，编译 `read/write/peek_palette` 为 cpdef。

关键安全约束：

- **mirroring 每次动态读取**：`self._mapper.mirroring` 每调用一次，不缓存。MMC1 切换 mirroring 后自动生效。
- **mapper observer 保持**：`self._observe_ppu` 逻辑不变，MMC3 A12 IRQ 不受影响。
- **mapper.ppu_read/ppu_write 仍为 Python 调用**：mapper 本身不编译，调用开销可接受（CHR 读取已被 Cython context 优化）。

#### 结构

```cython
cdef class PPUBusCy:
    cdef:
        object _mapper
        bytearray _nametables, _palette_ram, _palette_cache
        object _observe_ppu  # None or mapper.observe_ppu_address

    # --- Construction-time Mirroring enum value cache ---
    cdef int _mir_h, _mir_v, _mir_sl, _mir_su, _mir_4s

    cpdef int read(self, int address):
        ...

    cpdef void write(self, int address, int value):
        ...

    cpdef int peek_palette(self, int index):
        # Kept for API compatibility; PPUCy uses inline path
        ...
```

### AD-9d.3：统一 `get_palette_cache()` 接口

纯 Python `PPUBus` 和 Cython `PPUBusCy` 均提供：

```python
def get_palette_cache(self) -> bytearray:
    """Return the shared palette cache bytearray for inline peek access."""
    return self._palette_cache
```

`NESMachine` 使用：

```python
self._ppu_bus = PPUBus(self._mapper)
palette_cache = self._ppu_bus.get_palette_cache()
self._ppu = PPU(bus=self._ppu_bus, interrupts=self._interrupts,
                palette_cache=palette_cache)
```

不使用 `getattr(bus, '_palette_cache', None)` —— 稳定的 public 接口更可靠。

### AD-9d.4：Mirroring 动态读取 + 构造期枚举缓存

PPUBus 不缓存当前 mirroring 值。每次 `_nametable_index()` 读取 `int(self._mapper.mirroring)`。

为加速 C 级比较，在 `__init__` 时缓存 Mirroring 枚举的 int 值：

```cython
from simplenes.cartridge.image import Mirroring
self._mir_h  = int(Mirroring.HORIZONTAL)
self._mir_v  = int(Mirroring.VERTICAL)
self._mir_4s = int(Mirroring.FOUR_SCREEN)
self._mir_sl = int(Mirroring.SINGLE_SCREEN_LOWER)
self._mir_su = int(Mirroring.SINGLE_SCREEN_UPPER)
```

运行时：

```cython
cdef int _nametable_index(self, int address):
    cdef int mirroring = int(self._mapper.mirroring)
    cdef int nt_select = (address >> 10) & 3
    if mirroring == self._mir_h:
        nt_select = nt_select >> 1
    elif mirroring == self._mir_v:
        nt_select = nt_select & 1
    elif mirroring == self._mir_sl:
        nt_select = 0
    elif mirroring == self._mir_su:
        nt_select = 1
    elif mirroring == self._mir_4s:
        from simplenes.errors import PPUBusError
        raise PPUBusError("Four-screen mirroring is not supported")
    return nt_select * 1024 + (address & 0x3FF)
```

### AD-9d.5：SIMPLENES_BACKEND 完整行为矩阵

PPU 和 PPUBus 各自由各自的 `__init__.py` fallback，但 `SIMPLENES_BACKEND` 环境变量统一控制：

| `SIMPLENES_BACKEND` | PPU | PPUBus | 行为 |
|---------------------|------|--------|------|
| (unset) | auto | auto | 各自独立 autodetect；可混合 |
| `python` | pure Python | pure Python | 强制纯 Python，用于 CI oracle |
| `cython` | Cython | Cython | **两者都必须可用**；任一缺失 → `ImportError` |

#### `simplenes/bus/__init__.py`

```python
import os
_backend = os.environ.get("SIMPLENES_BACKEND", "")
if _backend == "python":
    from simplenes.bus.ppu_bus import PPUBus
elif _backend == "cython":
    from simplenes.bus._ppu_bus_cy import PPUBusCy as PPUBus
else:
    try:
        from simplenes.bus._ppu_bus_cy import PPUBusCy as PPUBus
    except ImportError:
        from simplenes.bus.ppu_bus import PPUBus
```

`ppu/__init__.py` 保持 Phase 9c 逻辑不变。

---

## Cython PPUBus 实现要点

### 完整 `PPUBusCy` 代码结构

```cython
cdef class PPUBusCy:
    cdef:
        object _mapper
        bytearray _nametables, _palette_ram, _palette_cache
        object _observe_ppu
        int _mir_h, _mir_v, _mir_sl, _mir_su, _mir_4s

    def __init__(self, mapper):
        from simplenes.cartridge.image import Mirroring

        self._mapper = mapper
        self._nametables = bytearray(2048)
        self._palette_ram = bytearray(32)
        self._palette_cache = bytearray(32)
        self._sync_palette_cache()

        self._observe_ppu = (
            self._mapper.observe_ppu_address
            if getattr(self._mapper, "has_ppu_observer", False)
            else None
        )

        # Cache Mirroring enum int values for fast C comparison
        self._mir_h  = int(Mirroring.HORIZONTAL)
        self._mir_v  = int(Mirroring.VERTICAL)
        self._mir_4s = int(Mirroring.FOUR_SCREEN)
        self._mir_sl = int(Mirroring.SINGLE_SCREEN_LOWER)
        self._mir_su = int(Mirroring.SINGLE_SCREEN_UPPER)

    def get_palette_cache(self):
        """Return the shared palette cache bytearray."""
        return self._palette_cache

    cpdef int read(self, int address):
        ...

    cpdef void write(self, int address, int value):
        ...

    cpdef int peek_palette(self, int index):
        ...

    cdef int _nametable_index(self, int address):
        ...

    # _sync_palette_cache, _palette_index, _read_palette, _write_palette,
    # _read_nametable, _write_nametable — same logic as PPUBus
```

### PPUCy `__init__` 签名（修正 keyword-only 问题）

```cython
def __init__(self, bus, interrupts, region=None, palette_cache=None):
```

纯 Python `PPU.__init__` 同步：

```python
def __init__(self, bus, interrupts, *, region=None, palette_cache=None):
```

注意：Cython 端不用 `*`，Python 端保留 `*`。调用方始终使用 keyword 传参，兼容两者。

---

## Import 路径矩阵（Phase 9d 最终状态）

| 导入路径 | 用途 | 解析为 |
|----------|------|--------|
| `from simplenes.ppu import PPU` | 生产代码 | Cython `PPUCy` or pure `PPU` |
| `from simplenes.ppu.ppu import PPU` | 测试 oracle | 纯 Python `PPU` |
| `from simplenes.bus import PPUBus` | 生产代码 | Cython `PPUBusCy` or pure `PPUBus` |
| `from simplenes.bus.ppu_bus import PPUBus` | 测试 oracle | 纯 Python `PPUBus` |

---

## Build Infrastructure

### scripts/build_cython.py 更新

```python
extensions = [
    Extension("simplenes.ppu._ppu_cy",
              ["src/simplenes/ppu/_ppu_cy.pyx"]),
    Extension("simplenes.bus._ppu_bus_cy",
              ["src/simplenes/bus/_ppu_bus_cy.pyx"]),
]
```

剩余 setup() 参数不变（`package_dir={"": "src"}`, `script_args=["build_ext", "--inplace"]`）。

### pyproject.toml

Phase 9c 已添加 `cython>=3.0`, `setuptools>=64`，无需新增依赖。

---

## Data Model Changes

| 字段/方法 | 位置 | 变更 |
|-----------|------|------|
| `_palette_cache` | `_ppu_cy.pyx` | **新增** — bytearray(32)，用于 inline peek |
| `_inline_peek(index)` | `_ppu_cy.pyx` | **新增** — cdef 方法，替换 `_peek_palette()` 调用 |
| `get_palette_cache()` | `ppu_bus.py` | **新增** — public 方法 |
| `get_palette_cache()` | `_ppu_bus_cy.pyx` | **新增** — Python-callable |
| `palette_cache` 参数 | `PPU.__init__` / `PPUCy.__init__` | **新增** — optional |
| `_mir_h, _mir_v, ...` | `_ppu_bus_cy.pyx` | **新增** — Mirroring enum int 缓存 |

---

## Implementation Plan

### Step 1: `get_palette_cache()` + PPUBus import 修正

1. `ppu_bus.py` 新增 `get_palette_cache()` → `return self._palette_cache`。
2. 编辑 `src/simplenes/bus/__init__.py`，写入 SIMPLENES_BACKEND 逻辑。
3. `machine.py`: `from simplenes.bus.ppu_bus import PPUBus` → `from simplenes.bus import PPUBus`。
4. `benchmarks/conftest.py` 同步。
5. 运行 `SIMPLENES_BACKEND=python uv run pytest tests/ -q`。

### Step 2: PPUBus Cython 实现

1. 创建 `src/simplenes/bus/_ppu_bus_cy.pyx`。
2. 实现 `PPUBusCy`，含 constructor Mirroring enum 缓存 + `cpdef read/write`。
3. 编译 + 运行 `SIMPLENES_BACKEND=cython uv run pytest tests/ -q`。

### Step 3: Inline peek + palette_cache 参数

1. `_ppu_cy.pyx` 增加 `_palette_cache` / `_inline_peek()`。
2. `__init__` 增加 `palette_cache=None` 参数。
3. `ppu.py` `PPU.__init__` 同步增加参数。
4. `machine.py` 使用 `ppu_bus.get_palette_cache()` 传入。
5. 编译 + 运行 tests。

### Step 4: PPUBus Cython 冒烟测试

1. 新增 `tests/unit/test_ppu_bus_cy_smoke.py`；
   **必须** 用 `pytest.importorskip("simplenes.bus._ppu_bus_cy")` 直接 import `PPUBusCy`，
   不可通过 `from simplenes.bus import PPUBus` fallback 到 pure Python：
   ```python
   _ppu_bus_cy = pytest.importorskip("simplenes.bus._ppu_bus_cy")
   PPUBusCy = _ppu_bus_cy.PPUBusCy
   ```
   对比 oracle：
   ```python
   from simplenes.bus.ppu_bus import PPUBus as PyPPUBus
   ```
   测试项：
   - HORIZONTAL / VERTICAL mirroring：`PPUBusCy` vs `PyPPUBus` 读写一致。
   - Palette `$3F10/$3F14` mirror：两者行为相同。
   - MMC1 dynamic mirroring：切换后 nametable 正确。
   - MMC3 observer：`observe_ppu_address` 被正常调用。
   - **禁止** 访问 `_palette_cache` / `_nametables` / `_palette_ram` 等 `cdef` 私有字段，只使用 public API。
   - `get_palette_cache()` 同步验证：`bus.write(0x3F01, 0x7F)` 后 `cache[1] == 0x3F` 且 `bus.peek_palette(1) == 0x3F`。
2. 有 `.so`：测试实际跑 `PPUBusCy`；无 `.so`：`importorskip` 跳过，不会 fallback。

### Step 5: 验收

1. 纯 Python fallback：
   ```bash
   uv run ruff check src/ tests/
   SIMPLENES_BACKEND=python uv run pytest tests/ -q
   SIMPLENES_BACKEND=python uv run pytest benchmarks/ --benchmark-only -q
   ```
2. Cython 后端：
   ```bash
   uv run python scripts/build_cython.py
   SIMPLENES_BACKEND=cython uv run pytest tests/ -q
   SIMPLENES_BACKEND=cython uv run pytest benchmarks/ --benchmark-only -q
   ```
3. 判定：
   - ≤ 16.67ms → Phase 9 完成。
   - 16.67-20ms → 进入 Phase 9e (CPU/CPUBus)。
   - > 20ms → 回溯 profiling。

---

## Risks

| 风险 | 缓解 |
|------|------|
| Mirroring enum int 值随时间变化（如 enum 重排） | 构造期缓存 `int(Mirroring.HORIZONTAL)` 等，运行时 int 比较 |
| PPUBus Cython observer 行为与纯 Python 不一致 | 冒烟测试 + MMC3 IRQ 路径验证 |
| palette_cache 引用同步 | PPUBus 写入 palette 直接修改 shared bytearray，PPUCy 自动可见 |
| `_inline_peek` fallback 路径覆盖不足 | NESMachine 使用 `get_palette_cache()` 总是传入；`_inline_peek` 中 `is not None` 分支仅用于纯 Python bus 场景 |
| 混合 backend（PPU Cy + Bus Py）行为不一致 | 设计不禁止混合但也不保证优化；CI/benchmark 只用统一 backend |

---

## Non-Goals

- 不修改 CPU / APU / Mapper。
- 不做 pattern cache / sprite cache。
- 不编译 mapper。
- 不替换 `hatchling`。

---

## Success Criteria

| 标准 | 指标 |
|------|------|
| **Hard** | `SIMPLENES_BACKEND=cython` 下 `test_bench_run_frame` mean ≤ **16.67ms/frame**（≥60 FPS） |
| **Gate** | 所有测试在 Cython 和纯 Python 后端均通过 |
| **Gate** | Mirroring 冒烟测试（HORIZONTAL / VERTICAL / MMC1 dynamic）通过 |
| **Gate** | 纯 Python fallback 性能不退化 |
| **Fallback** | 若不达 16.67ms，不 merge 为 "60 FPS complete"；根据 Phase Gate 进入 Phase 9e 或回溯 profiling |
