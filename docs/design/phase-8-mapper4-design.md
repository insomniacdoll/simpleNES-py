# Phase 8：Mapper 4 (MMC3) 实现设计

## Summary

Phase 8 最后实现 Mapper 4 (MMC3 / TxROM)，是最常用的 NES mapper。

MMC3 核心机制：
- **8 KiB 粒度的 PRG banking**：两个 8 KiB switchable + 两个 8 KiB fixed bank
- **细粒度 CHR banking**：两个 2 KiB + 四个 1 KiB = 共 6 个 bank 寄存器
- **CHR A12 inversion**：切换 2 KiB / 1 KiB bank group 在 PPU 地址空间的布局
- **Scanline IRQ counter**：通过 PPU A12 上升沿检测
- **Even/odd 地址寄存器对**：按地址范围解码
- **Mirroring 由 MMC3 控制**（H/V）

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/cartridge/mappers/mapper004_mmc3.py` | **新建** |
| `src/simplenes/machine.py` | **修改** — `_create_mapper()` 改为 instance method，增加 mapper_id=4，传入 `interrupts` |
| `tests/unit/test_mmc3.py` | **新建** |

`cpu_bus.py`、`ppu_bus.py`、`image.py`、`ines.py`、`mapper.py` 均不变。

**PPUBus** 已调用 `mapper.observe_ppu_address(address)` — MMC3 借此检测 PPU A12 上升沿。

---

## Architecture Decisions

### AD-8.4.1: 按地址范围解码寄存器

MMC3 不是简单的 even/odd 对——需要用 `address & 0xE001` 区分 4 组寄存器：

```python
def cpu_write(self, address: int, value: int) -> None:
    if 0x6000 <= address <= 0x7FFF:
        self._prg_ram[address - 0x6000] = value & 0xFF
        return

    reg = address & 0xE001
    if reg == 0x8000:       # Bank select
        self._bank_select = value & 0x07
        self._prg_mode = bool(value & 0x40)
        self._chr_invert = bool(value & 0x80)
    elif reg == 0x8001:     # Bank data
        self._write_bank_data(value)
    elif reg == 0xA000:     # Mirroring
        self._mirroring = Mirroring.HORIZONTAL if (value & 1) else Mirroring.VERTICAL
    elif reg == 0xA001:     # PRG RAM protect (ignored in Phase 8)
        pass
    elif reg == 0xC000:     # IRQ latch
        self._irq_latch = value
    elif reg == 0xC001:     # IRQ reload
        self._irq_reload_flag = True
    elif reg == 0xE000:     # IRQ disable
        self._irq_enabled = False
        self._irq_pending = False
        self._interrupts.irq_mapper = False
    elif reg == 0xE001:     # IRQ enable
        self._irq_enabled = True
```

| 寄存器地址 | 操作 |
|-----------|------|
| $8000 | Bank select (bits 2-0: index, bit6: PRG mode, bit7: CHR A12 invert) |
| $8001 | Bank data (writes to currently selected bank register) |
| $A000 | Mirroring (bit0: 0=V, 1=H) |
| $A001 | PRG RAM protect (ignored) |
| $C000 | IRQ latch |
| $C001 | IRQ reload (fires reload flag) |
| $E000 | IRQ disable + clear pending |
| $E001 | IRQ enable |

### AD-8.4.2: Bank Data 写入

Bank select `bit0-2` 选择 8 个 bank 寄存器之一，bank data 写入目标：

```python
def _write_bank_data(self, value: int) -> None:
    target = self._bank_select
    if target <= 1:
        self._chr_banks[target] = value & 0xFE  # even only (2 KiB aligned)
    elif target <= 5:
        self._chr_banks[target] = value
    elif target == 6:
        self._prg_bank0 = value & 0x3F
    elif target == 7:
        self._prg_bank1 = value & 0x3F
```

| Bank Select | Window |
|-------------|--------|
| 0 | CHR $0000-$07FF (2 KiB, bit0 ignored) |
| 1 | CHR $0800-$0FFF (2 KiB, bit0 ignored) |
| 2 | CHR $1000-$13FF (1 KiB) |
| 3 | CHR $1400-$17FF (1 KiB) |
| 4 | CHR $1800-$1BFF (1 KiB) |
| 5 | CHR $1C00-$1FFF (1 KiB) |
| 6 | PRG $8000-$9FFF (or $C000-$DFFF per PRG mode) |
| 7 | PRG $A000-$BFFF |

### AD-8.4.3: PRG Banking

```python
def _prg_offset(self, address: int) -> int:
    if address < 0xA000:
        bank = self._prg_banks - 2 if self._prg_mode else self._prg_bank0
    elif address < 0xC000:
        bank = self._prg_bank1
    elif address < 0xE000:
        bank = self._prg_bank0 if self._prg_mode else self._prg_banks - 2
    else:
        bank = self._prg_banks - 1
    return (bank * 0x2000 + (address & 0x1FFF)) % len(self._prg_rom)
```

| Mode | $8000-$9FFF | $A000-$BFFF | $C000-$DFFF | $E000-$FFFF |
|------|-------------|-------------|-------------|-------------|
| 0 (normal)  | `prg_bank0` | `prg_bank1` | second-last | last |
| 1 (swapped) | second-last | `prg_bank1` | `prg_bank0` | last |

### AD-8.4.4: CHR Banking (含 A12 inversion)

```python
def _chr_offset(self, address: int) -> int:
    addr = address & 0x1FFF
    if not self._chr_invert:
        # Normal: 2K/2K then 1K/1K/1K/1K
        if addr < 0x0800:
            return self._chr_banks[0] * 0x400 + (addr & 0x7FF)
        elif addr < 0x1000:
            return self._chr_banks[1] * 0x400 + (addr & 0x7FF)
        elif addr < 0x1400:
            return self._chr_banks[2] * 0x400 + (addr - 0x1000)
        elif addr < 0x1800:
            return self._chr_banks[3] * 0x400 + (addr - 0x1400)
        elif addr < 0x1C00:
            return self._chr_banks[4] * 0x400 + (addr - 0x1800)
        else:
            return self._chr_banks[5] * 0x400 + (addr - 0x1C00)
    else:
        # Inverted: 1K/1K/1K/1K then 2K/2K
        if addr < 0x0400:
            return self._chr_banks[2] * 0x400 + addr
        elif addr < 0x0800:
            return self._chr_banks[3] * 0x400 + (addr - 0x0400)
        elif addr < 0x0C00:
            return self._chr_banks[4] * 0x400 + (addr - 0x0800)
        elif addr < 0x1000:
            return self._chr_banks[5] * 0x400 + (addr - 0x0C00)
        elif addr < 0x1800:
            return self._chr_banks[0] * 0x400 + (addr - 0x1000)
        else:
            return self._chr_banks[1] * 0x400 + (addr - 0x1800)
```

#### _chr_invert=False

| PPU Range | Bank Register | Size |
|----------|---------------|------|
| $0000-$07FF | R0 | 2 KiB |
| $0800-$0FFF | R1 | 2 KiB |
| $1000-$13FF | R2 | 1 KiB |
| $1400-$17FF | R3 | 1 KiB |
| $1800-$1BFF | R4 | 1 KiB |
| $1C00-$1FFF | R5 | 1 KiB |

#### _chr_invert=True

| PPU Range | Bank Register | Size |
|----------|---------------|------|
| $0000-$03FF | R2 | 1 KiB |
| $0400-$07FF | R3 | 1 KiB |
| $0800-$0BFF | R4 | 1 KiB |
| $0C00-$0FFF | R5 | 1 KiB |
| $1000-$17FF | R0 | 2 KiB |
| $1800-$1FFF | R1 | 2 KiB |

### AD-8.4.5: Mirroring

```python
# $A000 write: bit0 → H if 1, V if 0
self._mirroring = Mirroring.HORIZONTAL if (value & 1) else Mirroring.VERTICAL
```

### AD-8.4.6: Scanline IRQ Counter

```python
def observe_ppu_address(self, address: int) -> None:
    a12 = bool(address & 0x1000)
    if not self._a12_prev and a12:
        self._clock_irq()
    self._a12_prev = a12

def _clock_irq(self) -> None:
    if self._irq_counter == 0 or self._irq_reload_flag:
        self._irq_counter = self._irq_latch
        self._irq_reload_flag = False
    else:
        self._irq_counter -= 1
    if self._irq_counter == 0 and self._irq_enabled:
        self._irq_pending = True
        self._interrupts.irq_mapper = True
```

关键语义：
- `$C000` 写设置 latch 值。
- `$C001` 写置 reload flag = True（不立即 reload，等下一 A12 clock）。
- A12 上升沿时：
  - `irq_counter == 0` 或 `reload_flag`：`counter = latch`，清 reload flag
  - 否则 `counter -= 1`
  - 若 `counter == 0` 且 `irq_enabled`：assert pending
- `$E000` 写立即禁用 IRQ 并清除 pending。
- `$E001` 写立即启用 IRQ。

### AD-8.4.7: InterruptLines 注入

Mapper 4 需要 `InterruptLines` 引用以驱动 `irq_mapper`。`_create_mapper()` 改为 instance method：

```python
def _create_mapper(self, cartridge):
    if cartridge.mapper_id == 0:
        return NROMMapper(cartridge)
    if cartridge.mapper_id == 1:
        return MMC1Mapper(cartridge)
    if cartridge.mapper_id == 2:
        return UxROMMapper(cartridge)
    if cartridge.mapper_id == 3:
        return CNROMMapper(cartridge)
    if cartridge.mapper_id == 4:
        return MMC3Mapper(cartridge, interrupts=self._interrupts)
    raise UnsupportedMapperError(cartridge.mapper_id)
```

---

## Data Model Changes

### `MMC3Mapper` 类

```python
class MMC3Mapper:
    __slots__ = (
        "_prg_rom", "_prg_banks",
        "_prg_bank0", "_prg_bank1",
        "_chr_memory", "_chr_is_ram",
        "_chr_banks",           # list of 8 ints (6 CHR + 2 unused)
        "_bank_select", "_prg_mode", "_chr_invert",
        "_mirroring", "_prg_ram",
        "_irq_latch", "_irq_counter",
        "_irq_reload_flag", "_irq_enabled", "_irq_pending",
        "_a12_prev", "_interrupts",
    )

    def __init__(self, image: CartridgeImage, interrupts):
        self._prg_rom = image.prg_rom
        self._prg_banks = len(image.prg_rom) // 8192
        self._interrupts = interrupts

        # CHR memory
        self._chr_is_ram = image.chr_is_ram

        # Validate ROM
        if len(self._prg_rom) < 0x8000 or len(self._prg_rom) % 0x2000 != 0:
            raise InvalidRomError("MMC3 PRG ROM must be >= 32 KiB and 8 KiB aligned")
        if not self._chr_is_ram and (len(image.chr_rom) == 0 or len(image.chr_rom) % 0x400 != 0):
            raise InvalidRomError("MMC3 CHR ROM must be 1 KiB aligned")
        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError("MMC3 PRG RAM/NVRAM must be <= 8 KiB")

        self._chr_memory = bytearray(image.chr_rom if not self._chr_is_ram else 8192)

        # Bank registers
        self._chr_banks = [0] * 8
        self._prg_bank0 = 0
        self._prg_bank1 = 1
        self._bank_select = 0
        self._prg_mode = False
        self._chr_invert = False

        # Mirroring
        self._mirroring = Mirroring.HORIZONTAL

        # PRG RAM
        self._prg_ram = bytearray(8192)

        # IRQ
        self._irq_latch = 0
        self._irq_counter = 0
        self._irq_reload_flag = False
        self._irq_enabled = False
        self._irq_pending = False
        self._a12_prev = False
```

`cpu_write` / `_write_bank_data` / `_prg_offset` / `_chr_offset` / `observe_ppu_address` / `_clock_irq` 按上述 AD 伪代码实现。

---

## Control Flow

### Register Write

```
CPU STA $8000-$FFFF → mapper.cpu_write
  → reg = address & 0xE001
  → dispatch to bank select / bank data / mirroring / IRQ
```

### CHR Read + A12 Observation

```
PPU read $0000-$1FFF → PPUBus.read
  → mapper.observe_ppu_address(address) → A12 rise? → _clock_irq
  → mapper.ppu_read → _chr_offset → 6-window dispatch → chr_memory[offset]
```

### IRQ Flow

```
PPU A12 0→1 → _clock_irq
  → counter==0 or reload_flag? counter = latch, clear reload_flag
  → else counter -= 1
  → counter==0 && enabled? irq_pending=True, interrupts.irq_mapper=True
```

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| $C001 reload while counter counting | 置 reload flag，不立即 reload。下一 A12 clock 生效 |
| $E000 写 IRQ disable | 立即清除 irq_pending 和 interrupts.irq_mapper |
| CHR-RAM 模式 | CHR-RAM 使用 8 KiB buffer，bank offset 经 modulo wrap 到该 buffer 内 |
| CHR bank bit0 (2 KiB banks) | 自动 `& 0xFE` 忽略 bit0 |
| PRG bank bit6-7 | `& 0x3F` mask 限制 |
| A12 invert = True | 2 KiB group 移动到 `$1000-$1FFF`，1 KiB group 移动到 `$0000-$0FFF` |

---

## Non-Goals

- MMC6 / TxSROM 等变种
- A12 glitch filter（防干扰检测）
- DMC DMA 与 MMC3 IRQ 竞争

---

## Tests

新建 `tests/unit/test_mmc3.py`（共 25 个）：

```python
# Construction validation (3)
test_mmc3_rejects_prg_rom_too_small()
test_mmc3_rejects_unaligned_chr_rom()
test_mmc3_rejects_prg_ram_over_8k()

# Register decode (3)
test_mmc3_register_decode_even_odd_pairs()
test_mmc3_bank_select_and_data()
test_mmc3_mirroring_register()

# PRG banking (3)
test_mmc3_prg_normal_mode()
test_mmc3_prg_swapped_mode()
test_mmc3_prg_fixed_banks_second_last_and_last()

# CHR banking (5)
test_mmc3_chr_2k_banks_normal()
test_mmc3_chr_1k_banks_normal()
test_mmc3_chr_a12_invert_remaps()
test_mmc3_chr_2k_bank_bit0_ignored()
test_mmc3_chr_ram_no_bank_effect()

# IRQ (5)
test_mmc3_irq_counter_decrement()
test_mmc3_irq_reload_flag_behavior()
test_mmc3_irq_disable_clears_pending()
test_mmc3_irq_enable_after_disable()
test_mmc3_irq_sets_interrupt_line()

# PRG RAM (1)
test_mmc3_prg_ram_read_write()

# Integration (3)
test_mmc3_integration_cpu_bus_routing()
test_mmc3_integration_ppu_a12_observation()
test_mmc3_integration_mirroring_property()

# Factory (2)
test_machine_creates_mmc3_for_mapper_id_4()
test_machine_rejects_unknown_mapper()
```

---

## Implementation Plan

1. 新建 `mapper004_mmc3.py`
2. 修改 `machine.py` — `_create_mapper` 改为 instance method，加 `mapper_id=4`，传入 `interrupts`
3. 新建 `test_mmc3.py`
4. `ruff + pytest` 回归

---

## Risks

| Risk | Mitigation |
|------|-----------|
| R-8.4.1: A12 上升沿检测假触发 | PPUBus 每次 read/write 调用 observe_ppu_address |
| R-8.4.2: CHR A12 invert 6-window 映射错误 | 单元测试覆盖全部 6 个 window 的 normal + invert 布局 |
| R-8.4.3: IRQ reload flag 语义错误 | 单元测试区分 "reload next clock" vs "immediate reload" |
| R-8.4.4: Mapper factory 需传入 interrupts | 改为 instance method，传 `self._interrupts` |

## Verification Criteria

1. `ruff` clean，全部测试通过
2. 按地址范围正确解码 even/odd 寄存器对
3. PRG normal + swapped 模式正确
4. CHR 6-window banking 正确，A12 invert 重映射正确
5. IRQ counter 递减 / reload flag / enable/disable 正确
6. A12 上升沿检测正确
7. Mirroring 由 $A000 控制
8. `NESMachine` 接受 mapper_id=4 ROM，正确注入 interrupts
