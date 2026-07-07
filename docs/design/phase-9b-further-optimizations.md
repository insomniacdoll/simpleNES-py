# Phase 9b：性能优化补充设计

## Summary

Phase 9 优化后 `run_frame()` 约 40ms（~25 FPS），未达到 60 FPS 目标。本文档补充更多低风险 Python 层热路径优化。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/ppu.py` | **修改** — pre-allocate `_secondary_oam`；`_sprite_count==0` early exit |
| `src/simplenes/cpu/cpu.py` | **修改** — skip operand tuple when trace disabled |
| `src/simplenes/` | 不变 — bus/mapper/apu/scheduler/opcodes 无进一步优化 |

---

## Architecture Decisions

### AD-9b.1：Pre-allocate `_secondary_oam` 避免每行分配

当前 `_evaluate_sprites()` 中每可见 scanline 分配一个新 `bytearray(32)`（240 次/frame）：

```python
self._secondary_oam = bytearray(32)
```

`__init__` 和 `reset()` 已预分配 `self._secondary_oam = bytearray(32)`，实现时只需**删除 `_evaluate_sprites()` 内的热路径分配**，其余逻辑不变：现有 `for n in range(64)` 循环已通过 `_sprite_count` 控制写出范围。`reset()` 保留当前行为即可，不在 frame 热路径，无性能影响。

后续只读 `_secondary_oam` 且通过 `_sprite_count` 控制遍历边界，旧数据无需清零。

### AD-9b.2：跳过 trace-only 操作数序列化

`CPU.step_instruction()` 中无论 trace 是否启用，始终执行：

```python
operand_bytes = tuple(
    self._read((pc_before + i) & 0xFFFF)
    for i in range(1, entry.length)
)
```

每次指令创建一个 `tuple` + `range` iterator + N 次 `bus.read()`。trace 关闭时完全不需要。

改为条件执行，**始终保留 `_last_pc/_last_opcode`**（illegal opcode 报错需用）：

```python
self._last_pc = pc_before
self._last_opcode = opcode

if self._trace_logger and self._trace_logger.enabled:
    operand_bytes = tuple(
        self._read((pc_before + i) & 0xFFFF)
        for i in range(1, entry.length)
    )
    self._trace_logger.capture(
        self, pc_before, opcode, operand_bytes, entry
    )
    if self._trace_callback is not None:
        self._trace_callback(self._trace_logger.entries[-1])
```

### AD-9b.3：`_composite_sprite_pixel` 早退优化

当前即使 `_sprite_count == 0` 也会调用 `_composite_sprite_pixel`。在 `clock()` 中增加早退：

```python
if self._rendering and (self.mask & 0x10) and self._sprite_count:
    self._composite_sprite_pixel(self.dot - 1)
```

无 sprite 的 scanline 跳过函数调用 + 内部循环开销。

---

## Data Model Changes

无新增 slot。`_secondary_oam` 已在 `__slots__` 声明，`__init__` 已有初始化，无需变更。

---

## Implementation Plan

### Step 1: Pre-allocate `_secondary_oam`

1. 删除 `_evaluate_sprites()` 内的 `self._secondary_oam = bytearray(32)` 这一行。
2. `__init__` 和 `reset()` 已有 `self._secondary_oam = bytearray(32)` 初始化，无需额外修改。
3. 运行 `uv run pytest tests/ -q`

### Step 2: Skip operand serialization when trace off

1. `cpu.py` 中 conditionally read operand bytes，确保 `_last_pc/_last_opcode` 始终设置。
2. 验证 trace enabled 时仍生成完整 operand bytes 和 trace 输出。
3. 验证 illegal opcode 报错时仍使用正确的 `_last_pc` / `_last_opcode`。
4. 运行 `uv run pytest tests/ -q`

### Step 3: Clock sprite count early exit

1. `clock()` 中 `_composite_sprite_pixel` 调用增加 `self._sprite_count` 条件。
2. 运行 `uv run pytest tests/ -q`

### Step 4: 验收

1. 运行全部测试：
   ```bash
   uv run ruff check src/ tests/
   uv run pytest tests/ -q
   ```
2. 运行 benchmark 对比：
   ```bash
   uv run pytest benchmarks/ --benchmark-only -q
   ```

---

## Non-Goals

- 不做 sprite pattern row cache（跨 mapper bank 有正确性风险）
- 不修改 opcodes.py
- 不修改 bus/mapper/apu/scheduler
