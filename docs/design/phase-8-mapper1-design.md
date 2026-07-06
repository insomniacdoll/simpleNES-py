# Phase 8：Mapper 1 (MMC1) 实现设计

## Summary

Phase 8 进行中：Mapper 2 (UxROM) 已实现，Mapper 3 (CNROM) 待实现。本文档涵盖 Mapper 1 (MMC1)。

MMC1 核心机制：
- **串行写入协议**：5 次写入完成一个寄存器加载，bit7=1 复位 shift register。
- **4 个内部寄存器**：Control、CHR bank 0、CHR bank 1、PRG bank。
- **PRG banking**：32 KiB switchable（mode 0/1）、16 KiB fixed-first + switchable（mode 2）、16 KiB switchable + fixed-last（mode 3）。
- **CHR banking**：8 KiB 单 bank（mode 0）或 4 KiB 双 bank（mode 1）。
- **Mirroring 由 MMC1 控制**（H/V/single-screen），不来自 ROM header。
- **PRG RAM (WRAM)**：8 KiB，支持电池存档。
- **无 IRQ**。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/cartridge/mappers/mapper001_mmc1.py` | **新建** |
| `src/simplenes/machine.py` | **修改** — `_create_mapper()` 增加 mapper_id=1 |
| `tests/unit/test_mmc1.py` | **新建** |

### 不变模块

`cpu_bus.py`、`ppu_bus.py`、`image.py`、`ines.py`、`mapper.py`、`errors.py` 均不变。

---

## Architecture Decisions

### AD-8.1.1: 串行写入协议

```python
def cpu_write(self, address: int, value: int) -> None:
    if 0x6000 <= address <= 0x7FFF:
        self._prg_ram[address - 0x6000] = value & 0xFF
        return

    if value & 0x80:
        self._shift_reg = 0x10
        self._shift_count = 0
        self._control = self._control | 0x0C
        return

    # LSB-first: value bit0 → shift_reg MSB
    self._shift_reg = (self._shift_reg >> 1) | ((value & 1) << 4)
    self._shift_count += 1
    if self._shift_count == 5:
        self._load_register(address, self._shift_reg)
        self._shift_reg = 0x10
        self._shift_count = 0
```

- bit7=1：复位 shift register（`shift_reg = 0x10`），count=0。同时 `control |= 0x0C`。
- 正常写入：LSB first → `shift_reg = (shift_reg >> 1) | ((value & 1) << 4)`。
- 5 次写入后 `_load_register` 根据 A14/A13 选择目标。

### AD-8.1.2: 寄存器寻址

| A14:A13 | 地址范围 | 寄存器 |
|---------|---------|--------|
| 00 | $8000-$9FFF | Control |
| 01 | $A000-$BFFF | CHR Bank 0 |
| 10 | $C000-$DFFF | CHR Bank 1 |
| 11 | $E000-$FFFF | PRG Bank |

```python
def _load_register(self, address: int, value: int) -> None:
    reg = (address >> 13) & 3
    if reg == 0:
        self._control = value
    elif reg == 1:
        self._chr_bank0 = value
    elif reg == 2:
        self._chr_bank1 = value
    elif reg == 3:
        self._prg_bank = value
```

### AD-8.1.3: Control 寄存器

| Bit(s) | Name | 描述 |
|--------|------|------|
| 0–1 | Mirroring | 0=one-screen lower, 1=one-screen upper, 2=vertical, 3=horizontal |
| 2–3 | PRG ROM bank mode | 0/1=32 KiB switchable, 2=fixed first + switchable $C000, 3=switchable $8000 + fixed last |
| 4 | CHR bank mode | 0=8 KiB (single bank), 1=4 KiB (two banks) |

```python
@property
def mirroring(self) -> Mirroring:
    mm = self._control & 3
    if mm == 0: return Mirroring.SINGLE_SCREEN_LOWER
    if mm == 1: return Mirroring.SINGLE_SCREEN_UPPER
    if mm == 2: return Mirroring.VERTICAL
    return Mirroring.HORIZONTAL
```

### AD-8.1.4: PRG banking

```python
def _prg_offset(self, address: int) -> int:
    mode = (self._control >> 2) & 3
    addr = address & 0x7FFF

    if mode < 2:                        # 32 KiB switchable
        bank = self._prg_bank & 0x0E
        return bank * 0x4000 + addr

    elif mode == 2:                     # fixed first ($8000) + switchable ($C000)
        if address < 0xC000:
            bank = 0
        else:
            bank = self._prg_bank & 0x0F
        return bank * 0x4000 + (address & 0x3FFF)

    else:                               # mode == 3: switchable ($8000) + fixed last ($C000)
        if address < 0xC000:
            bank = self._prg_bank & 0x0F
        else:
            bank = self._prg_banks - 1
        return bank * 0x4000 + (address & 0x3FFF)
```

| mode | $8000-$BFFF | $C000-$FFFF |
|------|-------------|-------------|
| 0 / 1 | `(prg_bank & 0x0E) * 0x4000 + offset` (32 KiB) | same 32 KiB window |
| 2 | bank 0 (fixed first) | `prg_bank` (switchable) |
| 3 | `prg_bank` (switchable) | `prg_banks - 1` (fixed last) |

### AD-8.1.5: CHR banking

```python
def _chr_offset(self, address: int) -> int:
    if self._control & 0x10:            # 4 KiB mode
        if address < 0x1000:
            bank = self._chr_bank0
        else:
            bank = self._chr_bank1
        return bank * 0x1000 + (address & 0xFFF)
    else:                                # 8 KiB mode
        bank = self._chr_bank0 & 0x1E    # 4 KiB units, ignore bit0
        return bank * 0x1000 + (address & 0x1FFF)
```

| mode (bit4) | $0000-$0FFF | $1000-$1FFF |
|-------------|-------------|-------------|
| 0 (8 KiB) | `(chr_bank0 & 0x1E) * 0x1000 + offset` | same 8 KiB window |
| 1 (4 KiB) | `chr_bank0 * 0x1000 + offset` | `chr_bank1 * 0x1000 + offset` |

### AD-8.1.6: PRG RAM 与电池

- PRG RAM = `image.prg_nvram_size`（电池） + `image.prg_ram_size`（volatile），总计 ≤ 8 KiB。
- `cpu_write` 中 `$6000-$7FFF` 直接写入 PRG RAM。
- PRG Bank bit4（`prg_bank & 0x10`）控制 PRG RAM 启用：0=启用，1=禁用。Phase 8 忽略此 bit（始终启用 PRG RAM）。

### AD-8.1.7: 上电/复位状态

- shift_reg = 0x10，count = 0
- control = 0x0C → mirroring=0（single-screen lower），PRG mode=3（switchable + fixed last），CHR mode=0（8 KiB）
- chr_bank0 = chr_bank1 = prg_bank = 0

---

## Data Model Changes

### 1. `MMC1Mapper` 类

```python
class MMC1Mapper:
    __slots__ = (
        "_prg_rom", "_prg_banks",
        "_prg_ram", "_chr_memory", "_chr_is_ram",
        "_shift_reg", "_shift_count",
        "_control", "_chr_bank0", "_chr_bank1", "_prg_bank",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._prg_rom = image.prg_rom
        self._prg_banks = len(image.prg_rom) // 16384

        # Validate PRG ROM
        if len(image.prg_rom) == 0 or len(image.prg_rom) % 0x4000 != 0:
            raise InvalidRomError("MMC1 PRG ROM must be 16 KiB aligned")

        # PRG RAM
        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(...)
        self._prg_ram = bytearray(8192)

        # CHR memory
        self._chr_is_ram = image.chr_is_ram
        if self._chr_is_ram:
            self._chr_memory = bytearray(8192)
        else:
            chr_size = len(image.chr_rom)
            if chr_size == 0 or chr_size % 4096 != 0:
                raise InvalidRomError(...)
            self._chr_memory = bytearray(image.chr_rom)

        # Power-on state
        self._shift_reg = 0x10
        self._shift_count = 0
        self._control = 0x0C
        self._chr_bank0 = 0
        self._chr_bank1 = 0
        self._prg_bank = 0

    # --- Serial protocol (see AD-8.1.1) ---

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
            return
        if value & 0x80:
            self._shift_reg = 0x10
            self._shift_count = 0
            self._control = self._control | 0x0C
            return
        self._shift_reg = ((self._shift_reg >> 1) | ((value & 1) << 4)) & 0x1F
        self._shift_count += 1
        if self._shift_count == 5:
            self._load_register(address, self._shift_reg)
            self._shift_reg = 0x10
            self._shift_count = 0

    def _load_register(self, address: int, value: int) -> None:
        reg = (address >> 13) & 3
        if reg == 0:
            self._control = value
        elif reg == 1:
            self._chr_bank0 = value
        elif reg == 2:
            self._chr_bank1 = value
        elif reg == 3:
            self._prg_bank = value

    # --- PRG (see AD-8.1.4) ---

    def _prg_offset(self, address: int) -> int:
        mode = (self._control >> 2) & 3
        if mode < 2:
            bank = self._prg_bank & 0x0E
            offset = bank * 0x4000 + (address & 0x7FFF)
        elif mode == 2:
            bank = 0 if address < 0xC000 else self._prg_bank & 0x0F
            offset = bank * 0x4000 + (address & 0x3FFF)
        else:  # mode == 3
            bank = (self._prg_bank & 0x0F) if address < 0xC000 else self._prg_banks - 1
            offset = bank * 0x4000 + (address & 0x3FFF)
        return offset % len(self._prg_rom)

    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if address >= 0x8000:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    # --- CHR (see AD-8.1.5) ---

    def _chr_offset(self, address: int) -> int:
        if self._control & 0x10:
            if address < 0x1000:
                return self._chr_bank0 * 0x1000 + (address & 0xFFF)
            return self._chr_bank1 * 0x1000 + (address & 0xFFF)
        else:
            bank = self._chr_bank0 & 0x1E
            return bank * 0x1000 + (address & 0x1FFF)

    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_memory[self._chr_offset(address) % len(self._chr_memory)]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if self._chr_is_ram and 0x0000 <= address <= 0x1FFF:
            self._chr_memory[self._chr_offset(address) % len(self._chr_memory)] = value & 0xFF

    def observe_ppu_address(self, address: int) -> None:
        pass

    @property
    def mirroring(self) -> Mirroring:
        mm = self._control & 3
        if mm == 0: return Mirroring.SINGLE_SCREEN_LOWER
        if mm == 1: return Mirroring.SINGLE_SCREEN_UPPER
        if mm == 2: return Mirroring.VERTICAL
        return Mirroring.HORIZONTAL
```

### 2. `NESMachine._create_mapper()` 增加：

```python
if cartridge.mapper_id == 1:
    return MMC1Mapper(cartridge)

> **实现注意**：当 Mapper 4 (MMC3) 实现时，`_create_mapper()` 需改为 instance method（移除 `@staticmethod`）以支持 MMC3 传入 `self._interrupts`。MMC1 不需要 interrupts，共用同一工厂不受影响。
```

---

## Control Flow

```
CPU STA $8000, value
  bit7=1? → reset shift_reg=0x10, count=0, control|=0x0C
  else    → shift_reg = (shift_reg>>1) | ((value&1)<<4), count++
            count==5? → _load_register(address, shift_reg)
```

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| 写入中途 bit7 置位 | 清空 shift counter；Control OR 0x0C（强制 PRG mode 3，保留 CHR mode bit，不清除 bit4） |
| 写入非连续地址 | 不影响 shift register 累积 |
| PRG bank 超出 ROM | `% len(prg_rom)` 保护 |
| CHR-RAM 模式 | 使用 8 KiB buffer，bank offset 经 modulo wrap 到该 buffer 内 |
| 上电/复位状态 | PRG mode 3（switchable $8000 + fixed last $C000）、CHR 8 KiB mode、mirroring=0 |
| PRG RAM disable (prg_bank bit4) | Phase 8 忽略，PRG RAM 始终可用 |

---

## Non-Goals

- PRG RAM disable bit (`prg_bank & 0x10`)
- MMC1A/B/C 变种细节
- Bus conflict 模拟

---

## Tests

新建 `tests/unit/test_mmc1.py`（共 18 个）：

```python
# Serial write protocol (5)
test_mmc1_serial_write_5_writes_loads_register()
test_mmc1_serial_write_bit7_resets()
test_mmc1_serial_write_bit7_sets_control_or_0C()
test_mmc1_serial_write_partial_not_committed()
test_mmc1_serial_write_register_addressing()

# PRG banking (5)
test_mmc1_prg_mode3_switchable_and_fixed_last()
test_mmc1_prg_mode2_fixed_first_and_switchable()
test_mmc1_prg_mode0_32k_switchable()
test_mmc1_prg_reset_state_mode3_last_bank()
test_mmc1_prg_bank_wraps_with_rom_size()

# CHR banking (3)
test_mmc1_chr_8k_mode()
test_mmc1_chr_4k_mode()
test_mmc1_chr_ram_bank_switch_noop()

# Mirroring (2)
test_mmc1_mirroring_controlled_by_control_register()
test_mmc1_mirroring_default_single_screen_lower()

# PRG RAM (1)
test_mmc1_prg_ram_read_write()

# Integration (2)
test_mmc1_integration_cpu_bus_routing()
test_mmc1_reset_state_correct()
```

---

## Implementation Plan

1. 新建 `mapper001_mmc1.py`
2. 修改 `machine.py` — `_create_mapper()` 增加 `mapper_id=1`
3. 新建 `test_mmc1.py`
4. `ruff + pytest` 回归

---

## Risks

| Risk | Mitigation |
|------|-----------|
| R-8.1.1: 串行协议实现错误 | 单元测试覆盖完整 5-step + reset |
| R-8.1.2: CHR-RAM 使用 8 KiB buffer，bank offset 通过 modulo wrap | `_chr_offset % len(_chr_memory)` 保护 |
| R-8.1.3: MMC1 变种多 | Phase 8 覆盖 4 种 PRG mode + 2 种 CHR mode |
| R-8.1.4: 8 KiB CHR offset 使用 4 KiB unit | `(bank & 0x1E) * 0x1000` 正确 |

## Verification Criteria

1. `ruff` clean，全部测试通过
2. Serial write 5-step 协议正确
3. PRG mode 0/2/3 正确
4. CHR 8 KiB / 4 KiB 模式正确
5. Mirroring 由 control 寄存器控制
6. `NESMachine` 接受 mapper_id=1 的 ROM
