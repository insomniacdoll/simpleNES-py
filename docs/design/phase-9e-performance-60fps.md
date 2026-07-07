# Phase 9e: CPUBus Cython

> **⚠️ NO-GO — Profiling Gate failed (Phase 9d benchmark, Step 0).**
>
> CPUBus.read 自身耗时 ~1.2% 帧时间（< 10% 阈值）。Phase 9e 实现 CPUBusCy
> 预期节省 < 0.5ms，不值得投入。跳过此阶段，直接见 Phase 9f。
>
> ---

## Summary

Phase 9d 将 `run_frame()` 从 ~28.2ms 降至 ~25.6ms（inline palette + PPUBusCy），但未达到任何 phase gate 目标：

| 指标 | Phase 9c | Phase 9d | 目标 |
|------|----------|----------|------|
| `run_frame` | ~28.2ms | ~25.6ms | ≤16.67ms |
| `clock` visible | ~134ns/dot | ~102ns/dot | — |
| OPS | ~35 | ~39 | ≥60 |

Phase 9d 设计规定：
> `run_frame` > 20ms → 回溯 profiling，重新评估瓶颈

基于 Phase 9d 推测瓶颈分布（按 25.6ms 拆分）：

| 组件 | 估算耗时 | 占比 | 说明 |
|------|---------|------|------|
| PPU internal (Cython) | ~9ms | 35% | 102ns × 89K dots |
| CPU 纯 Python 路径 | ~6ms | 23% | 30K 指令，opcode dispatch + 地址解析 + 算数逻辑 |
| CPUBus (纯 Python, ~120K r/w) | ~4ms | 16% | CPU 每条指令 3–6 次 bus 访问 |
| mapper / CHR 访问 (~50K calls) | ~3ms | 12% | ppu_read/write 的 Python→mapper 调用 |
| Scheduler + 循环开销 | ~2ms | 8% | step_instruction for loop |
| 其它 | ~1.6ms | 6% | APU, interrupt, OAM DMA |

本阶段核心策略：**Cython CPUBus** 削减 CPU memory access 开销。

## Phase Gate

| 结果 | 行动 |
|------|------|
| `run_frame` ≤ 16.67ms | **Phase 9 complete** — 60 FPS 达标 |
| 16.67ms < `run_frame` ≤ 20ms | **Phase 9f required** — Cython CPU |
| 20ms < `run_frame` ≤ 24ms | **Phase 9f required** — Cython CPU |
| `run_frame` > 24ms | 回溯 profiling，确认 CPU/CPUBus 以外有新瓶颈 |

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/bus/_cpu_bus_cy.pyx` | **新增** — Cython CPUBus 实现 |
| `src/simplenes/bus/__init__.py` | **修改** — 扩展为同时处理 PPUBus + CPUBus 后端 |
| `src/simplenes/machine.py` | **修改** — import 路径改为 `from simplenes.bus import CPUBus` |
| `scripts/build_cython.py` | **修改** — 增加 `_cpu_bus_cy` 编译目标 |
| `tests/unit/test_cpu_bus_cy_smoke.py` | **新增** — Cython CPUBus 冒烟测试 |

**不变：** `ppu/`、`scheduler.py`、`cpu/`、`apu/`、`mappers/`。

## Architecture Decisions

### AD-9e.1：CPUBus Cython — 受限作用域

`_cpu_bus_cy.pyx` 仅编译 `CPUBus.read/write` 为 cpdef，内部组件引用（PPU、APU、mapper、controller、OAM DMA state）均为 `object`。

**不引入 cimport / typed dispatch。** CPU 仍为纯 Python，通过 Python bound-method 调用 bus.read/write。收益来自 bus 内部地址路由的 Cython 化（int 比较 + bytearray 访问）。

与 Phase 9d 的 PPUBusCy 性能定位类似：预期节省 1–2 ms/frame。

```
                  Python CPUBus                       Cython CPUBusCy
CPU (Python) ───→ read(addr)  ──CPython call──→  read(addr)
                                          addr routing (C int cmp)
                                          return byte/register val
```

### AD-9e.2：CPUBus Cython 结构

```cython
cdef class CPUBusCy:
    cdef:
        bytearray _ram                                     # 2048 bytes
        object _ppu, _apu, _mapper, _controller1, _controller2
        object _oam_dma_state

    def __init__(self, ppu, apu, mapper, controller1, controller2, oam_dma_state):
        self._ram = bytearray(2048)
        self._ppu = ppu
        self._apu = apu
        self._mapper = mapper
        self._controller1 = controller1
        self._controller2 = controller2
        self._oam_dma_state = oam_dma_state

    cpdef int read(self, int address):
        address &= 0xFFFF
        if address < 0x2000:
            return self._ram[address & 0x07FF]
        if address < 0x4000:
            return self._ppu.read_register(0x2000 | (address & 0x0007))
        if address == 0x4015:
            return self._apu.read_status()
        if address == 0x4016:
            return self._controller1.read()
        if address == 0x4017:
            return self._controller2.read()
        if address >= 0x4020:
            return self._mapper.cpu_read(address)
        return 0

    cpdef void write(self, int address, int value):
        address &= 0xFFFF
        value &= 0xFF
        if address < 0x2000:
            self._ram[address & 0x07FF] = value
            return
        if address < 0x4000:
            self._ppu.write_register(0x2000 | (address & 0x0007), value)
            return
        if address < 0x4014:
            self._apu.write_register(address, value)
            return
        if address == 0x4014:
            self._oam_dma_state.trigger(value)
            return
        if address == 0x4015:
            self._apu.write_register(address, value)
            return
        if address == 0x4016:
            self._controller1.write_strobe(value)
            self._controller2.write_strobe(value)
            return
        if address == 0x4017:
            self._apu.write_register(address, value)
            return
        if address >= 0x4020:
            self._mapper.cpu_write(address, value)
            return
```

### AD-9e.3：`bus/__init__.py` 扩展 — 同时导出 PPUBus 和 CPUBus

当前 `bus/__init__.py` 仅处理 PPUBus 后端。Phase 9e 扩展为双 module 后端：

```python
"""PPUBus / CPUBus module — auto-selects Cython or pure Python backends."""
import os

_backend = os.environ.get("SIMPLENES_BACKEND", "")

if _backend == "python":
    from simplenes.bus.ppu_bus import PPUBus   # noqa: F401
    from simplenes.bus.cpu_bus import CPUBus   # noqa: F401
elif _backend == "cython":
    from simplenes.bus._ppu_bus_cy import PPUBusCy as PPUBus  # noqa: F401
    from simplenes.bus._cpu_bus_cy import CPUBusCy as CPUBus  # noqa: F401
else:
    try:
        from simplenes.bus._ppu_bus_cy import PPUBusCy as PPUBus  # noqa: F401
    except ImportError:
        from simplenes.bus.ppu_bus import PPUBus                 # noqa: F401
    try:
        from simplenes.bus._cpu_bus_cy import CPUBusCy as CPUBus  # noqa: F401
    except ImportError:
        from simplenes.bus.cpu_bus import CPUBus                  # noqa: F401

__all__ = ["PPUBus", "CPUBus"]
```

### AD-9e.4：SIMPLENES_BACKEND 行为矩阵不变

| `SIMPLENES_BACKEND` | PPU | PPUBus | CPUBus | 行为 |
|---------------------|------|--------|--------|------|
| (unset) | auto | auto | auto | 各自独立 autodetect |
| `python` | pure | pure | pure | 全部强制纯 Python |
| `cython` | Cython | Cython | Cython | 三者都必须可用；任一缺失 → `ImportError` |

## Import 路径（Phase 9e 最终状态）

| 导入路径 | 用途 | 解析为 |
|----------|------|--------|
| `from simplenes.bus import PPUBus` | 生产代码 | Cython `PPUBusCy` or pure `PPUBus` |
| `from simplenes.bus import CPUBus` | 生产代码 | Cython `CPUBusCy` or pure `CPUBus` |
| `from simplenes.bus.ppu_bus import PPUBus` | 测试 oracle | 纯 Python `PPUBus` |
| `from simplenes.bus.cpu_bus import CPUBus` | 测试 oracle | 纯 Python `CPUBus` |

## Build Infrastructure

### scripts/build_cython.py 更新

```python
extensions = [
    Extension("simplenes.ppu._ppu_cy",     ["src/simplenes/ppu/_ppu_cy.pyx"]),
    Extension("simplenes.bus._ppu_bus_cy", ["src/simplenes/bus/_ppu_bus_cy.pyx"]),
    Extension("simplenes.bus._cpu_bus_cy", ["src/simplenes/bus/_cpu_bus_cy.pyx"]),
]
```

## Implementation Plan

### Step 0: Profiling Gate（必做）

在实现 CPUBusCy 之前，确认 CPUBus 确实是值得优化的热点：

```bash
SIMPLENES_BACKEND=cython uv run python -m cProfile -s cumulative \
  -m pytest benchmarks/test_bench_scheduler.py --benchmark-disable -q 2>&1 | head -40
```

重点关注：
- `cpus` 模块内 `read`/`write` 累计时间占比
- CPU `step_instruction` 内 `entry.handler` 开销
- Mapper `ppu_read`/`ppu_write` 调用频次

Go / no-go 规则：
- CPUBus `read`/`write` ≥ 10% 帧时间 → **Go**，实现 CPUBusCy
- CPU opcode handlers > 60% 且 CPUBus < 5% → **No-Go**，跳过 Phase 9e 直接设计 Phase 9f
- Mapper 调用 > 25% → **No-Go**，重新评估 mapper/PPU fetch 热点

### Step 1: CPUBus Cython 实现 + 编译

1. 创建 `src/simplenes/bus/_cpu_bus_cy.pyx`，实现 `CPUBusCy`。
2. 更新 `scripts/build_cython.py` 增加 `_cpu_bus_cy` Extension。
3. 编译：`uv run python scripts/build_cython.py`。
4. 确定编译成功，无 import 错误。

### Step 2: `bus/__init__.py` 扩展

1. 修改 `bus/__init__.py`，同时处理 PPUBus + CPUBus 后端 fallback。
2. 运行 `SIMPLENES_BACKEND=python uv run pytest tests/ -q`（确保纯 Python 路径正常）。

### Step 3: `machine.py` import 路径更新

1. `machine.py`：`from simplenes.bus.cpu_bus import CPUBus` → `from simplenes.bus import CPUBus`。
3. 运行 `SIMPLENES_BACKEND=python uv run pytest tests/ -q`。

### Step 4: CPUBus Cython 冒烟测试

1. 新增 `tests/unit/test_cpu_bus_cy_smoke.py`；
   **必须** 用 `pytest.importorskip("simplenes.bus._cpu_bus_cy")` 直接 import `CPUBusCy`：
   ```python
   _cpu_bus_cy = pytest.importorskip("simplenes.bus._cpu_bus_cy")
   CPUBusCy = _cpu_bus_cy.CPUBusCy
   ```
   对比 oracle：
   ```python
   from simplenes.bus.cpu_bus import CPUBus as PyCPUBus
   ```
   测试项：
   - RAM mirror ($0000–$1FFF)：`CPUBusCy` vs `PyCPUBus` read/write 一致。
   - PPU 寄存器 ($2000–$2007) 路由正确。
   - APU 寄存器 ($4000–$4017) 路由正确（含 $4016 strobe）。
   - OAM DMA ($4014) trigger 调用。
   - Mapper 区域 ($4020–$FFFF) 正确路由。
   - **禁止** 访问 `_ram` 等 `cdef` 私有字段，只使用 public API。
2. 有 `.so`：测试实际跑 `CPUBusCy`；无 `.so`：`importorskip` 跳过。

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
   - 16.67–24ms → 进入 Phase 9f (Cython CPU)。
   - \> 24ms → 回溯 profiling。
4. CPUBus 专项性能 gate：
   - CPUBusCy RAM read/write 不得慢于纯 Python CPUBus（可选 microbenchmark 验证）。
   - `test_bench_run_frame` 不得 regress（vs Phase 9d ~25.6ms）。
   - 若 `run_frame` 改善 < 0.5ms，标记 Phase 9e insufficient，跳转到 Phase 9f。

## Risks

| 风险 | 缓解 |
|------|------|
| CPUBus Cython 与纯 Python 行为不一致 | 冒烟测试覆盖 RAM/PPU/APU/mapper 全部路由路径 |
| OAM DMA 通过 CPUBus 读写未正确触发 | 冒烟测试含 $4014 trigger 验证 |
| Controller strobe 副作用遗漏 | 冒烟测试含 $4016 write → controller1/2 strobe |
| 收益未达预期（< 1ms） | 若结果 > 24ms，回溯 profiling 确认 CPU 才是瓶颈 |
| `_ppu.read_register` / `_ppu.write_register` 为 Cython 时跨语言调用仍慢 | 不引入 typed dispatch；这属于 Phase 9f 范畴 |

## Non-Goals

- 不修改 CPU / opcodes。
- 不修改 PPU / PPUBus。
- 不修改 APU / Mapper。
- 不引入 cimport / typed bus dispatch（留到 Phase 9f）。

## Success Criteria

| 标准 | 指标 |
|------|------|
| **Hard** | `SIMPLENES_BACKEND=cython` 下 `test_bench_run_frame` mean ≤ **16.67ms/frame** |
| **Gate** | 所有测试在 Cython 和纯 Python 后端均通过 |
| **Gate** | CPUBus 冒烟测试通过（RAM/PPU/APU/mapper/OAM DMA 路由） |
| **Gate** | 纯 Python fallback 性能不退化 |
| **Fallback** | 若不达 16.67ms，不 merge 为 "60 FPS complete"；根据 Phase Gate 进入 Phase 9f 或回溯 profiling |

## Profiling 策略（若 Phase 9e 结果 > 24ms）

若 Phase 9e 基准仍 > 24ms/frame，则在进入 Phase 9f 前执行 profiling：

```bash
SIMPLENES_BACKEND=cython uv run python -m cProfile -s cumulative \
  -m pytest benchmarks/test_bench_scheduler.py --benchmark-disable -q
```

重点关注：
- `CPU.step_instruction` 内部 `entry.handler(self)` 的 Python 方法调用开销
- `CPUBus.read/write` 的 cProfile 统计（应该显著下降）
- `Scheduler.step_instruction` 的 for loop 开销
- Mapper `ppu_read/write` 的调用频次和耗时

若 CPUBus 开销已显著下降但 CPU 成为单一最大瓶颈（> 50%），则 Phase 9f 的 Cython CPU 是明确方向。
