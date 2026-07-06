# Phase 8：Mapper 2 (UxROM) 实现设计

## Summary

Phase 0–7 已完成：CPU、PPU 背景+精灵、controller、OAM DMA、pygame 前端、APU 音频。当前仅支持 Mapper 0 / NROM。

Phase 8 实现 Mapper 2 (UxROM / UNROM)，是 roadmap 中"更多 Mapper"的第一个目标。UxROM 使用 CHR-RAM（而非 CHR-ROM），PRG 使用 16 KiB 可切换 bank + 16 KiB 固定 bank 的 banking 方案，无 IRQ。

Phase 8 范围：
- 新增 `UxROMMapper` 类
- `NESMachine` 增加 `_create_mapper()` 私有工厂，支持 mapper_id=2
- `CPUBus` 中 mapper 地址路由无需修改（已支持 `>= 0x4020` 路由），但 PRG ROM 写入 $8000-$FFFF 在 UxROM 中是 bank 寄存器写入
- 覆盖 CHR-RAM 读/写
- 单元测试覆盖 bank switch、CHR-RAM、固定 bank、边界、非法 ROM 构造

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/cartridge/mappers/mapper002_uxrom.py` | **新建** |
| `src/simplenes/machine.py` | **修改** — 添加 `_create_mapper()` 私有工厂，移除 mapper_id != 0 硬拒绝 |
| `tests/unit/test_mapper2.py` | **新建** |
| `tests/unit/test_mapper.py` | **不变** |

### 不变模块（无需修改）

| Module | 原因 |
|--------|------|
| `src/simplenes/bus/cpu_bus.py` | `address >= 0x4020` 路由已覆盖 mapper，无需修改 |
| `src/simplenes/bus/ppu_bus.py` | CHR 区 `$0000-$1FFF` 已委托 `mapper.ppu_read/write` |
| `src/simplenes/cartridge/image.py` | 不可变 CartridgeImage 已包含所有必要字段 |
| `src/simplenes/cartridge/ines.py` | 已解析 mapper_id，无需修改 |
| `src/simplenes/cartridge/mapper.py` | Mapper Protocol 覆盖所需接口 |
| `src/simplenes/errors.py` | 已有 `UnsupportedMapperError`、`InvalidRomError` |

---

## Architecture Decisions

### AD-8.1: `_create_mapper()` 私有工厂在 NESMachine 中实现

```python
def _create_mapper(self, cartridge: CartridgeImage):
    if cartridge.mapper_id == 0:
        return NROMMapper(cartridge)
    if cartridge.mapper_id == 2:
        return UxROMMapper(cartridge)
    raise UnsupportedMapperError(cartridge.mapper_id)
```

- 移除 `if cartridge.mapper_id != 0:` 硬拒绝。
- 保留 `FOUR_SCREEN` 检测。
- 工厂为 `NESMachine` 私有方法，不暴露到包外部。

### AD-8.2: PRG 写入 $8000-$FFFF 是 bank 寄存器写入

```python
def cpu_write(self, address: int, value: int) -> None:
    if 0x6000 <= address <= 0x7FFF:
        self._prg_ram[address - 0x6000] = value & 0xFF
    elif address >= 0x8000:
        # Any write to $8000-$FFFF sets bank register
        self._prg_bank = (value & 0x0F) & self._bank_mask
```

- UxROM 无真正的 PRG ROM 写入——总线写入 $8000-$FFFF 被 mapper 拦截为 bank select。
- Bank 寄存器只使用低 4 位，再与 `_bank_mask` 做 AND，确保 bank 值不超出实际 ROM bank 数。
- `_bank_mask = prg_banks - 1`，在构造时计算。要求 `prg_banks` 为 2 的幂（见 AD-8.5）。
- 无 bus conflict 模拟（Phase 8 不做 bus conflict）。

### AD-8.3: CHR-RAM 而非 CHR-ROM

UxROM 卡带总是使用 CHR-RAM（可写 pattern table）。Mapper 内部分配 8 KiB `bytearray` 用于 CHR 数据，PPU 可读写。

- `ppu_read` 时返回 CHR-RAM 数据
- `ppu_write` 时写入 CHR-RAM
- `observe_ppu_address` 对 UxROM 为 no-op（无 MMC3 风格的地址观察需求）
- 构造时如果 ROM 包含 CHR-ROM，**明确拒绝**（见 AD-8.5）。

### AD-8.4: PRG 固定 bank 为最后一个 bank

UxROM 的 $C000-$FFFF 固定映射到 PRG ROM 的**最后一个 16 KiB bank**：

```python
_fixed_bank_offset = (prg_banks - 1) * 0x4000
```

$8000-$BFFF 映射到由 `_prg_bank` 选择的 16 KiB bank。

### AD-8.5: 构造时 ROM 校验

在 `__init__` 中校验：

1. **PRG bank 数**：必须 ≥ 2 且 ≤ 16，并且为 2 的幂。
2. **CHR-ROM**：Phase 8 不支持 CHR-ROM 的 UxROM，必须拒绝。
3. **PRG RAM/NVRAM**：总量不超过 8 KiB（CPU 可见窗口上限）。

校验失败抛出 `InvalidRomError`。

```python
if prg_banks < 2 or prg_banks > 16:
    raise InvalidRomError(f"UxROM PRG banks must be 2-16, got {prg_banks}")
if prg_banks & (prg_banks - 1):
    raise InvalidRomError(f"UxROM PRG bank count must be power of 2, got {prg_banks}")

if len(image.chr_rom) != 0:
    raise InvalidRomError("UxROM with CHR ROM is not supported in Phase 8")

prg_ram_total = image.prg_ram_size + image.prg_nvram_size
if prg_ram_total > 8192:
    raise InvalidRomError(f"UxROM PRG RAM/NVRAM must be <= 8 KiB, got {prg_ram_total}")
```

---

## Data Model Changes

### 1. `UxROMMapper` 类

```python
class UxROMMapper:
    """Mapper 2: UxROM (UNROM).

    - PRG ROM: 16 KiB switchable bank at $8000-$BFFF
               + 16 KiB fixed bank at $C000-$FFFF (last bank)
    - CHR: 8 KiB CHR-RAM (always read-write)
    - Mirroring: fixed from header
    - No IRQ
    """

    __slots__ = (
        "_prg_rom", "_prg_banks", "_bank_mask",
        "_prg_bank",            # 0..bank_mask, selects bank at $8000-$BFFF
        "_fixed_bank_offset",   # offset to last bank ($C000-$FFFF)
        "_prg_ram",             # $6000-$7FFF, up to 8 KiB
        "_chr_ram",             # 8 KiB CHR-RAM
        "_mirroring",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._prg_rom = image.prg_rom
        self._prg_banks = len(image.prg_rom) // 16384
        self._mirroring = image.mirroring

        # Validate PRG bank count
        if self._prg_banks < 2 or self._prg_banks > 16:
            raise InvalidRomError(
                f"UxROM PRG banks must be 2-16, got {self._prg_banks}"
            )
        if self._prg_banks & (self._prg_banks - 1):
            raise InvalidRomError(
                f"UxROM PRG bank count must be power of 2, got {self._prg_banks}"
            )

        self._bank_mask = self._prg_banks - 1
        self._prg_bank = 0
        self._fixed_bank_offset = (self._prg_banks - 1) * 0x4000

        # Reject CHR-ROM UxROM in Phase 8
        if len(image.chr_rom) != 0:
            raise InvalidRomError(
                "UxROM with CHR ROM is not supported in Phase 8"
            )

        # PRG RAM: $6000-$7FFF, window is 8 KiB
        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(
                f"UxROM PRG RAM/NVRAM must be <= 8 KiB, got {prg_ram_total}"
            )
        self._prg_ram = bytearray(8192)

        # CHR-RAM: 8 KiB, initialised to zero
        self._chr_ram = bytearray(8192)

    # --- PRG ROM mapping ---
    def _switchable_offset(self, address: int) -> int:
        return self._prg_bank * 0x4000 + (address - 0x8000)

    def _fixed_offset(self, address: int) -> int:
        return self._fixed_bank_offset + (address - 0xC000)

    # --- CPU bus ---
    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if 0x8000 <= address <= 0xBFFF:
            return self._prg_rom[self._switchable_offset(address)]
        if 0xC000 <= address <= 0xFFFF:
            return self._prg_rom[self._fixed_offset(address)]
        return 0

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
        elif address >= 0x8000:
            # Any write to $8000-$FFFF sets bank register
            self._prg_bank = (value & 0x0F) & self._bank_mask

    # --- PPU bus ---
    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_ram[address & 0x1FFF]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if 0x0000 <= address <= 0x1FFF:
            self._chr_ram[address & 0x1FFF] = value & 0xFF

    def observe_ppu_address(self, address: int) -> None:
        pass  # UxROM has no PPU address observer requirement

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
```

### 2. `NESMachine` 私有 mapper 工厂

```python
def _create_mapper(self, cartridge: CartridgeImage):
    """Private factory: create the correct Mapper for the cartridge."""
    if cartridge.mapper_id == 0:
        return NROMMapper(cartridge)
    if cartridge.mapper_id == 2:
        return UxROMMapper(cartridge)
    raise UnsupportedMapperError(cartridge.mapper_id)
```

`__init__` 中替换：

```python
# 旧: if cartridge.mapper_id != 0: raise UnsupportedMapperError(...)
# 新:
self._mapper = self._create_mapper(cartridge)
```

### 3. CPUBus 地址路由（不变）

当前 CPUBus 的 `address >= 0x4020` 路由已经将 $8000-$FFFF 区域委托给 `mapper.cpu_read/write`。UxROM 的 bank 寄存器写入发生在 `mapper.cpu_write` 中，对 CPUBus 完全透明。

### 4. PPUBus 地址路由（不变）

CHR $0000-$1FFF 区域通过 `mapper.ppu_read/write` 路由，UxROM 的 CHR-RAM 读写在 mapper 内部完成。

---

## Control Flow

### Bank Switch 流程

```
CPU 执行 STA $8000+, value → CPUBus.write(address >= 0x4020)
  → mapper.cpu_write(address, value)
    → self._prg_bank = (value & 0x0F) & self._bank_mask
```

后续 `cpu_read($8000-$BFFF)` 使用 `_switchable_offset()` 计算 PRG ROM 偏移。

### CHR-RAM 写入流程

```
PPU 写寄存器 $2007, value → PPUBus.write($0000-$1FFF, value)
  → mapper.ppu_write(address, value)
    → self._chr_ram[address & 0x1FFF] = value & 0xFF
```

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| PRG bank 值超出 ROM bank 数 | `(value & 0x0F) & bank_mask` 限制在合法范围内。例如 4 bank ROM 写入 bank=9 → bank=9 & 3 = 1 |
| CHR-ROM UxROM | 构造时 `InvalidRomError`：Phase 8 不支持 |
| CHR-RAM 初始值 | 全 0（`bytearray(8192)`） |
| PRG bank 不是 2 的幂 | 构造时 `InvalidRomError` |
| PRG RAM/NVRAM > 8 KiB | 构造时 `InvalidRomError` |
| $8000 写入 bank 寄存器 | 与 $FFFF 写入行为相同，均为 bank select |
| ROM 规模 64 KiB PRG（4 banks） | `_fixed_bank_offset = 3 * 0x4000` = $C000。`bank_mask = 3`。bank 寄存器有效值 0-3 |
| ROM 规模 128 KiB PRG（8 banks） | `_fixed_bank_offset = 7 * 0x4000`。`bank_mask = 7`。bank 寄存器有效值 0-7 |
| ROM 规模 256 KiB PRG（16 banks） | `_fixed_bank_offset = 15 * 0x4000`。`bank_mask = 15`。bank 寄存器有效值 0-15 |

---

## Non-Goals (Phase 8)

- Mapper 3 / 1 / 4
- Bus conflict 模拟
- Save state / load state（Mapper 2 内部）
- CHR bank switching
- IRQ

---

## Tests

新建 `tests/unit/test_mapper2.py`（共 15 个）：

### 构造与 ROM 校验 (5)

```python
test_uxrom_construction_valid_4banks()
# 64 KiB PRG → 4 banks, bank_mask=3, bank 初始 0, CHR-RAM 8 KiB

test_uxrom_construction_valid_8banks()
# 128 KiB PRG → 8 banks, bank_mask=7

test_uxrom_construction_rejects_chr_rom()
# CHR-ROM UxROM → InvalidRomError

test_uxrom_construction_rejects_non_power_of_two_banks()
# 48 KiB PRG（3 banks）→ InvalidRomError

test_uxrom_construction_rejects_prg_ram_over_8k()
# PRG RAM/NVRAM > 8 KiB → InvalidRomError
```

### PRG Bank Switch (3)

```python
test_uxrom_bank_switch_within_range()
# 写入 bank=2 → $8000 读取映射到 bank 2

test_uxrom_bank_switch_masked_by_bank_mask()
# 4 bank ROM, 写入 bank=7 → bank=(7 & 3) = 3, $8000 映射到 bank 3

test_uxrom_fixed_bank_unchanged()
# $C000-$FFFF 始终映射到最后一个 bank, 不随 bank 寄存器变化
```

### CHR-RAM (2)

```python
test_uxrom_chr_ram_read_write()
# PPU 写入 CHR-RAM 后能读回

test_uxrom_chr_ram_initial_zero()
# CHR-RAM 初始值为 0
```

### PRG RAM (1)

```python
test_uxrom_prg_ram_read_write()
# $6000-$7FFF 读写
```

### Bank 寄存器写入 (2)

```python
test_uxrom_bank_register_write_any_address()
# 任意 $8000-$FFFF 地址写入均更新 bank 寄存器

test_uxrom_bank_register_masks_low_4_bits_and_bank_mask()
# value=0x9F → (0x0F & bank_mask) 结合两个 mask
```

### 集成 / 端到端 (2)

```python
test_uxrom_integration_cpu_bus_routing()
# 通过 NESMachine 构造 UxROM 实例，验证 CPUBus 路由正确

test_uxrom_mirroring_from_header()
# 验证 mirroring 属性来自 ROM header
```

---

## Implementation Plan

1. 新建 `src/simplenes/cartridge/mappers/mapper002_uxrom.py` — `UxROMMapper` 类
2. 修改 `src/simplenes/machine.py` — 添加 `_create_mapper()`，移除 hardcoded check
3. 新建 `tests/unit/test_mapper2.py` — 14 个测试
4. `ruff + pytest` 回归

---

## Risks

| Risk | Mitigation |
|------|-----------|
| R-8.1: CHR-RAM 初始随机值可能使画面异常 | 使用 `bytearray(8192)` 保证全 0 初始 |
| R-8.2: PRG bank 寄存器越界 | `_bank_mask` 在构造时计算为 `prg_banks - 1`，bank 写入时应用 mask，且构造时校验 bank 数为 2 的幂 |
| R-8.3: 原 NROM 测试受 mapper 工厂重构影响 | 重构后 NROM 测试必须全部通过（240+ 测试不变） |
| R-8.4: 无 UxROM 测试 ROM | 单元测试覆盖 bank switch + CHR-RAM 正确性；后续可收集 UxROM ROM（如 Mega Man、Castlevania）做集成验证 |

## Verification Criteria

1. `ruff` clean
2. `uv run pytest tests/ -q` 全部通过（240+ existing + 15 new）
3. 原 NROM 测试不受影响
4. UxROM 构造拒绝非法 ROM（CHR-ROM、非 power-of-two bank、超大 PRG RAM）
5. Bank switch 正确：写入 bank=N → $8000 读取 bank N 数据；超出时 mask 生效
6. Fixed bank 不受 bank 寄存器影响
7. CHR-RAM 可读写
8. `NESMachine` 接受 mapper_id=2 的 ROM
