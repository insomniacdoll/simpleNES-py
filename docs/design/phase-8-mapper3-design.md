# Phase 8：Mapper 3 (CNROM) 实现设计

## Summary

Phase 8 进行中：Mapper 2 (UxROM) 已实现。下一步实现 Mapper 3 (CNROM)。

CNROM 与 NROM/UxROM 的关键区别：
- PRG ROM 固定映射（与 NROM 相同，无 PRG banking）。
- CHR-ROM 支持 bank switching：CPU 写入 $8000-$FFFF 选择一个 8 KiB CHR bank。
- 无 IRQ、无 CHR-RAM（使用 CHR-ROM）。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/cartridge/mappers/mapper003_cnrom.py` | **新建** |
| `src/simplenes/machine.py` | **修改** — `_create_mapper()` 增加 mapper_id=3 |
| `tests/unit/test_mapper3.py` | **新建** |

### 不变模块

`cpu_bus.py`、`ppu_bus.py`、`image.py`、`ines.py`、`mapper.py`、`errors.py` 均不变。

---

## Architecture Decisions

### AD-8.3.1: CHR bank 寄存器 — 完整字节

```python
def cpu_write(self, address: int, value: int) -> None:
    if 0x6000 <= address <= 0x7FFF:
        self._prg_ram[address - 0x6000] = value & 0xFF
    elif address >= 0x8000:
        self._chr_bank = value & (self._chr_banks - 1)
```

- CPU 写入 $8000-$FFFF 任意地址均更新 CHR bank。
- Bank 值使用完整 8 位 `value & (chr_banks - 1)`。
- 构造时要求 `chr_banks` 为 2 的幂。

### AD-8.3.2: PRG 固定映射（同 NROM）

16 KiB PRG 时 $C000-$FFFF 镜像 $8000-$BFFF；32 KiB 时线性映射。

### AD-8.3.3: 构造时校验

1. PRG ROM = 16 KiB 或 32 KiB。
2. CHR-ROM 存在且为 8 KiB 整数倍、bank 数为 2 的幂。
3. PRG RAM/NVRAM ≤ 8 KiB。

---

## Data Model Changes

### 1. `CNROMMapper` 类

```python
class CNROMMapper:
    __slots__ = (
        "_prg_rom", "_prg_ram",
        "_chr_rom", "_chr_banks",
        "_chr_bank", "_mirroring",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._prg_rom = image.prg_rom
        self._mirroring = image.mirroring

        # Validate PRG
        if len(self._prg_rom) not in (16384, 32768):
            raise InvalidRomError(...)

        # Validate CHR
        chr_size = len(image.chr_rom)
        if chr_size == 0 or chr_size % 8192 != 0:
            raise InvalidRomError("CNROM requires CHR-ROM in 8 KiB multiples")
        self._chr_banks = chr_size // 8192
        if self._chr_banks & (self._chr_banks - 1):
            raise InvalidRomError("CNROM CHR bank count must be power of 2")
        self._chr_rom = image.chr_rom
        self._chr_bank = 0

        # PRG RAM
        prg_ram_total = image.prg_ram_size + image.prg_nvram_size
        if prg_ram_total > 8192:
            raise InvalidRomError(...)
        self._prg_ram = bytearray(8192)

    def _prg_offset(self, address: int) -> int:
        offset = (address - 0x8000) & 0x7FFF
        if len(self._prg_rom) == 16384:
            offset &= 0x3FFF
        return offset

    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if address >= 0x8000:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF
        elif address >= 0x8000:
            self._chr_bank = value & (self._chr_banks - 1)

    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_rom[self._chr_bank * 8192 + (address & 0x1FFF)]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        pass  # CHR-ROM is read-only

    def observe_ppu_address(self, address: int) -> None:
        pass

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
```

### 2. `NESMachine._create_mapper()` 增加：

```python
if cartridge.mapper_id == 3:
    return CNROMMapper(cartridge)

> **实现注意**：当 Mapper 4 (MMC3) 实现时，`_create_mapper()` 需改为 instance method（移除 `@staticmethod`）以支持 MMC3 传入 `self._interrupts`。CNROM 不需要 interrupts，共用同一工厂不受影响。
```

---

## Control Flow

```
CPU STA $8000+ → mapper.cpu_write → chr_bank = value & (chr_banks - 1)
PPU read $0000-$1FFF → mapper.ppu_read → chr_rom[chr_bank * 8192 + offset]
```

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| CHR bank 超出范围 | `& (chr_banks - 1)` mask |
| CHR bank 数非 2 的幂 | 构造时 `InvalidRomError` |
| PRG 16 KiB | $C000 镜像 $8000 |
| PRG 32 KiB | 线性映射 |
| 写入 CHR-ROM | `ppu_write` no-op |
| 无 CHR-ROM | 构造时 `InvalidRomError` |

---

## Tests

新建 `tests/unit/test_mapper3.py`（10 个）：

```python
# Construction & validation (4)
test_cnrom_construction_16k_prg()
test_cnrom_construction_32k_prg()
test_cnrom_construction_rejects_no_chr_rom()
test_cnrom_construction_rejects_non_power_of_two_chr_banks()

# CHR bank switch (3)
test_cnrom_chr_bank_switch_within_range()
test_cnrom_chr_bank_switch_masked()
test_cnrom_default_bank_zero()

# PRG (1)
test_cnrom_prg_16k_mirrors_c000()

# PRG RAM (1)
test_cnrom_prg_ram_read_write()

# Integration (1)
test_cnrom_integration_cpu_bus_routing()
```

---

## Implementation Plan

1. 新建 `mapper003_cnrom.py`
2. 修改 `machine.py` — `_create_mapper()` 增加 `mapper_id=3`
3. 新建 `test_mapper3.py`
4. `ruff + pytest` 回归

---

## Risks

| Risk | Mitigation |
|------|-----------|
| CHR bank mask 对非 power-of-2 不安全 | 构造时强制 power-of-2 校验 |
| 无 CNROM 测试 ROM | 单元测试覆盖 CHR bank switch |

## Verification Criteria

1. `ruff` clean，全部测试通过
2. 原 NROM/UxROM 测试不受影响
3. CHR bank switch 正确
4. PRG 16 KiB 镜像正确
5. `NESMachine` 接受 mapper_id=3 的 ROM
