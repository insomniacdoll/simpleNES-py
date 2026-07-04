# Phase 3: PPU 寄存器详细实现设计

## Summary

本文档基于 `docs/architecture.md` Phase 3 目标，产出 PPU 寄存器层的实现级设计。Phase 0-2 已完成项目骨架、iNES/NROM、完整 6502 CPU，Phase 3 将 PPU 从 stub 升级为完整的寄存器行为实现，包括所有 8 个 PPU 寄存器的精确读写语义、内部 VRAM 地址寄存器、NMI 生成逻辑。

**设计范围：**
- 8 个 PPU 寄存器：PPUCTRL / PPUMASK / PPUSTATUS / OAMADDR / OAMDATA / PPUSCROLL / PPUADDR / PPUDATA
- 内部 VRAM 地址寄存器 `v` / `t` / `fine_x` / `write_toggle`
- PPUDATA read buffer
- NMI 生成（PPUCTRL bit 7 + VBlank + edge-triggered）

**绝对不进入 Phase 4（背景渲染）。**

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/ppu/ppu.py` | **重写** register read/write + NMI 逻辑 |
| `tests/unit/test_ppu_registers.py` | **新建** 寄存器行为单元测试 |
| `src/simplenes/ppu/__init__.py` | 修改 — 确保导出 PPU |
| `tests/unit/test_ppu_bus.py` | **新建** PPUBus nametable/palette mirroring 单测 |

---

## Interface & API Design

### 1. PPU Register Map (`$2000-$2007`)

```
Register | Address | R/W | Summary
---------|---------|-----|--------
PPUCTRL  | $2000   | W   | Controller: NMI enable, PPU master/slave, sprite size, BG/Sprite table, increment mode, name table
PPUMASK  | $2001   | W   | Rendering mask: color emphasis, sprite/BG enable, sprite/BG left column, grayscale
PPUSTATUS| $2002   | R   | Status: VBlank, sprite 0 hit, sprite overflow; clears VBlank on read, resets toggle
OAMADDR  | $2003   | W   | OAM address pointer
OAMDATA  | $2004   | R/W | OAM data read/write (direct OAM array access)
PPUSCROLL| $2005   | W   | Scroll: first write = coarse X + fine X, second write = coarse Y + fine Y
PPUADDR  | $2006   | W   | Address: first write = high byte, second write = low byte → t, then copy t→v on second write
PPUDATA  | $2007   | R/W | VRAM data: read/write to VRAM at current v, auto-increment v
```

### 2. Internal Registers

| Register | Width | Description |
|----------|-------|-------------|
| `v`      | 15-bit | Current VRAM address (active during rendering) |
| `t`      | 15-bit | Temporary VRAM address (written by PPUADDR/PPUSCROLL) |
| `fine_x` | 3-bit  | Fine X scroll (written only by first PPUSCROLL write) |
| `write_toggle` | 1-bit | First/second write latch for PPUSCROLL/PPUADDR |
| `read_buffer` | 8-bit | PPUDATA read delay buffer |

### 3. PPUCTRL ($2000 write) — 详细位域

```
bit 7: NMI enable (V)     — 0=off, 1=generate NMI at VBlank
bit 6: PPU master/slave   — unused on NES
bit 5: Sprite size        — 0=8x8, 1=8x16 (8x16 not used until Phase 5)
bit 4: BG pattern table   — 0=$0000, 1=$1000
bit 3: Sprite pattern table — 0=$0000, 1=$1000
bit 2: VRAM increment     — 0=+1 (across), 1=+32 (down)
bit 1-0: Name table base  — 0=$2000, 1=$2400, 2=$2800, 3=$2C00
```

**t register update:** `self.t = (self.t & 0xF3FF) | ((value & 0x03) << 10)`

#### NMI 生成逻辑（统一 edge detection 方案）

NMI 是紧耦合的 `_nmi_output()` + `_update_nmi()` 模式，统一处理所有 NMI 触发路径：

```python
def _nmi_output(self) -> bool:
    """Internal NMI signal: true when both VBlank and NMI-enable are set."""
    return bool((self.control & 0x80) and (self.status & 0x80))

def _update_nmi(self) -> None:
    """On rising edge (0→1) of NMI output, set nmi_pending on shared InterruptLines."""
    current = self._nmi_output()
    if current and not self._nmi_prev:
        self.interrupts.nmi_pending = True
    self._nmi_prev = current
```

**调用点：**

1. `clock()` — VBlank flag 置位（scanline 241 dot 1）/ 清零（scanline 261 dot 1）后调用
2. `_write_ppuctrl()` — PPUCTRL 写入后调用（支持 VBlank 期间开启 NMI enable 立即触发）
3. `read_register(PPUSTATUS)` — 清除 VBlank flag 后调用

**时序语义：**

- VBlank flag 在 scanline 241, dot 1 置位 → `_update_nmi()` 在置位后立即调用
- 若 PPUCTRL bit 7 已开启，rising edge 触发 NMI
- 若 PPUCTRL bit 7 在 VBlank 期间写入，`_update_nmi()` 立即检测 rising edge
- PPUSTATUS 读取清除 VBlank → `_update_nmi()` 将 `_nmi_output()` 降为 False

**设计决定：Phase 3 不实现精确 dot-level race condition suppression**  
标准 NES VBlank race（CPU 在 VBlank flag 刚被 set 的同一 dot 读取 PPUSTATUS 抑制 NMI）需要在 PPU dot 和 CPU cycle 之间做细粒度交错。
由于当前 architecture 采用 instruction-level scheduler，不暴露 CPU cycle ↔ PPU dot 交错，无法在 Phase 3 实现该行为。
该行为不影响 nestest 或标准的 test ROM 兼容性；若后续需要，可在升级到 cycle-accurate scheduler 时补充。

### 4. PPUMASK ($2001 write)

```
bit 7: Emphasize Blue
bit 6: Emphasize Green
bit 5: Emphasize Red
bit 4: Show sprites
bit 3: Show background
bit 2: Show sprites in leftmost 8 pixels
bit 1: Show background in leftmost 8 pixels
bit 0: Grayscale
```

Phase 3: 仅存储 `mask` 寄存器值即可。渲染逻辑在 Phase 4 使用。

### 5. PPUSTATUS ($2002 read)

```
bit 7: VBlank
bit 6: Sprite 0 hit
bit 5: Sprite overflow
bit 4-0: open bus (low 5 bits of internal data bus latch)
```

**关键行为：**
- 读取后清除 VBlank flag (bit 7 → 0)，但不清除 sprite 0 hit 或 overflow
- 读取后重置 `write_toggle` 为 0
- 读取后调用 `_update_nmi()` 更新 NMI 状态
- **注意**：读取 PPUSTATUS 不会清除已经 pending 的 NMI event（`interrupts.nmi_pending`），只影响后续 rising edge 检测。NMI pending 由 CPU 在 service 时清除。
- 读取时低 5 位返回上次总线值 (open bus 行为，Phase 3 简化为返回 0)

### 6. OAMADDR ($2003 write)

- 直接设置 `oam_address`
- 无额外 side effect

### 7. OAMDATA ($2004 read/write)

- 读：`self.oam[self.oam_address]`
- 写：`self.oam[self.oam_address] = value`，然后 `self.oam_address = (self.oam_address + 1) & 0xFF`
- 渲染期间（visible scanlines）：OAM 操作结果未定义，Phase 3 不做限制

### 8. PPUSCROLL ($2005 write)

**两次写入协议（由 write_toggle 控制）：**

```
toggle=0 (first write):  Horizontal scroll
  t = (t & 0x7FE0) | ((value >> 3) & 0x1F)   // coarse X
  fine_x = value & 0x07                         // fine X
  toggle = 1

toggle=1 (second write): Vertical scroll
  t = (t & 0x0C1F) | ((value & 0x07) << 12)   // fine Y
  t = (t & 0x7C1F) | ((value & 0xF8) << 2)    // coarse Y
  toggle = 0
```

### 9. PPUADDR ($2006 write)

**两次写入协议（由 write_toggle 控制）：**

```
toggle=0 (first write):  High byte
  t = (t & 0x00FF) | ((value & 0x3F) << 8)    // low 6 bits → bits 14-8 of t
  toggle = 1

toggle=1 (second write): Low byte
  t = (t & 0x7F00) | value                      // bits 7-0 of t
  v = t                                         // copy t → v
  toggle = 0
```

**关键：第二轮写入将 t 整体复制到 v。** 这意味着 PPUADDR 和 PPUSCROLL 写入的顺序决定了最终的 t 值，而只有 PPUADDR 的第二轮写入才会复制 t→v。

### 10. PPUDATA ($2007 read/write)

**读（PPUDATA read）：**

```
addr = v & 0x3FFF
if addr < 0x3F00:
    result = read_buffer
    read_buffer = ppu_bus.read(addr)
else:
    result = ppu_bus.read(addr)
    # palette area: read_buffer updated from nametable mirror beneath ($3Fxx → $2Fxx)
    read_buffer = ppu_bus.read(addr - 0x1000)
v += increment
```

**关键：**
- `$0000-$3EFF` 范围：返回 `read_buffer`（上一轮的值），然后填充新值
- `$3F00-$3FFF` 范围：立即返回 palette 值，但 `read_buffer` 用 nametable 底部同名地址的值更新
- 每次读后 `v` 自动递增

**写（PPUDATA write）：**

```
addr = v & 0x3FFF
ppu_bus.write(addr, value)
v += increment
```

### 11. VRAM 地址增量

```
increment = 1 if (PPUCTRL bit 2) == 0 else 32
v = (v + increment) & 0x7FFF
```

`v` 是 15 位地址。PPU bus 实际访问时通过 `v & 0x3FFF` 映射到 `$0000-$3FFF`。Phase 3 保持 `v = (v + increment) & 0x7FFF`。

---

## Data Model Changes

### PPU `__slots__` (Phase 3)

```python
__slots__ = (
    "bus", "interrupts",
    "control", "mask", "status",
    "oam_address",
    "v", "t", "fine_x", "write_toggle", "read_buffer",
    "scanline", "dot", "frame", "odd_frame",
    "framebuffer", "oam",
    "_nmi_prev",   # NEW: previous NMI output state (for edge detection)
)
```

### PPU.reset() 语义

```
control = 0
mask = 0
status = 0
oam_address = 0
v = 0
t = 0
fine_x = 0
write_toggle = False
read_buffer = 0
scanline = 0
dot = 0
frame = 0
odd_frame = False
_nmi_prev = False
```

实际 NES 冷启动时寄存器非确定性，但 nestest 等 test ROM 通常在 reset 后写入寄存器。为可重复测试，reset 归零所有寄存器。

---

## Control Flow

### `PPU.clock()` — 每 dot 更新

```python
def clock(self) -> None:
    self.dot += 1
    if self.dot >= 341:
        self.dot = 0
        self.scanline += 1
        if self.scanline >= 262:
            self.scanline = 0
            self.frame += 1
            self.odd_frame = not self.odd_frame

    # VBlank boundary
    if self.scanline == 241 and self.dot == 1:
        self.status |= 0x80          # set VBlank flag
        self._update_nmi()           # edge-detect NMI
    elif self.scanline == 261 and self.dot == 1:
        self.status &= 0x1F          # clear VBlank, sprite 0, sprite overflow
        self._update_nmi()           # edge-detect NMI
```

### `PPU._write_ppuctrl(value)` — NMI 更新

```python
def _write_ppuctrl(self, value: int) -> None:
    self.control = value & 0xFF
    self.t = (self.t & 0xF3FF) | ((value & 0x03) << 10)
    self._update_nmi()  # handle NMI toggle during VBlank
```

### `PPU.read_register(address)` — 完整寄存器读

```python
def read_register(self, address: int) -> int:
    reg = address & 7
    if reg == 2:  # PPUSTATUS
        result = self.status & 0xE0
        self.status &= 0x7F          # clear VBlank
        self.write_toggle = False
        self._update_nmi()           # edge-detect after VBlank clear
        return result
    elif reg == 4:  # OAMDATA
        return self.oam[self.oam_address]
    elif reg == 7:  # PPUDATA
        return self._read_ppudata()
    return 0  # write-only registers return open bus (0 for now)
```

### `PPU.write_register(address, value)` — 完整寄存器写

```python
def write_register(self, address: int, value: int) -> None:
    reg = address & 7
    if reg == 0:    # PPUCTRL
        self._write_ppuctrl(value)
    elif reg == 1:  # PPUMASK
        self.mask = value & 0xFF
    elif reg == 3:  # OAMADDR
        self.oam_address = value & 0xFF
    elif reg == 4:  # OAMDATA
        self.oam[self.oam_address] = value
        self.oam_address = (self.oam_address + 1) & 0xFF
    elif reg == 5:  # PPUSCROLL
        self._write_ppuscroll(value)
    elif reg == 6:  # PPUADDR
        self._write_ppuaddr(value)
    elif reg == 7:  # PPUDATA
        self._write_ppudata(value)
```

---

## Edge Cases

### E-1: PPUSTATUS 读取后 NMI 状态更新

由于采用 `_update_nmi()` edge detection，PPUSTATUS 读取会自动清除 rising edge condition：

- VBlank flag 被清除 → `_nmi_output()` 变 False → `_nmi_prev` 更新为 False
- 如果 VBlank 尚未再次 set，后续不会再有 rising edge

### E-2: PPUCTRL 在 VBlank 期间写入

若游戏先写入 PPUMASK 等配置，VBlank 到达后，再写入 PPUCTRL bit 7：

- `_write_ppuctrl()` 中调用 `_update_nmi()`
- VBlank flag 已 set，`_nmi_output()` 从 False→True
- `_nmi_prev` 仍为 False（尚未有过上升沿），触发 `nmi_pending = True`
- 符合 NES 行为：VBlank 期间开启 NMI enable 会立即触发

### E-3: PPUADDR/PPUSCROLL 写入与 v/t 交织

由于 PPUSCROLL 和 PPUADDR 共享 `write_toggle`，写入顺序会影响 t：

- 写入 PPUADDR hi → toggle=1 → 写入 PPUSCROLL → 被视为 second write → 重置 toggle
- 同理，PPUSCROLL first → PPUADDR hi（被当作 second write）

实现中不验证调用顺序，交给 toggle 自动处理。

### E-4: PPUDATA read buffer 在 palette 地址的行为

读 `$3F00-$3FFF`：
- 返回的字节来自 palette（立即），但 `read_buffer` 更新为 nametable 底部同名地址的值
- `addr - 0x1000` 将 `$3Fxx` 映射到 `$2Fxx`（nametable mirror）

```python
def _read_ppudata(self) -> int:
    addr = self.v & 0x3FFF
    self.v = (self.v + self._increment()) & 0x7FFF
    if addr < 0x3F00:
        result = self.read_buffer
        self.read_buffer = self.bus.read(addr)
        return result
    else:
        result = self.bus.read(addr)
        self.read_buffer = self.bus.read(addr - 0x1000)
        return result
```

### E-5: v 地址溢出与 wrap

- `v` 是 15-bit (`$0000-$7FFF`)，PPU 地址空间为 `$0000-$3FFF`
- 超出 `$3FFF` 的地址会 wrap 回 `$0000-$3FFF`
- 实现中总是 `v & 0x3FFF` 作为 bus 地址，`v` 自身保持 `+increment & 0x7FFF`

### E-6: 写 PPUDATA 时不对 t/v 区分

PPUDATA write 只通过 `v` 寻址，不涉及 `t`。

### E-7: PPUSTATUS 读取的低 5 位 (open bus)

真实 NES 返回上一次内部总线值的低 5 位。Phase 3 简化：返回 0。不影响 test ROM 兼容性。

---

## Test Plan

### 单元测试 (`tests/unit/test_ppu_registers.py`)

| Test | Description |
|------|-------------|
| `test_ppuctrl_nmi_enable` | PPUCTRL bit 7 → VBlank 触发 NMI |
| `test_ppuctrl_nmi_disable` | PPUCTRL bit 7=0 → VBlank 不触发 NMI |
| `test_ppuctrl_nmi_late_enable` | VBlank 期间写 PPUCTRL bit 7 → 立即触发 NMI |
| `test_ppuctrl_name_table_bits` | PPUCTRL bit 1-0 → t bit 11-10 |
| `test_ppuctrl_increment_mode` | PPUCTRL bit 2 → PPUDATA increment 1 vs 32 |
| `test_ppustatus_vblank_flag` | scanline 241.1 → status bit 7 set, 261→clear |
| `test_ppustatus_clear_on_read` | 读 PPUSTATUS → bit 7 清零, toggle 重置 |
| `test_prerender_clears_status_flags` | scanline 261.1 → status bits 7/6/5 清零 |
| `test_ppustatus_read_prevents_late_nmi` | 读 PPUSTATUS 清除 VBlank → NMI output edge 消失（不再触发 NMI） |
| `test_ppuaddr_first_write` | PPUADDR hi → t bit 14-8, toggle=1 |
| `test_ppuaddr_second_write` | PPUADDR lo → t bit 7-0, v=t, toggle=0 |
| `test_ppuscroll_first_write` | PPUSCROLL first → coarse X + fine_x |
| `test_ppuscroll_second_write` | PPUSCROLL second → coarse Y + fine Y |
| `test_ppuscroll_toggle` | PPUSCROLL x2 → toggle 回 0 |
| `test_ppuscroll_ppuaddr_interleave` | PPUSCROLL → PPUADDR 共享 toggle |
| `test_ppudata_read_buffer` | $0000-$3EFF → 返回 read_buffer, 延迟一字节 |
| `test_ppudata_read_palette` | $3F00+ → 立即返回, read_buffer 更新自 nametable |
| `test_ppudata_write_increment` | 写 PPUDATA → v+1 或 v+32 |
| `test_ppudata_read_increment` | 读 PPUDATA → v 自动递增 |
| `test_oamaddr_write` | $2003 写 → oam_address 更新 |
| `test_oamdata_read` | $2004 读 → oam[oam_address] |
| `test_oamdata_write_inc` | $2004 写 → oam[oam_address]=value, addr++ |
| `test_vblank_nmi_timing` | VBlank set → NMI pending, 两次 VBlank 间不重复触发 |
| `test_ppumask_store` | PPUMASK 写 → mask 寄存 |

### PPUBus 单测 (`tests/unit/test_ppu_bus.py`)

| Test | Description |
|------|-------------|
| `test_nametable_horizontal_mirroring` | NT0/NT1→0, NT2/NT3→1 |
| `test_nametable_vertical_mirroring` | NT0/NT2→0, NT1/NT3→1 |
| `test_nametable_single_screen_lower` | 全映射到 NT0 |
| `test_palette_mirror_3f10_3f00` | $3F10 → $3F00 |
| `test_palette_mirror_3f14_3f04` | $3F14 → $3F04 |
| `test_palette_read_write` | 写 palette → 读回正确值 |
| `test_chr_passthrough_to_mapper` | $0000-$1FFF → mapper.ppu_read/write |

### 集成测试

| Test | Description |
|------|-------------|
| `test_vblank_nmi_flow` | CPU + PPU: PPUCTRL write → VBlank → NMI service → PC jumps to NMI vector |

---

## Implementation Plan

### Step 1: 补充 PPUBus 单测
文件：`tests/unit/test_ppu_bus.py`
- nametable mirroring 完整性（H/V/SINGLE）
- palette mirroring
- CHR passthrough

### Step 2: 实现 NMI edge detection + PPUCTRL / PPUMASK / PPUSTATUS
文件：`src/simplenes/ppu/ppu.py`
- `_nmi_output()` — 返回 `(control & 0x80) && (status & 0x80)`
- `_update_nmi()` — rising edge → `interrupts.nmi_pending = True`
- `_write_ppuctrl(value)` — 更新 control + t name table bits + `_update_nmi()`
- `_write_ppumask(value)` — 存储 mask
- 完善 `read_register(PPUSTATUS)` — 返回 status 高 3 位，清除 VBlank + toggle + `_update_nmi()`
- `clock()` — VBlank set/clear 均调用 `_update_nmi()`

### Step 3: 实现 PPUADDR / PPUSCROLL / write_toggle
- `_write_ppuaddr(value)` — 两轮写入，t bit 操纵，第二轮复制 t→v
- `_write_ppuscroll(value)` — 两轮写入，coarse/fine 位域更新
- toggle 共享

### Step 4: 实现 PPUDATA
- `_read_ppudata()` — read buffer 延迟，palette 例外，v 自动递增
- `_write_ppudata(value)` — bus write + v 自动递增
- `_increment()` — 返回 1 或 32

### Step 5: 实现 OAMADDR / OAMDATA
- 简单寄存器存取 ＋ OAM 写入后 addr 自增

### Step 6: 寄存器单元测试
创建 `tests/unit/test_ppu_registers.py`，覆盖所有寄存器行为、toggle、read buffer、NMI。

### Step 7: NMI 集成测试
- 设置 PPUCTRL NMI enable
- 推进 PPU 到 VBlank
- 验证 CPU NMI service

### Step 8: 验证
- `uv run ruff check src/ tests/`
- `uv run pytest tests/ -q`
- 所有现有测试继续通过

---

## Risks / Open Questions

1. **NMI race condition 精度**：PPUSTATUS 读取抑制 NMI 的精确 dot 级别行为在 Phase 3 不实现，因为 instruction-level scheduler 不暴露 CPU cycle ↔ PPU dot 交错。当前 edge-triggered `_nmi_output()` / `_update_nmi()` 模型已覆盖标准 NMI 行为（VBlank 触发、VBlank 期间 enable、PPUSTATUS 读取后停止）。若后续需要 dot-exact NMI suppression，可在升级到 cycle-accurate scheduler 时细化。

2. **open bus 行为**：PPUSTATUS 低 5 位和 write-only register 读的 open bus 暂时返回 0。绝大多数 test ROM 不依赖此行为。

3. **OAM 读写冲突**：渲染期间（scanline 0-239 visible + pre-render）OAMDATA 行为未定义。Phase 3 不做限制，Phase 5 完善。

4. **PPUDATA 的 v 地址范围**：`v` 保持 15 位，但 bus 地址总是 `& 0x3FFF`。`$3F00-$3FFF` palette 读取时 `read_buffer` 回填使用 `addr - 0x1000`（将 `$3Fxx` 映射到 nametable mirror `$2Fxx`），符合 NES 行为。

5. **four-screen mirroring**：Phase 3 继续拒绝 four-screen，与 Phase 1 一致。
