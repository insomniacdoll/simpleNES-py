# Phase 5: 精灵渲染、Controller、OAM DMA 详细实现设计

## Summary

Phase 4 已完成逐 dot 背景渲染管线。Phase 5 在此基础上添加：

- **精灵（sprite）渲染**：每 scanline 最多 8 个 sprite，含 sprite 0 hit 和 sprite overflow 检测
- **OAM 数据结构**：64 个 sprite 条目，secondary OAM（扫描线级评估）
- **Controller**：已完成 `input/controller.py` 实现，Phase 5 验证 CPUBus 接线并通过测试补充确认
- **OAM DMA**：从 stub 升级为 Scheduler 驱动的原子 DMA 执行

Phase 5 完成后，简单 NROM 游戏应可操作且 sprite 显示正确。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/ppu.py` | **扩展** — 添加 sprite 评估、sprite 渲染、sprite 0 hit/overflow、`_last_bg_pixel` |
| `src/simplenes/scheduler.py` | **扩展** — 添加 OAM DMA 执行逻辑 `_execute_dma()`；返回值包含 DMA cycles |
| `src/simplenes/dma/oam_dma.py` | **修改** — 简化 OAMDMAState |
| `src/simplenes/machine.py` | **修改** — Scheduler 构造函数新增 `oam_dma_state`、`cpu_bus` 参数 |
| `src/simplenes/input/controller.py` | 不变（已实现） |
| `src/simplenes/bus/cpu_bus.py` | 不变（已正确接线） |
| `tests/unit/test_ppu_sprites.py` | **新建** — sprite 评估、渲染、hit/overflow 单元测试 |
| `tests/unit/test_oam_dma.py` | **新建** — OAM DMA 执行测试 |

---

## Data Model Changes

### 1. PPU 新增 `__slots__`

```python
__slots__ = (
    # ... existing slots ...

    # --- Phase 5: sprite rendering ---
    "_secondary_oam",        # bytearray(32) — 8 sprites × 4 bytes
    "_sprite_count",         # int — number of sprites on current scanline
    "_sprite_zero_possible", # bool — sprite 0 was in secondary OAM
    "_last_bg_pixel",        # int (0-3) — raw bg pattern pixel from LAST dot output
)
```

- `_last_bg_pixel`：上一个 dot 的背景 raw pixel（0-3）。**用于 sprite priority 和 sprite 0 hit 的 opacity 判断**，取代 framebuffer palette index。
- **不引入 `_sprite_zero_rendered`**：status bit 6 自身 latch，无需额外字段防止重复 hit。

初始化：

```python
self._secondary_oam = bytearray(32)
self._sprite_count = 0
self._sprite_zero_possible = False
self._last_bg_pixel = 0
```

### 2. OAMDMAState 简化

```python
class OAMDMAState:
    __slots__ = ("active", "page")

    def __init__(self):
        self.active = False
        self.page = 0

    def trigger(self, value: int) -> None:
        self.active = True
        self.page = value & 0xFF

    def reset(self) -> None:
        self.active = False
        self.page = 0
```

### 3. Scheduler 新增 DMA 支持

新增 `__slots__` 和构造参数：

```python
class Scheduler:
    __slots__ = ("_cpu", "_ppu", "_apu", "_timing", "_oam_dma", "_cpu_bus")

    def __init__(self, cpu, ppu, apu, timing, oam_dma_state, cpu_bus):
        # ... existing ...
        self._oam_dma = oam_dma_state
        self._cpu_bus = cpu_bus
```

---

## Interface & API Design

### 1. Background Opacity Tracking（Phase 5 关键变更）

Phase 5 要求区分"背景像素透明"和"背景像素输出 palette[0]"。为此：

- `_output_background_pixel()` 在输出后设置 `self._last_bg_pixel`：若背景像素实际可见且非零，设为 `pixel`；否则设为 0。
- `_output_backdrop_pixel()` 设置 `self._last_bg_pixel = 0`

```python
def _output_background_pixel(self, fb_x: int) -> None:
    mux = 0x8000 >> self.fine_x
    pixel = ...  # raw 0-3
    attr = ...

    # Determine actual background opacity (accounts for left clipping)
    bg_left_on = fb_x >= 8 or (self.mask & 0x02)
    if not bg_left_on or pixel == 0:
        palette_idx = self.bus.read(0x3F00) & 0x3F
        self._last_bg_pixel = 0
    else:
        palette_idx = self.bus.read(0x3F00 | ((attr << 2) | pixel)) & 0x3F
        self._last_bg_pixel = pixel  # ← NEW

    self.framebuffer[self.scanline * 256 + fb_x] = palette_idx

def _output_backdrop_pixel(self, fb_x: int) -> None:
    self.framebuffer[self.scanline * 256 + fb_x] = (
        self.bus.read(0x3F00) & 0x3F
    )
    self._last_bg_pixel = 0  # ← NEW: backdrop is transparent
```

### 2. Sprite Evaluation（扫描线级，dot 257 触发）

在 `_tick_background()` 的 dot 257 处调用。

#### 2.1 评估逻辑

```
_evaluate_sprites():
    next_scanline = scanline + 1 if scanline < 261 else 0
    if next_scanline >= 240:
        _sprite_count = 0
        _sprite_zero_possible = False
        return

    _secondary_oam = bytearray(32)
    _sprite_count = 0
    _sprite_zero_possible = False

    height = 16 if (control & 0x20) else 8

    for n in range(64):
        sprite_y = oam[n * 4]
        # NES OAM Y: sprite top is at (sprite_y + 1)
        # visible when 0 <= (next_scanline - (sprite_y + 1)) < height
        row = next_scanline - (sprite_y + 1)
        if 0 <= row < height:
            if n == 0:
                _sprite_zero_possible = True
            if _sprite_count < 8:
                idx = _sprite_count * 4
                _secondary_oam[idx:idx+4] = oam[n*4:n*4+4]
                _sprite_count += 1
            else:
                status |= 0x20  # sprite overflow
                break
```

#### 2.2 时序

- dot 257：`_increment_y()` 之后、`_reload_horizontal()` 之前调用 `_evaluate_sprites()`
- 评估基于 **下一 scanline**

### 3. Sprite 渲染（per-dot，覆盖在 background 之上）

在 `clock()` 的 visible output 中，background 输出之后调用：

```
clock():
    ...
    if scanline <= 239 and 1 <= dot <= 256:
        if _rendering and _bg_enabled:
            _output_background_pixel(dot - 1)
        else:
            _output_backdrop_pixel(dot - 1)
        # Phase 5: overlay sprites
        if _rendering and (mask & 0x10):
            _composite_sprite_pixel(dot - 1)
    ...
```

#### 3.1 像素合成 `_composite_sprite_pixel(fb_x)`

```python
def _composite_sprite_pixel(self, fb_x: int) -> None:
    # Left 8-pixel sprite clipping
    if fb_x < 8 and not (self.mask & 0x04):
        return

    for n in range(self._sprite_count):
        entry = self._secondary_oam[n * 4 : n * 4 + 4]
        sprite_y = entry[0]
        tile_idx = entry[1]
        attr = entry[2]
        sprite_x = entry[3]

        offset = fb_x - sprite_x
        if offset < 0 or offset >= 8:
            continue

        # horizontal flip
        column = offset
        if attr & 0x40:
            column = 7 - offset

        pixel = self._fetch_sprite_pixel(
            self.scanline, sprite_y, tile_idx, attr, column
        )

        if pixel == 0:
            continue  # transparent → try next sprite

        # USE _last_bg_pixel, NOT framebuffer palette index!
        bg_opaque = self._last_bg_pixel != 0
        behind_bg = bool(attr & 0x20)

        # Sprite 0 hit — MUST be checked BEFORE priority return.
        # Sprite priority bit does NOT suppress sprite 0 hit.
        if n == 0 and self._sprite_zero_possible:
            if pixel != 0 and bg_opaque:
                if fb_x != 255:  # not rightmost column
                    left_ok = fb_x >= 8 or (
                        (self.mask & 0x02) and (self.mask & 0x04)
                    )
                    if left_ok:
                        self.status |= 0x40  # sprite 0 hit

        # Priority: sprite vs background
        if behind_bg and bg_opaque:
            # This sprite is selected but loses to BG.
            # Lower-priority sprites MUST NOT be considered.
            return

        # Palette index
        palette_base = 0x3F10
        palette_idx = self.bus.read(
            palette_base | ((attr & 3) << 2) | pixel
        ) & 0x3F
        self.framebuffer[self.scanline * 256 + fb_x] = palette_idx

        return  # found visible sprite pixel, done
```

**关键行为**：
- **Sprite 0 hit 必须在 priority return 之前检测**：sprite priority bit 不影响 sprite 0 hit。即使 sprite 标记为 behind background，只要非透明 sprite pixel 与 opaque background 重叠就可能触发 hit。
- `behind_bg and bg_opaque` 时 **`return`**，不是 `continue`：第一个非透明 sprite pixel 赢得 sprite 间 priority，即便它输给 BG 也不会让后续 sprite 覆盖。
- **不依赖 `_sprite_zero_rendered`**：status bit 6 自身 latch。sprite 0 hit 可在同 scanline 后续像素触发。
- `_last_bg_pixel` 判断 opacity，不使用 framebuffer palette index。

#### 3.2 Sprite 像素取指 `_fetch_sprite_pixel(scanline, sprite_y, tile_idx, attr, column)`

```python
def _fetch_sprite_pixel(self, scanline: int, sprite_y: int,
                        tile_idx: int, attr: int, column: int) -> int:
    """Fetch one sprite pixel (0-3).  column: 0-7 (left-to-right)."""
    height = 16 if (self.control & 0x20) else 8

    # NES OAM Y: sprite top at (sprite_y + 1)
    row = scanline - (sprite_y + 1)
    if row < 0 or row >= height:
        return 0

    # Vertical flip
    if attr & 0x80:
        row = (height - 1) - row

    if height == 16:
        # 8×16 mode: tile_idx bit 0 selects pattern table
        table = 0x1000 if (tile_idx & 1) else 0x0000
        if row < 8:
            tile = tile_idx & 0xFE   # top tile (even)
        else:
            tile = (tile_idx & 0xFE) | 1  # bottom tile (odd)
            row -= 8
    else:
        # 8×8 mode: PPUCTRL bit 3 selects pattern table
        table = 0x1000 if (self.control & 0x08) else 0x0000
        tile = tile_idx

    addr = table | (tile << 4) | row
    pt_lo = self.bus.read(addr)
    pt_hi = self.bus.read(addr | 8)

    # Extract pixel: bit 7 = leftmost column
    bit = 7 - column
    return ((pt_hi >> bit) & 1) << 1 | ((pt_lo >> bit) & 1)
```

**关键修正**：
- row 计算使用 `scanline - (sprite_y + 1)`（NES OAM Y 语义）
- 8×16 模式：`tile_idx & 1` 选 pattern table（**不用** `PPUCTRL bit 3`）
- 垂直翻转先对完整 16-px row 做 `row = 15 - row`，再拆 top/bottom

### 4. Sprite 0 Hit

完整条件：

1. Sprite 0 在 secondary OAM 中（`_sprite_zero_possible == True`）
2. Sprite 0 的非透明像素与背景非透明像素（`_last_bg_pixel != 0`）在同一 pixel 重叠
3. 该 pixel 不是 `x == 255`（最右列）
4. 若 `x < 8`：必须 `mask & 0x02`（bg left enable）**且** `mask & 0x04`（sprite left enable）都置位才可能触发
5. `mask & 0x08`（bg enabled）且 `mask & 0x10`（sprites enabled）

**status bit 6 是 self-latching**：一次置位后，后续 dot 不会清除（只在 PPUSTATUS read 和 pre-render dot 1 清除）。

**重要**：sprite 0 hit 不受 sprite priority bit（attr bit 5）抑制。behind-background 的 sprite 0 仍可在与 opaque background 重叠时触发 hit。

Phase 3/4 已有的清除行为不变。

### 5. Sprite Overflow

在 `_evaluate_sprites()` 中：count 达到 8 后遇到第 9+ 个候选 sprite → `status |= 0x20`。

### 6. Controller

`input/controller.py` 已实现完整。Phase 5 不做修改，验证 CPUBus 接线：

- `$4016` read → `controller1.read()`
- `$4017` read → `controller2.read()`
- `$4016` write strobe → both controllers

### 7. OAM DMA（原子方案）

#### 7.1 触发

- CPU 写 `$4014` → `CPUBus.write()` → `OAMDMAState.trigger(value)`
- DMA 在**当前指令完成后**由 Scheduler 原子执行

#### 7.2 Scheduler._execute_dma() → int

```python
def _execute_dma(self) -> int:
    """Execute full OAM DMA atomically. Returns DMA cycle count (513)."""
    dma = self._oam_dma
    dma.active = False
    page = dma.page

    # Step 1: dummy read cycle
    self._cpu_bus.read(page << 8)

    # Step 2: 256 reads + writes
    for addr in range(256):
        data = self._cpu_bus.read((page << 8) | addr)
        self._ppu.write_register(0x2004, data)

    # Step 3: tick PPU/APU for DMA duration
    DMA_CYCLES = 513
    for _ in range(DMA_CYCLES):
        for _ in range(self._timing.ppu_dots_per_cpu_cycle):
            self._ppu.clock()
        self._apu.clock_cpu_cycle()

    return DMA_CYCLES
```

#### 7.3 Scheduler.step_instruction() 集成（返回值包含 DMA cycles）

```python
def step_instruction(self) -> int:
    cycles = self._cpu.step_instruction()
    for _ in range(cycles):
        for _ in range(self._timing.ppu_dots_per_cpu_cycle):
            self._ppu.clock()
        self._apu.clock_cpu_cycle()

    # OAM DMA: atomic, after instruction completes
    dma_cycles = 0
    if self._oam_dma.active:
        dma_cycles = self._execute_dma()

    return cycles + dma_cycles  # ← 返回值包含 DMA stall cycles
```

**简化取舍**：513 vs 514 的奇偶差异对 NROM 游戏无影响。OAM 复制先于 PPU/APU 推进，与真实硬件交错行为不一致，标记为已知风险。

---

## Control Flow

```
clock()
  → _update_rendering_flags()
  → Visible output:
      _output_background_pixel / _output_backdrop_pixel (dots 1-256)
        → sets _last_bg_pixel
      _composite_sprite_pixel (dots 1-256, if sprites enabled)
        → uses _last_bg_pixel for priority/hit
  → if _rendering:
        _tick_background()
          → ... (shift/fetch/load)
          → dot 257: _evaluate_sprites()  ← NEW
  → odd frame skip
  → counter advance
  → VBlank handling
```

```
Scheduler.step_instruction() → int
  → CPU.step_instruction() → returns cycles
  → tick PPU/APU for `cycles`
  → if DMA active: dma_cycles = _execute_dma()
  → return cycles + dma_cycles
```

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| sprite count = 0 | `_composite_sprite_pixel` 无操作 |
| sprite pixel transparent | `continue` 检查下一个 sprite |
| sprite behind bg + bg opaque | `return`（不继续检查低优先级 sprite） |
| sprite 0 hit + left 8px | 需同时满足 `mask&0x02` 和 `mask&0x04` |
| sprite 0 hit + x == 255 | 不触发 |
| sprite 0 hit 在同 scanline 后续 pixel | 可触发（不依赖 `_sprite_zero_rendered`） |
| sprite Y = 0 | 从 scanline 1 开始显示（OAM Y = 顶部 - 1） |
| 8×16 mode | tile_idx bit 0 选表，bits 7-1 选 tile pair |
| vertical flip (attr bit 7) | 先 `row = (height-1) - row`，再拆 top/bottom |
| horizontal flip (attr bit 6) | column = 7 - offset |
| OAM DMA during VBlank | 正常执行，PPU OAM 被覆写 |
| DMA 原子 | 1 次执行完 513 cycles，不交错 CPU 指令 |
| sprites disabled (mask bit 4 == 0) | `_composite_sprite_pixel` 短路 |
| pre-render sprite evaluation | 评估用于 scanline 0 的 sprites |
| Scheduler DMA cycles 包含 | `step_instruction()` 返回 `cycles + 513` |

---

## Non-Goals (Phase 5)

- 精确 OAM 评估 cycle-level 行为
- Sprite overflow bug（第 9 个 sprite 的伪随机替换行为）
- OAMADDR 写入对正在进行的 sprite 评估的影响
- DMC DMA
- 多手柄支持（zapper 等）
- DMA 与 PPU/APU 交错推进（当前为原子 DMA 简化）

---

## Implementation Plan

### Step 1: 简化 OAMDMAState
- 移除 `address`、`data`、`dummy_cycle`、`read_phase`、`cycles_remaining`
- 只保留 `active` 和 `page`
- 更新 `trigger()` 和 `reset()`

### Step 2: Scheduler 添加 `_execute_dma()` → int
- 新增 `_oam_dma` 和 `_cpu_bus` 字段
- 实现 `_execute_dma()`：1 dummy + 256 R/W + 513 PPU/APU ticks → return 513
- `step_instruction()` 中检查 DMA，返回 `cycles + dma_cycles`

### Step 3: 更新 `NESMachine` 构造 Scheduler
- 传入 `oam_dma_state` 和 `cpu_bus`

### Step 4: PPU 添加 sprite 状态字段
- `__slots__` 新增 `_secondary_oam`、`_sprite_count`、`_sprite_zero_possible`、`_last_bg_pixel`
- `__init__` / `reset()` 初始化
- **不引入 `_sprite_zero_rendered`**

### Step 5: 修改背景输出：记录 `_last_bg_pixel`
- `_output_background_pixel()` 末尾设置 `self._last_bg_pixel = pixel`
- `_output_backdrop_pixel()` 设置 `self._last_bg_pixel = 0`

### Step 6: 实现 `_fetch_sprite_pixel()`
- 8×8 / 8×16 模式（8×16 用 `tile_idx & 1` 选表）
- row = `scanline - (sprite_y + 1)`
- 垂直翻转 + Pattern 字节读取 + 位提取

### Step 7: 实现 `_evaluate_sprites()`
- `row = next_scanline - (sprite_y + 1)`
- 在 `_tick_background()` 的 dot 257 处调用
- 遍历 64 sprites，填充 secondary OAM
- 处理 sprite overflow

### Step 8: 实现 `_composite_sprite_pixel()`
- 遍历 secondary OAM sprites
- 使用 `_last_bg_pixel` 判断 priority
- behind-bg + bg opaque → `return`（不是 `continue`）
- Sprite 0 hit：不依赖 `_sprite_zero_rendered`，同时检查 left clipping

### Step 9: 集成到 `clock()`
- `_rendering` 且 `mask & 0x10` 时调用 `_composite_sprite_pixel`
- `_evaluate_sprites()` 在 `_tick_background()` 的 dot 257 触发

### Step 10: 编写单元测试

**`tests/unit/test_ppu_sprites.py`**：

1. **`test_bg_pixel_opacity_tracking`** — `_last_bg_pixel` 记录实际 opacity（含左裁剪）
2. **`test_bg_left_clipping_sets_last_bg_pixel_zero`** — 左 8px bg clipping 时 `_last_bg_pixel=0`
3. **`test_sprite_behind_bg_visible_when_bg_left_clipped`** — bg 左裁剪时 behind-bg sprite 可见
4. **`test_sprite_y_off_by_one`** — sprite Y=0 从 scanline 1 开始显示
5. **`test_sprite_evaluation_8x8`** — 验证 sprite 评估 + secondary OAM 填充
4. **`test_sprite_evaluation_8x16`** — 8×16 模式评估
5. **`test_sprite_overflow`** — 超过 8 sprites 时 status bit 5 置位
6. **`test_sprite_pixel_fetch_8x8`** — 验证 `_fetch_sprite_pixel()` 返回正确 pixel
7. **`test_sprite_pixel_fetch_8x16`** — 8×16 模式 pixel 读取（验证 table 选择规则）
8. **`test_sprite_horizontal_flip`** — attr bit 6 翻转 column
9. **`test_sprite_vertical_flip`** — attr bit 7 翻转 row
10. **`test_sprite_composite`** — 验证 sprite pixel 覆写 framebuffer
11. **`test_sprite_behind_bg_blocks_lower_sprites`** — high-priority behind-bg sprite 遇到 opaque bg → return，低优先级 sprite 不显示
12. **`test_sprite_zero_hit`** — sprite 0 + opaque bg → status bit 6
13. **`test_sprite_zero_hit_left_clipping`** — left 8px 内需同时满足 bg/sprite left enable
14. **`test_sprite_zero_hit_x255`** — x==255 时不触发
15. **`test_sprite_zero_hit_later_pixel`** — sprite 0 hit 可在同 scanline 后续像素触发
16. **`test_sprite_zero_hit_when_sprite_behind_background`** — behind-bg sprite 0 仍能触发 hit
17. **`test_sprite_left_clipping`** — `mask & 0x04 == 0` 时左 8 像素无 sprite

**`tests/unit/test_oam_dma.py`**：

1. **`test_dma_copies_oam`** — DMA 将 256 字节复制到 PPU OAM
2. **`test_dma_ticks_ppu`** — DMA 执行期间 PPU clock 推进
3. **`test_dma_deactivates`** — DMA 完成后 `active == False`
4. **`test_dma_dummy_read`** — 第一个周期是 dummy read
5. **`test_dma_cycles_in_return`** — DMA 后 `step_instruction()` 返回 cycles + 513

### Step 11: 运行全量回归

```bash
uv run ruff check src/ tests/
uv run pytest tests/ -q
```

---

## Risks / Open Questions

### R-1: Sprite evaluation 精确时序

真实 PPU 在 dots 257-320 逐 cycle 评估 OAM。Phase 5 使用 scanline 级一次性评估。

**缓解**：NROM 游戏不依赖 cycle-accurate OAM 评估。后续按需精确化。

### R-2: Sprite 0 hit 边界条件

NES sprite 0 hit 边界条件较复杂。Phase 5 实现标准条件：left 8px（需同时满足 bg/sprite enable）、x==255 排除。

**缓解**：常见 NROM 游戏（Donkey Kong）不使用 sprite 0 hit。

### R-3: DMA 原子执行 vs 交错执行

当前设计 DMA 原子执行：先复制 OAM，后 tick PPU/APU。真实硬件交错推进。

**缓解**：NROM 游戏通常不依赖 DMA/PPU 交错行为。此简化明确列为 non-goal。

### R-4: 8×16 sprite 顶部/底部 tile 选择

8×16 模式下 `tile_idx & 1` 选表已在设计中修正。需在测试中验证 Donkey Kong（使用 8×16 sprites）的 Mario sprite 正确渲染。

**缓解**：测试覆盖 8×16 模式 pixel 取指。

---

## Verification Criteria

1. 新增测试全部通过（test_ppu_sprites.py 16 + test_oam_dma.py 5）
2. 现有 162 个测试继续通过
3. ruff 零警告
4. NROM 游戏 sprite 可见：Donkey Kong 的 Mario/barrel sprites 正常渲染
5. Controller 输入可响应：headless 脚本验证按键读写
6. OAM DMA 正确复制 256 字节 + Scheduler 返回值包含 DMA cycles
