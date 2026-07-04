# Phase 4: 背景渲染详细实现设计

## Summary

本文档产出 PPU 背景渲染管线的实现级设计。Phase 3 已完成全部 8 个 PPU 寄存器的精确读写行为，Phase 4 在现有 dot-level `clock()` 基础上添加逐 dot 背景渲染管线，包括 tile 取指、移位寄存器、v/t 寄存器渲染过程中的更新、pre-render scanline 行为、odd frame skip，以及 framebuffer 输出。

Phase 4 不进入 sprite 渲染（Phase 5），但需预留 sprite 相关的渲染使能逻辑（即 `_rendering` 标志由 `mask & 0x18 != 0` 决定）。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/ppu.py` | **扩展** — 添加背景渲染状态、per-dot 管线、framebuffer 输出 |
| `tests/unit/test_ppu_background.py` | **新建** — 背景渲染单元测试（tile 读取、移位寄存器、palette index 输出） |
| `tests/unit/test_ppu_registers.py` | 不变 |
| `tests/unit/test_ppu_bus.py` | 不变 |
| `src/simplenes/bus/ppu_bus.py` | 不变 |

---

## Dot Convention（重要）

本文档中的所有 dot 编号遵循：

- `clock()` **先执行本 dot 的渲染工作，再递增 dot**。
- 即渲染逻辑被调用时，`self.dot` 是当前 dot 的值（0-340）。
- counter 更新在渲染工作之后。

---

## Data Model Changes

### 新增 `__slots__`

在现有 `ppu.py` 的 `__slots__` 中新增背景渲染管线状态：

```python
__slots__ = (
    # ... existing slots ...

    # --- NEW: background rendering pipeline ---
    "_bg_shift_lo",      # 16-bit 移位寄存器 — pattern bit plane 0
    "_bg_shift_hi",      # 16-bit 移位寄存器 — pattern bit plane 1
    "_bg_attr_lo",       # 16-bit 移位寄存器 — attribute bit 0
    "_bg_attr_hi",       # 16-bit 移位寄存器 — attribute bit 1

    # Tile fetch latches (2-tile pipeline)
    "_nt_latch",         # 当前正在取指的 nametable byte
    "_at_latch",         # 当前正在取指的 attribute byte
    "_pt_lo_latch",      # pattern table low byte
    "_pt_hi_latch",      # pattern table high byte

    # Rendering control
    "_rendering",        # bool — mask & 0x18 != 0 (bg 或 sprite 使能)
    "_bg_enabled",       # bool — mask & 0x08 != 0
)
```

### 初始化

`__init__` 和 `reset()` 中初始化这些字段：

```python
self._bg_shift_lo = 0
self._bg_shift_hi = 0
self._bg_attr_lo = 0
self._bg_attr_hi = 0
self._nt_latch = 0
self._at_latch = 0
self._pt_lo_latch = 0
self._pt_hi_latch = 0
self._rendering = False
self._bg_enabled = False
```

---

## Interface & API Design

### 1. `PPU.clock()` — 扩展 per-dot 行为

`clock()` 是核心入口。Phase 4 扩展为：

```
clock():
    # Step 0: update rendering flags
    _update_rendering_flags()

    # Step 1: visible framebuffer output
    # Always outputs even when rendering is disabled — prevents stale pixels.
    if scanline <= 239 and 1 <= dot <= 256:
        if _rendering and _bg_enabled:
            _output_background_pixel(dot - 1)
        else:
            _output_backdrop_pixel(dot - 1)

    # Step 2: background pipeline — shift + fetch + v/t scroll updates.
    # Only active when rendering is enabled.
    if _rendering:
        if scanline <= 239 or scanline == 261:
            _tick_background()

    # Step 3: sprite evaluation (Phase 5, no-op for now)
    # if _rendering and scanline in [...]:
    #     _tick_sprites()

    # Step 4: odd frame skip (jump from pre-render dot 339 straight to visible dot 0)
    if scanline == 261 and dot == 339 and odd_frame and _rendering:
        dot = 0
        scanline = 0
        frame += 1
        odd_frame = not odd_frame
        return   # skip the rest of pre-render

    # Step 5: advance dot/scanline/frame counters
    dot += 1
    if dot >= 341:
        dot = 0
        scanline += 1
        if scanline >= 262:
            scanline = 0
            frame += 1
            odd_frame = not odd_frame

    # Step 6: VBlank boundary (existing, unchanged from Phase 3)
    if scanline == 241 and dot == 1:
        status |= 0x80
        _update_nmi()
    elif scanline == 261 and dot == 1:
        status &= 0x1F  # clear VBlank, sprite 0, sprite overflow
        _update_nmi()
```

**重要说明**：
- VBlank flag set / NMI 生成与渲染使能无关，保持 Phase 3 行为。
- **visible output（Step 1）与背景管线（Step 2）是解耦的**：渲染关闭时 visible dots 输出背景色但不执行 shift/fetch/v update；渲染开启时 normal pixel + full pipeline。
- 不引入 `_suppress_vbl` 字段。

---

### 2. Background Pipeline: `_tick_background()`

仅处理 shift + fetch + scroll 更新。**不包含 pixel output**（output 已在 `clock()` Step 1 完成）。

#### 2.1 总体逻辑

```
_tick_background():
    active_dots = (1 <= dot <= 256) or (321 <= dot <= 336)

    # --- SHIFT (active dots) ---
    if active_dots:
        _shift_registers()           # left shift by 1

    # --- FETCH / LOAD (active dots) ---
    if active_dots:
        _fetch_and_shift()           # memory reads + shifter load per phase

    # --- END-OF-SCANLINE UPDATES ---
    if dot == 256:
        _increment_y()
    if dot == 257:
        _reload_horizontal()

    # --- PRE-RENDER VERTICAL RELOAD ---
    if scanline == 261 and 280 <= dot <= 304:
        _reload_vertical()
```

#### 2.2 移位寄存器 `_shift_registers()`

每个 active dot **左移一位**（向 MSB 方向），并 mask 到 16-bit：

```python
def _shift_registers(self) -> None:
    self._bg_shift_lo = (self._bg_shift_lo << 1) & 0xFFFF
    self._bg_shift_hi = (self._bg_shift_hi << 1) & 0xFFFF
    self._bg_attr_lo = (self._bg_attr_lo << 1) & 0xFFFF
    self._bg_attr_hi = (self._bg_attr_hi << 1) & 0xFFFF
```

#### 2.3 Tile 取指 `_fetch_and_shift()`

Tile 取指在 8-dot 周期内完成 4 次内存读取。phase 由 `dot & 7` 决定：

```
_fetch_and_shift():
    phase = dot & 7   # 0-7

    if phase == 0:
        # Load previously fetched data into shifter low byte
        _load_shift_registers()
        # Increment coarse X
        _increment_x()

    elif phase == 1:
        # Fetch nametable byte for NEXT tile
        _nt_latch = bus.read(_nt_address())

    elif phase == 3:
        # Fetch attribute byte
        _at_latch = bus.read(_at_address())

    elif phase == 5:
        # Fetch pattern table low byte
        _pt_lo_latch = bus.read(_pt_lo_address())

    elif phase == 7:
        # Fetch pattern table high byte
        _pt_hi_latch = bus.read(_pt_hi_address())

    # phases 2, 4, 6: idle
```

**Pipeline 时序**：phase 0 先将当前 latches 中的数据加载进移位寄存器低 8 位并递增 coarse_x，然后 phase 1-7 取指下一个 tile 的数据到 latches。移位寄存器在每个 dot 左移 1 位，数据经过 8 次左移后从低 8 位移到高 8 位——此时通过 `fine_x` 选通的像素恰好可见。

#### 2.4 地址计算

```python
def _nt_address(self) -> int:
    """Nametable address: $2000 | (v & 0x0FFF)."""
    return 0x2000 | (self.v & 0x0FFF)

def _at_address(self) -> int:
    """Attribute table address within current nametable.
    $23C0 | (v & 0x0C00) | ((v >> 4) & 0x38) | ((v >> 2) & 0x07)
    """
    return 0x23C0 | (self.v & 0x0C00) | ((self.v >> 4) & 0x38) | ((self.v >> 2) & 0x07)

def _pt_lo_address(self) -> int:
    """Pattern table low byte.
    base = $1000 if control bit 4 else $0000
    fine_y extracted inline from v bits 14-12
    """
    base = 0x1000 if (self.control & 0x10) else 0x0000
    fine_y = (self.v >> 12) & 0x07
    return base | (self._nt_latch << 4) | fine_y

def _pt_hi_address(self) -> int:
    """Pattern table high byte = low + 8."""
    return self._pt_lo_address() | 8
```

#### 2.5 加载移位寄存器 `_load_shift_registers()`

将 latches 中的数据加载到移位寄存器的 **低 8 位**（bits 7-0），保留高 8 位不变。`coarse_x` / `coarse_y` 使用 inline bit 提取：

```python
def _load_shift_registers(self) -> None:
    # Pattern data → lower 8 bits
    self._bg_shift_lo = (self._bg_shift_lo & 0xFF00) | self._pt_lo_latch
    self._bg_shift_hi = (self._bg_shift_hi & 0xFF00) | self._pt_hi_latch

    # Attribute: determine which 2-bit palette select applies to this 16×16 area
    coarse_x = self.v & 0x1F           # bits 4-0
    coarse_y = (self.v >> 5) & 0x1F    # bits 9-5
    shift = (coarse_x & 2) | ((coarse_y & 2) << 1)
    pal = (self._at_latch >> shift) & 3

    attr_lo = 0xFF if (pal & 1) else 0x00
    attr_hi = 0xFF if (pal & 2) else 0x00
    self._bg_attr_lo = (self._bg_attr_lo & 0xFF00) | attr_lo
    self._bg_attr_hi = (self._bg_attr_hi & 0xFF00) | attr_hi
```

#### 2.6 像素输出 `_output_background_pixel(fb_x: int)`

使用 `fine_x` 通过 `mux` 选通移位寄存器中的像素位：

```python
def _output_background_pixel(self, fb_x: int) -> None:
    """Output one background pixel to framebuffer at (fb_x, scanline)."""
    mux = 0x8000 >> self.fine_x

    pixel = (
        ((1 if (self._bg_shift_hi & mux) else 0) << 1)
        | (1 if (self._bg_shift_lo & mux) else 0)
    )
    attr = (
        ((1 if (self._bg_attr_hi & mux) else 0) << 1)
        | (1 if (self._bg_attr_lo & mux) else 0)
    )

    if fb_x < 8 and not (self.mask & 0x02):
        # Left 8-pixel clipping: use backdrop color
        palette_idx = self.bus.read(0x3F00) & 0x3F
    elif pixel == 0:
        # Transparent pixel → universal background color
        palette_idx = self.bus.read(0x3F00) & 0x3F
    else:
        # Palette index = attr * 4 + pixel
        palette_idx = self.bus.read(0x3F00 | ((attr << 2) | pixel)) & 0x3F

    offset = self.scanline * 256 + fb_x
    self.framebuffer[offset] = palette_idx

def _output_backdrop_pixel(self, fb_x: int) -> None:
    """Output backdrop color — used when bg disabled or rendering off."""
    offset = self.scanline * 256 + fb_x
    self.framebuffer[offset] = self.bus.read(0x3F00) & 0x3F
```

---

### 3. v/t 渲染期间更新

#### 3.1 粗 X 递增 `_increment_x()`

在 tile 取指 phase 0（即每个 tile 完成，准备下一个 tile 时）调用：

```python
def _increment_x(self) -> None:
    if (self.v & 0x001F) == 31:
        # coarse_x wraps: 31 → 0, toggle horizontal nametable
        self.v &= ~0x001F              # coarse_x = 0
        self.v ^= 0x0400               # toggle horizontal nt bit
    else:
        self.v += 1                    # coarse_x++
```

#### 3.2 Y 递增 `_increment_y()`

在 dot 256 调用（每个可见 scanline 结束时）：

```python
def _increment_y(self) -> None:
    fine_y = (self.v >> 12) & 7
    if fine_y < 7:
        self.v += 0x1000               # fine_y++
    else:
        self.v &= ~0x7000              # fine_y = 0
        coarse_y = (self.v >> 5) & 0x1F
        if coarse_y == 29:
            self.v &= ~(0x1F << 5)     # coarse_y = 0
            self.v ^= 0x0800           # toggle vertical nt bit
        elif coarse_y == 31:
            self.v &= ~(0x1F << 5)     # coarse_y = 0, no nt toggle
        else:
            self.v += 0x0020           # coarse_y++
```

#### 3.3 水平重载 `_reload_horizontal()`

在 dot 257 调用：

```python
def _reload_horizontal(self) -> None:
    """Copy coarse_x (bits 4-0) + horizontal nametable (bit 10) from t to v."""
    self.v = (self.v & ~0x041F) | (self.t & 0x041F)
```

#### 3.4 Pre-render 垂直重载 `_reload_vertical()`

在 pre-render scanline（261）的 dots 280-304 区间每 dot 调用：

```python
def _reload_vertical(self) -> None:
    """Copy fine_y (bits 14-12) + vertical nametable (bit 11) + coarse_y (bits 9-5) from t to v."""
    self.v = (self.v & ~0x7BE0) | (self.t & 0x7BE0)
```

---

### 4. PPUMASK 渲染控制

```python
def _update_rendering_flags(self) -> None:
    self._rendering = (self.mask & 0x18) != 0   # bg (bit 3) or sprites (bit 4)
    self._bg_enabled = (self.mask & 0x08) != 0
```

在 `clock()` 入口和 `write_register(0x2001)` 路径中调用。

**关键行为**：
- `_rendering == False`：visible dots 输出背景色；`_tick_background()` 不调用 → 不取指、不移位、v 不递增
- `_rendering == True, _bg_enabled == False`：取指 + 移位 + v 更新正常执行；visible dots 输出背景色
- `_rendering == True, _bg_enabled == True`：完整的 normal pixel 输出 + 管线
- Phase 4 假设 `_rendering` 在帧开始时稳定

---

### 5. Odd Frame Skip

NTSC NES 在奇数帧时，pre-render scanline 只有 340 个 dot（跳过最后一个 dot 340）。

```python
# In clock(), AFTER _tick_background() for dot 339:
if self.scanline == 261 and self.dot == 339 and self.odd_frame and self._rendering:
    self.dot = 0
    self.scanline = 0
    self.frame += 1
    self.odd_frame = not self.odd_frame
    return  # skip counter increment + VBlank handling for this "dot"
```

**效果**：
- 正常帧：pre-render 有 341 dots (0-340)
- 奇数帧 + 渲染：pre-render 只有 340 dots (0-339)，dot 340 被跳过，直接跳转到 visible scanline 0 dot 0

---

### 6. Framebuffer

不变：`bytearray(256 * 240)`，每像素 palette index（0-63）。

新增规则：
- **渲染关闭时**（`_rendering == False`）：visible dots 输出背景色，防止 framebuffer 残留上一帧像素
- **背景关闭时**（`_bg_enabled == False`）：visible dots 输出背景色
- 左 8px 裁剪 + 透明像素：输出背景色
- 非零像素：`palette[(attr << 2) | pixel]`
- **Grayscale / Color emphasis：Phase 4 non-goal**

---

## Control Flow

```
clock()
  → _update_rendering_flags()
  → Visible output:  _output_background_pixel / _output_backdrop_pixel (always, dots 1-256)
  → if _rendering:
        _tick_background()
          → SHIFT:   _shift_registers() (active dots)
          → FETCH:   _fetch_and_shift() (active dots)
          → dot 256: _increment_y()
          → dot 257: _reload_horizontal()
          → pre-render dots 280-304: _reload_vertical()
  → odd frame skip (pre-render dot 339)
  → dot += 1 (with wrap to scanline/frame)
  → VBlank handling
```

---

## Edge Cases

| 场景 | 行为 | 处理方式 |
|------|------|----------|
| `mask` 全部关闭（`_rendering=False`） | visible dots 输出背景色；不取指、不移位、v 不递增 | `clock()` Step 1 写 backdrop；Step 2 短路 |
| bg disabled（`_bg_enabled=False`）| 执行 shift + fetch + v update；visible dots 输出背景色 | `clock()` Step 1 走 `else` 分支 |
| bg disabled + sprites enabled | 管线活跃（`_rendering=True`），v/t 正常更新 | `_rendering` 检查 `mask & 0x18` |
| bg + sprites both enabled | 完整 normal 输出 + 管线 | 所有路径活跃 |
| 左 8 像素裁剪 | `fb_x < 8` 且 `mask bit 1 == 0` → 背景色 | `_output_background_pixel()` 检查 |
| coarse_x 溢出 (31→0) | 翻转水平 nametable bit | `_increment_x()` 处理 |
| coarse_y 溢出 (29→0) | 翻转垂直 nametable bit | `_increment_y()` 处理 |
| coarse_y == 31 | 归零但不翻转 nt | `_increment_y()` 处理 |
| pre-render dots 280-304 | 每 dot 将 t 垂直分量复制到 v | `_reload_vertical()` 每 dot 调用 |
| odd frame skip | pre-render dot 339 → 直接跳 visible dot 0 | `clock()` 中 return |
| palette mirror | PPUBus 已处理 `$3F10/$3F14/$3F18/$3F1C` | 无需额外处理 |
| VBlank + 渲染 | VBlank flag 照常 set/clear | 与 Phase 3 行为一致 |
| pre-render 不输出像素 | 只取指 + 移位 + 重载 | `_output_background_pixel` 仅在 `scanline <= 239` 调用 |
| PPUSCROLL mid-frame | 写入 t，dot 257/280-304 时重载到 v | `_write_ppuscroll` 不改动 |
| 渲染关闭 → 重新开启 | framebuffer 保留背景色（非 stale 像素） | `clock()` Step 1 始终执行输出 |
| dots 337-340（dummy NT fetches） | Phase 4 阶段 no-op，留待后续精确化 | `active_dots` 范围不包含 337-340 |

---

## Non-Goals (Phase 4)

- Grayscale (`mask & 0x01`) 处理和 color emphasis (`mask & 0xE0`)：延迟到 frontend 或后续 Phase
- Sprite rendering / sprite 0 hit / sprite overflow
- Mid-scanline scroll split（如 SMB status bar）
- Dot-accurate VBlank NMI suppression race
- PAL / Dendy timing
- Dots 337-340 dummy nametable fetch（暂 no-op）

---

## Implementation Plan

### Step 1: 添加渲染状态字段
- 在 `__slots__` 中添加 `_bg_shift_lo/hi`、`_bg_attr_lo/hi`、`_nt_latch`、`_at_latch`、`_pt_lo_latch`、`_pt_hi_latch`、`_rendering`、`_bg_enabled`
- 在 `__init__` 和 `reset()` 中初始化

### Step 2: 实现渲染使能逻辑
- 添加 `_update_rendering_flags()`
- 在 `clock()` 入口和 `write_register(0x2001)` 路径中调用

### Step 3: 实现 `_output_backdrop_pixel()`
- 将 palette[0] 写入 framebuffer 指定位置
- **在 `clock()` Step 1 中整合调用**：始终在 visible dots 1-256 输出

### Step 4: 实现移位寄存器
- `_shift_registers()`：**左移 1 位 & 0xFFFF**

### Step 5: 实现 coarse_x 递增
- `_increment_x()`：coarse_x + 1，处理 wrap + 水平 nt 翻转

### Step 6: 实现 Y 递增
- `_increment_y()`：fine_y + 1 → coarse_y wrap → 垂直 nt 翻转

### Step 7: 实现水平/垂直重载
- `_reload_horizontal()`：t bits 4-0 + bit 10 → v
- `_reload_vertical()`：t bits 14-12 + bit 11 + bits 9-5 → v

### Step 8: 实现地址计算辅助方法
- `_nt_address()`、`_at_address()`、`_pt_lo_address()`（inline `fine_y = (v >> 12) & 7`）、`_pt_hi_address()`

### Step 9: 实现 tile 取指管线
- `_load_shift_registers()`：latches → shifter **低 8 位**；coarse_x/coarse_y **inline bit 提取**
- `_fetch_and_shift()`：根据 `dot & 7` 执行取指 → load

### Step 10: 实现像素输出
- `_output_background_pixel(fb_x)`：**使用 `mux = 0x8000 >> fine_x` 选通像素**，处理左裁剪、背景色、palette index

### Step 11: 实现 `_tick_background()`
- shift → fetch → end-of-scanline updates（**不含 pixel output**）
- pre-render 只执行管线，不输出像素

### Step 12: 实现 odd frame skip
- 在 `clock()` 中，`_tick_background()` 之后、dot 递增之前：pre-render dot 339 + odd frame → 跳转 visible dot 0

### Step 13: 整合到 `clock()`
- Step 1: visible output（始终执行）
- Step 2: `_tick_background()`（`_rendering=True` 时）
- Step 4-5: odd frame skip → counter advance
- Step 6: VBlank handling
- **不引入 `_suppress_vbl`**

### Step 14: 编写单元测试

新建 `tests/unit/test_ppu_background.py`：

1. **`test_rendering_disabled_outputs_backdrop`** — `mask & 0x18 == 0` 时 visible dots 写入背景色，v 不递增
2. **`test_rendering_disabled_no_v_increment`** — `_rendering=False` 时 v 寄存器不变化
3. **`test_bg_disabled_outputs_backdrop`** — `mask & 0x08 == 0` 时像素输出背景色（但 v 正常更新）
4. **`test_coarse_x_increment`** — 验证 `_increment_x()` 正确递增
5. **`test_coarse_x_wrap`** — coarse_x 31 → 0，水平 nametable bit 翻转
6. **`test_increment_y`** — 验证 `_increment_y()`：fine_y 递增 + coarse_y wrap
7. **`test_horizontal_reload`** — dot 257 时 v 的水平分量从 t 重载
8. **`test_vertical_reload`** — pre-render dots 280-304 时 v 的垂直分量从 t 重载
9. **`test_tile_fetch_addresses`** — 验证 NT/AT/PT 地址计算（fine_y inline 提取）
10. **`test_shifter_load_and_shift`** — 加载数据到低 8 位 → 左移 → 验证数据位移
11. **`test_pixel_output_with_fine_x`** — 预设 shifter 值 + `fine_x`，验证 `mux` 选通正确
12. **`test_left_clipping`** — `mask & 0x02 == 0` 时左 8 像素输出背景色
13. **`test_odd_frame_skip`** — 奇数帧 pre-render 在 dot 339 跳转

### Step 15: 运行全量回归

```bash
uv run ruff check src/ tests/
uv run pytest tests/ -q
```

---

## Risks / Open Questions

### R-1: 逐 dot 取指相位精确性

取指相位 `dot & 7` 和 pre-render prefetch 时序可能需微调。NROM 游戏通常不依赖逐 dot 精确行为。

**缓解**：对已知 working emulator（Mesen、Nestopia）做对照验证。precise timing 留待 `blargg_ppu_tests` 阶段。

### R-2: fine_x + shifter 建模正确性

`mux = 0x8000 >> fine_x` + 左移 + 低 8 位 load 的模型对 pipeline 深度的隐含假设（数据需经过 ≥8 次左移才可见）。

**缓解**：与 pre-render prefetch 一起验证。先构造已知 tile 的 nametable fixture，验证第一行像素与预期 tile pattern 一致。

### R-3: 动态 render toggle（mid-frame mask change）

游戏（Battletoads 等）在渲染期间开关 PPUMASK。Phase 4 假设 `_rendering` 帧内稳定。

**缓解**：Non-goal。后续按需补充。

### R-4: Mid-scanline PPUSCROLL/PPUADDR 写入

分屏滚动在 HBlank 期间写入 PPUSCROLL。dot 257 重载水平滚动意味着 mid-scanline 写入仅在下一 scanline 生效。

**缓解**：Phase 4 目标为 NROM 无分屏游戏。SMB 兼容性需后续验证。

### R-5: Sprite 0 hit / sprite overflow

Phase 4 不实现 sprite。`status` bits 6/5 不会被设置，影响依赖 sprite 0 hit 的游戏。

**缓解**：Phase 5 补充。

### R-6: 性能

逐 dot PPUBus.read() 约 245K 次/帧。纯 Python `bytearray` + `if/elif` 应在每帧数 ms 内完成。

**缓解**：正确性优先，性能后续优化。

---

## Verification Criteria

1. **单元测试 13 项全部通过** — `test_ppu_background.py`
2. **ruff 零警告** — `uv run ruff check src/ tests/`
3. **全量回归通过** — 现有 146 个测试继续通过
4. **NROM 游戏视觉验证** — Donkey Kong / Ice Climber framebuffer 非全零 + tile 结构可见
5. **framebuffer hash 可复现** — 固定 ROM + 固定状态 → 稳定 hash
6. **渲染关闭 backdrop 验证** — `mask & 0x18 == 0` 时 framebuffer 全为背景色，无残留像素
