# Phase 9f: Cython CPU — CPUCy

## Summary

Phase 9e profiling（Step 0）确认：CPUBus 不是瓶颈。真正热点在 CPU opcode dispatch + 内部方法调用。

Phase 9e 判定为 **No-Go**，直接进入 Phase 9f。

### Phase 9d 基准

| 指标 | Phase 9d (Cython) |
|------|----|
| `run_frame` | ~25.6ms |
| OPS | ~39 |

### 瓶颈（Phase 9e Step 0 确认）

| 热点 | 调用数/frame | 占帧时 | 说明 |
|------|-------------|--------|------|
| CPU `step_instruction` | 11.9K | ~6% | Python 方法调用 + 查表 dispatch |
| Opcode handler（`entry.handler(self)`） | 11.9K | ~16% | Python 函数调用，内部多次 `_read` / `_write` |
| Address mode helpers（`_abs` / `_read_pc` 等） | 47K | ~8% | 每个 operand fetch |
| 内部 helper（`_set_nz` / `_push` / `_pull` 等） | 混合 | ~3% | flag 计算、stack push/pop |

合计 CPU 路径约占 `run_frame` 的 30%+（~7.8ms）。

### 策略

**Cython CPUCy**：编译 `CPU` 类为 `cdef class`，保留 opcode handlers 在纯 Python。

关键权衡：
- 不重写 150+ opcode handlers（风险大、工期长）
- 将 CPU 内部方法（`_read_pc` / `_read` / `_write` / `_set_nz` / `_push` / `_pull` / flag helpers）全部标记 `cpdef`
- opcode handlers 在 Python 中调用 `cpdef` 方法：调用边界有 Python overhead，但方法体编译为 C
- `step_instruction` 为 `cpdef`，查表 + dispatch 均在编译后执行

预期节省 3–5ms → ~20–22ms/frame。

## Phase Gate

| 结果 | 行动 |
|------|------|
| `run_frame` ≤ 16.67ms | **Phase 9 complete** — 60 FPS 达标 |
| 16.67ms < `run_frame` ≤ 22ms | **Phase 9g required** — 后续优化（mapper/Scheduler/APU 选优） |
| `run_frame` > 22ms | 回溯 profiling，确认 CPU 外部瓶颈 |

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/cpu/_cpu_cy.pyx` | **新增** — Cython CPUCy 类 |
| `src/simplenes/cpu/__init__.py` | **新增/修改** — CPU backend 选择（与 PPU/PPUBus 相同模式） |
| `src/simplenes/cpu/cpu.py` | **不变** — 纯 Python oracle |
| `src/simplenes/cpu/opcodes.py` | **不变** — handler 表保持纯 Python |
| `src/simplenes/machine.py` | **修改** — import 改为 `from simplenes.cpu import CPU` |
| `scripts/build_cython.py` | **修改** — 增加 `_cpu_cy` Extension |
| `tests/unit/test_cpu_cy_smoke.py` | **新增** — CPUCy 冒烟测试 |

**不变：** `ppu/`、`bus/`、`apu/`、`mappers/`、`scheduler.py`。

## Architecture Decisions

### AD-9f.1：CPUCy 继承策略 — 不复用 Python CPU 基类

`CPUCy` 并非 `CPU` 的子类。它是一个独立的 `cdef class`，包含与 `CPU` 相同的方法签名（all `cpdef`），但与 Python `CPU` 无继承关系。

原因：
- Cython `cdef class` 无法继承普通 Python class 并保留 cdef 字段
- 保持两个实现完全独立有利于 oracle 验证

### AD-9f.2：opcode handlers 留在 Python

`opcodes.py` 的 150+ handler 函数和 `OPCODES` 表**不做修改**。它们接收 `CPU` 对象作为参数。

由于 handlers 调用 `cpu._read_pc()`、`cpu._set_nz()` 等，`CPUCy` 必须将这些方法暴露为 `cpdef`：

```cython
cdef class CPUCy:
    cdef public:
        int a, x, y, sp, pc, p
        unsigned long long total_cycles
        bint halted, _page_crossed, _irq_prev
        int _last_pc, _last_opcode
        object bus, interrupts

    cdef public int STACK_BASE, VECTOR_NMI, VECTOR_RESET, VECTOR_IRQ
    cdef public int FLAG_C, FLAG_Z, FLAG_I, FLAG_D, FLAG_B, FLAG_U, FLAG_V, FLAG_N

    cdef:
        object _bus_read, _bus_write
        object _trace_logger, _trace_callback
        object _opcodes

    cpdef int _read(self, int addr):
        return self._bus_read(addr)

    cpdef void _write(self, int addr, int value):
        self._bus_write(addr, value)

    cpdef int _read_pc(self):
        cdef int val = self._read(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return val

    cpdef void _set_nz(self, int value):
        value &= 0xFF
        self.p = (self.p & ~(0x80 | 0x02)) | (value & 0x80)
        if value == 0:
            self.p |= 0x02

    cpdef void _push(self, int value):
        self._write(0x100 | self.sp, value & 0xFF)
        self.sp = (self.sp - 1) & 0xFF

    cpdef int _pull(self):
        self.sp = (self.sp + 1) & 0xFF
        return self._read(0x100 | self.sp)

    cpdef void _add_with_carry(self, int value):
        cdef int a = self.a, c = self.p & 1
        cdef int temp = a + value + c
        cdef int result = temp & 0xFF
        cdef int v = (~(a ^ value) & (a ^ result)) & 0x40
        self.p = (self.p & ~(0x40 | 1)) | v | (1 if temp > 0xFF else 0)
        self.a = result
        self._set_nz(self.a)

    cpdef int step_instruction(self):
        ...
```

关键原则：
- 所有 handler 调用的方法标记为 `cpdef`（`_read_pc` / `_read` / `_write` / `_set_nz` / `_push` / `_pull` / `_add_with_carry` / `_sub_with_carry` / `_compare` / `_read_word` / `_push_word` / `_pull_word` / `_check_nmi` / `_check_irq`）
- 内部标记操作（`cpdef void _set_flag_*` 等）可以不提供 Python 调用
- `_bus_read` / `_bus_write` / `_opcodes` 缓存 bus callable 和 opcode 表，在 `__init__` 中设置
- CPU constants（`STACK_BASE` / `FLAG_*` 等）声明为 `cdef public int`，在 `__init__` 中初始化：

```cython
def __init__(self, bus, interrupts):
    from simplenes.cpu.opcodes import OPCODES

    self.bus = bus
    self.interrupts = interrupts
    self._bus_read = bus.read
    self._bus_write = bus.write
    self._opcodes = OPCODES

    # CPU constants (must be cdef public int for Python handler access)
    self.STACK_BASE = 0x0100
    self.VECTOR_NMI = 0xFFFA
    self.VECTOR_RESET = 0xFFFC
    self.VECTOR_IRQ = 0xFFFE

    self.FLAG_C = 0x01
    self.FLAG_Z = 0x02
    self.FLAG_I = 0x04
    self.FLAG_D = 0x08
    self.FLAG_B = 0x10
    self.FLAG_U = 0x20
    self.FLAG_V = 0x40
    self.FLAG_N = 0x80
```


### AD-9f.3：`step_instruction` 实现

> Cython 要求所有 `cdef` local declaration 放在方法开头、任何非声明语句之前。

```cython
cpdef int step_instruction(self):
    cdef int pc_before, opcode, cycles, intr, total
    cdef object entry

    pc_before = self.pc
    opcode = self._read_pc()
    entry = self._opcodes[opcode]

    self._last_pc = pc_before
    self._last_opcode = opcode

    # ---- trace capture (same as pure Python CPU) ----
    if self._trace_logger is not None and self._trace_logger.enabled:
        operand_bytes = tuple(
            self._read((pc_before + i) & 0xFFFF)
            for i in range(1, entry.length)
        )
        self._trace_logger.capture(self, pc_before, opcode, operand_bytes, entry)
        if self._trace_callback is not None:
            self._trace_callback(self._trace_logger.entries[-1])

    self._page_crossed = False
    cycles = entry.handler(self)

    intr = self._check_nmi()
    if intr == 0:
        intr = self._check_irq()

    total = cycles + intr
    self.total_cycles += total
    return total
```

### AD-9f.4：Trace 兼容 — 保留 Python 日志器

Trace 日志器（`CpuTraceLogger`）和 `format_operand` 保留在 `cpu.py` 中不变。

`CPUCy` 提供与 `CPU` 完全一致的 trace API（含 callback）：

```cython
cpdef void set_trace_enabled(self, bint enabled):
    if self._trace_logger is None and enabled:
        from simplenes.cpu.cpu import CpuTraceLogger
        self._trace_logger = CpuTraceLogger()
    if self._trace_logger is not None:
        self._trace_logger.enabled = enabled

cpdef object get_trace_logger(self):
    return self._trace_logger

cpdef void set_trace_callback(self, callback):
    self._trace_callback = callback
    if callback is not None:
        self.set_trace_enabled(True)
```

`step_instruction` 中的 trace 捕获逻辑已在 AD-9f.3 完整列出，与 Python `CPU` 一致。

`CpuTraceLogger.capture()` 接收 `cpu` 参数并访问 `cpu.a`、`cpu.x` 等公共字段——`CPUCy` 的 `a/x/y/p/sp/total_cycles` 已声明为 `cdef public`，确保 `capture()` 正常工作。

### AD-9f.5：SIMPLENES_BACKEND 行为矩阵

| `SIMPLENES_BACKEND` | PPU | PPUBus | CPU | 行为 |
|---------------------|------|--------|-----|------|
| (unset) | auto | auto | auto | 各自独立 autodetect |
| `python` | pure | pure | pure | 全部强制纯 Python |
| `cython` | Cython | Cython | Cython | 三者都必须可用；任一缺失 → `ImportError` |

### AD-9f.6：CPU 后端选择（`cpu/__init__.py`）

```python
import os

from simplenes.cpu.cpu import CpuTraceEntry, CpuTraceLogger  # noqa: F401

_backend = os.environ.get("SIMPLENES_BACKEND", "")
if _backend == "python":
    from simplenes.cpu.cpu import CPU  # noqa: F401
elif _backend == "cython":
    from simplenes.cpu._cpu_cy import CPUCy as CPU  # noqa: F401
else:
    try:
        from simplenes.cpu._cpu_cy import CPUCy as CPU  # noqa: F401
    except ImportError:
        from simplenes.cpu.cpu import CPU  # noqa: F401

__all__ = ["CPU", "CpuTraceEntry", "CpuTraceLogger"]
```

## Import 路径（Phase 9f 最终状态）

| 导入路径 | 用途 | 解析为 |
|----------|------|--------|
| `from simplenes.cpu import CPU` | 生产代码 | Cython `CPUCy` or pure `CPU` |
| `from simplenes.cpu.cpu import CPU` | 测试 oracle | 纯 Python `CPU` |
| `from simplenes.bus import PPUBus` | 生产代码 | Cython `PPUBusCy` or pure `PPUBus` |
| `from simplenes.ppu import PPU` | 生产代码 | Cython `PPUCy` or pure `PPU` |

## Build Infrastructure

### scripts/build_cython.py

```python
extensions = [
    Extension("simplenes.ppu._ppu_cy",      ["src/simplenes/ppu/_ppu_cy.pyx"]),
    Extension("simplenes.bus._ppu_bus_cy",  ["src/simplenes/bus/_ppu_bus_cy.pyx"]),
    Extension("simplenes.cpu._cpu_cy",      ["src/simplenes/cpu/_cpu_cy.pyx"]),
]
```

## Implementation Plan

### Step 1: CPUCy 完整实现

1. 创建 `src/simplenes/cpu/_cpu_cy.pyx`：
   - `cdef class CPUCy`，含所有 `cdef public` 字段（a / x / y / sp / pc / p / total_cycles / halted / _page_crossed / _last_pc / _last_opcode）
   - `bus` / `interrupts` 为 `cdef public object`
   - `_bus_read` / `_bus_write` / `_opcodes` 缓存 callable 和 OPCODES 表
   - 所有 helper 方法 `cpdef`：
     - `_read(addr)` / `_write(addr, value)` / `_read_pc()`
     - `_read_word(addr)` / `_push(value)` / `_pull()` / `_push_word(value)` / `_pull_word()`
     - `_set_nz(value)` / `_add_with_carry(value)` / `_sub_with_carry(value)` / `_compare(reg, value)`
     - `_check_nmi()` → returns int cycles (0 or 7)
     - `_check_irq()` → returns int cycles (0 or 7)
   - `__init__(bus, interrupts)` — 初始状态与 `CPU.__init__` 逐字段一致：
    - `a = x = y = 0`、`sp = 0xFD`、`pc = 0`、`p = FLAG_U | FLAG_I`
    - `total_cycles = 0`、`halted = False`
    - `_page_crossed = False`、`_irq_prev = False`
    - `_last_pc = _last_opcode = 0`
    - `_trace_logger = _trace_callback = None`
  - `reset()` — 与 `CPU.reset()` 语义一致：
    - 重置所有寄存器（同上）
    - `total_cycles = 7`（而非 0）
    - `pc = self._read_word(self.VECTOR_RESET)`
    - **不** 重置 `_bus_read` / `_bus_write` / `_opcodes` / constants
  - `step_instruction()`
  - Trace API（`set_trace_enabled` / `get_trace_logger` / `set_trace_callback`）
2. 编译：`uv run python scripts/build_cython.py`。

### Step 2: `cpu/__init__.py` 后端选择

1. 创建/替换 `src/simplenes/cpu/__init__.py`，SIMPLENES_BACKEND 逻辑。
2. 运行 `SIMPLENES_BACKEND=python uv run pytest tests/ -q`。

### Step 3: `machine.py` import 更新

1. `machine.py`：`from simplenes.cpu.cpu import CPU` → `from simplenes.cpu import CPU`。
2. 运行 `SIMPLENES_BACKEND=python uv run pytest tests/ -q`。

### Step 4: CPUCy 冒烟测试

1. 新增 `tests/unit/test_cpu_cy_smoke.py`：
   ```python
   _cpu_cy = pytest.importorskip("simplenes.cpu._cpu_cy")
   CPUCy = _cpu_cy.CPUCy
   from simplenes.cpu.cpu import CPU as PureCPU
   ```
   测试项：
   - 基础指令（LDA / STA / JMP / NOP）`CPUCy` vs `PureCPU` 寄存器等价。
   - 分支指令（BEQ / BNE / BCC / BCS）PC 和 cycle 正确。
   - Stack push/pull（PHA / PLA / JSR / RTS）SP 和内存一致。
   - flag 计算（ADC / SBC / CMP / CPX / CPY）N/Z/C/V 标志正确。
   - 中断（NMI / IRQ）触发 → vector 读取 → 标志设置。
   - **nestest trace 对比（mandatory gate）：**
     - 若 `tests/roms/nestest.nes` + `nestest.log` 存在：对比前 100 条 trace。
     - 若 ROM 缺失：**必须** 执行 synthetic trace parity，使用项目内构造的小 ROM，至少覆盖：LDA imm/abs、STA zp/abs、JMP abs、branch taken/not-taken、PHA/PLA、ADC/SBC。对比 `CPUCy` vs `PureCPU` 前 200 条指令 trace 完全一致。
   - **禁止** 访问私有字段，只使用 public API。
2. 有 `.so`：测试实际跑 `CPUCy`；无 `.so`：`importorskip` 跳过。

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
   - 16.67–22ms → 进入 Phase 9g（Scheduler / Mapper / 残余热点）。
   - \> 22ms → 回溯 profiling。
4. Backend-missing smoke：
   ```bash
   # 未编译时：SIMPLENES_BACKEND=cython 必须 loudly fail
   SIMPLENES_BACKEND=cython uv run python -c "from simplenes.cpu import CPU" 2>&1 || echo "EXPECTED FAIL"
   # 编译后：必须成功
   uv run python scripts/build_cython.py && SIMPLENES_BACKEND=cython uv run python -c "from simplenes.cpu import CPU; print('OK')"
   ```

## Risks

| 风险 | 缓解 |
|------|------|
| `cpdef` 方法仍有 Python 调用开销（handler→CPUCy） | 预期仍可节省 3-5ms（方法体编译 + dispatch 编译） |
| Trace 日志器引用 `cpu.a` 等字段在 Cython class 上失败 | `cdef public int a` 确保 Python-accessible |
| Cython `cdef class` 上的 `__init__` 签名与 Python oracle 不兼容 | 统一参数列表 `bus, interrupts`，不引入额外 kwarg |
| `OPCODES[opcode].handler(self)` 中 `self` 类型不匹配 | `entry.handler` 接受 `CPU`，`CPUCy` 提供相同 duck-typed 接口 |
| nestest trace mismatch（寄存器 / cycles 偏移） | 冒烟测试含 nestest 前 100 条指令 trace 对比 |

## Non-Goals

- 不重写 opcode handlers。
- 不修改 CPUBus / PPUBus / PPU。
- 不修改 APU / Mapper。
- 不引入 cimport / typed dispatch（handler→CPUCy 调用仍是 Python dispatch）。

## Success Criteria

| 标准 | 指标 |
|------|------|
| **Hard** | `SIMPLENES_BACKEND=cython` 下 `test_bench_run_frame` mean ≤ **16.67ms/frame** |
| **Gate** | 所有测试在 Cython 和纯 Python 后端均通过 |
| **Gate** | CPUCy 冒烟测试通过（LDA/STA/JMP/branch/stack/interrupt） |
| **Gate** | CPUCy vs PureCPU trace parity：nestest（若 ROM 存在）或 synthetic（mandatory）|
| **Gate** | 纯 Python fallback 性能不退化 |
| **Gate** | `test_bench_run_frame` 不得慢于 Phase 9d baseline（~25.6ms）。若改善 < 1ms，标记 insufficient，停止该路线 |
| **Fallback** | 若不达 16.67ms，不 merge 为 "60 FPS complete"；根据 Phase Gate 进入 Phase 9g 或回溯 profiling |
