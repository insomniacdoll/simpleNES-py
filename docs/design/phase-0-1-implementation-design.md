# Phase 0 + Phase 1 详细实现设计

## Summary

本文档基于 `docs/architecture.md` 的架构设计，产出 Phase 0（项目骨架）和 Phase 1（ROM 与 Mapper 0）的详细实现级设计。涵盖包命名与目录结构、所有新增/修改模块的接口契约、数据结构、控制流、边界条件、Step-by-step 实现计划与单元测试矩阵。

设计范围：

- **Phase 0**：错误类型体系、枚举常量、InterruptLines、空组件 stub、空 `NESMachine` 组合根、Headless frontend protocol、CLI 入口骨架
- **Phase 1**：`CartridgeImage` 不可变模型、iNES 1.0 parser（含 battery PRG NVRAM）、NES 2.0 拒绝逻辑、`Mapper` protocol、NROM mapper（含 PRG RAM ≤ 8 KiB 校验 + PRG NVRAM 读写）、`CPUBus` 与 `PPUBus` 最小可运行实现

**绝对不进入 Phase 2（CPU opcode 实现）。**

> **Revision History**：
> - v1.4 — battery-backed PRG RAM 映射、文档自包含
> - v1.5 — NROM PRG RAM/NVRAM total ≤ 8 KiB 校验；PPUBus Mirroring import；Edge Cases 展开

---

## Package Naming & Directory Layout

### Decision

基于 `pyproject.toml` 中已定义的项目名 `simplenes-py`，实际 Python 包目录使用 `simplenes`。

### 目录结构

```text
simplenes-py/
├── pyproject.toml
├── main.py                 # 删除
├── docs/
│   ├── architecture.md
│   └── design/
│       └── phase-0-1-implementation-design.md
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_errors.py
│   │   ├── test_interrupts.py
│   │   ├── test_cartridge.py
│   │   ├── test_mapper.py
│   │   ├── test_cpu_bus.py
│   │   └── test_ppu_bus.py
│   ├── integration/
│   │   ├── __init__.py
│   │   └── test_machine.py
│   └── fixtures/
│       └── nrom_sample.py
└── src/
    └── simplenes/
        ├── __init__.py
        ├── __main__.py
        ├── cli.py
        ├── errors.py
        ├── timing.py
        ├── interrupts.py
        ├── machine.py
        ├── scheduler.py
        ├── cartridge/
        │   ├── __init__.py
        │   ├── image.py
        │   ├── ines.py
        │   ├── mapper.py
        │   └── mappers/
        │       ├── __init__.py
        │       └── mapper000_nrom.py
        ├── bus/
        │   ├── __init__.py
        │   ├── cpu_bus.py
        │   └── ppu_bus.py
        ├── cpu/__init__.py + cpu.py
        ├── ppu/__init__.py + ppu.py
        ├── apu/__init__.py + apu.py
        ├── input/__init__.py + controller.py
        ├── dma/__init__.py + oam_dma.py
        └── frontend/
            ├── __init__.py
            ├── protocol.py
            └── headless.py
```

---

## Modules Affected

| Module | Phase | Action |
|--------|-------|--------|
| `pyproject.toml` | 0 | 修改 |
| `main.py` | 0 | 删除 |
| `src/simplenes/errors.py` | 0 | 新建 |
| `src/simplenes/timing.py` | 0 | 新建 |
| `src/simplenes/interrupts.py` | 0 | 新建 |
| `src/simplenes/machine.py` | 0+1 | 新建 |
| `src/simplenes/scheduler.py` | 0 | 新建 |
| `src/simplenes/cli.py` + `__main__.py` | 0 | 新建 |
| `src/simplenes/cartridge/image.py` | 1 | 新建 |
| `src/simplenes/cartridge/ines.py` | 1 | 新建 |
| `src/simplenes/cartridge/mapper.py` | 1 | 新建 |
| `src/simplenes/cartridge/mappers/mapper000_nrom.py` | 1 | 新建 |
| `src/simplenes/bus/cpu_bus.py` | 1 | 新建 |
| `src/simplenes/bus/ppu_bus.py` | 1 | 新建 |
| `src/simplenes/cpu/cpu.py` | 0+1 | 新建 stub |
| `src/simplenes/ppu/ppu.py` | 0+1 | 新建 stub |
| `src/simplenes/apu/apu.py` | 0 | 新建 stub |
| `src/simplenes/input/controller.py` | 0 | 新建 |
| `src/simplenes/dma/oam_dma.py` | 0 | 新建 |
| `src/simplenes/frontend/protocol.py` + `headless.py` | 0 | 新建 |
| `tests/` | 0+1 | 新建 |

---

## Interface & API Design

---

### 1. `simplenes/errors.py`

```python
# 根
class SimpleNESError(Exception):
    """Base exception for all SimpleNES errors."""

# ROM / Cartridge
class RomError(SimpleNESError):
    """Base exception for ROM-related errors."""

class InvalidRomError(RomError):
    """ROM format is invalid or unsupported."""

class UnsupportedMapperError(RomError):
    """Requested mapper is not implemented.
    
    Always constructed with the offending mapper_id for inspection:
        raise UnsupportedMapperError(mapper_id)
    """
    def __init__(self, mapper_id: int) -> None:
        super().__init__(f"Unsupported mapper: {mapper_id}")
        self.mapper_id = mapper_id

class UnsupportedNES2Error(RomError):
    """NES 2.0 format is not yet supported."""

# Emulation
class EmulationError(SimpleNESError):
    """Base exception for runtime emulation errors."""

class CPUBusError(EmulationError):
    """Invalid CPU bus access."""

class PPUBusError(EmulationError):
    """Invalid PPU bus access."""
```

**契约**：所有 public API 异常为 `SimpleNESError` 子类。`UnsupportedMapperError` 携带 `mapper_id`。

---

### 2. `simplenes/timing.py`

```python
from enum import Enum, auto

class Region(Enum):
    NTSC = auto()
    PAL = auto()
    DENDY = auto()

@dataclass(frozen=True, slots=True)
class TimingConstants:
    cpu_clock_hz: int          # NTSC: 1_789_773
    ppu_dots_per_scanline: int # 341
    scanlines_per_frame: int   # 262
    ppu_dots_per_cpu_cycle: int # 3

NTSC_TIMING = TimingConstants(
    cpu_clock_hz=1_789_773,
    ppu_dots_per_scanline=341,
    scanlines_per_frame=262,
    ppu_dots_per_cpu_cycle=3,
)
```

**验收**：
- `Region.NTSC` 可访问
- `NTSC_TIMING.cpu_clock_hz == 1789773`
- `NTSC_TIMING.ppu_dots_per_cpu_cycle == 3`

---

### 3. `simplenes/interrupts.py`

```python
@dataclass
class InterruptLines:
    """Shared interrupt lines between components.
    
    PPU drives nmi_pending.
    APU drives irq_apu_frame and irq_dmc.
    Mapper drives irq_mapper.
    CPU samples at proper boundaries.
    """
    nmi_pending: bool = False
    irq_apu_frame: bool = False
    irq_dmc: bool = False
    irq_mapper: bool = False

    @property
    def irq_active(self) -> bool:
        """True if any IRQ source is asserting."""
        return self.irq_apu_frame or self.irq_dmc or self.irq_mapper

    def clear_irqs(self) -> None:
        """Reset all IRQ flags. Caller must re-assert if still valid."""
        self.irq_apu_frame = False
        self.irq_dmc = False
        self.irq_mapper = False
```

**验收**：
- 默认所有线为 `False`，`irq_active == False`
- `irq_mapper = True` → `irq_active == True`
- `clear_irqs()` → 全部 `False`

---

### 4. `simplenes/cartridge/image.py`

```python
from enum import Enum, auto
from dataclasses import dataclass

class Mirroring(Enum):
    HORIZONTAL = auto()
    VERTICAL = auto()
    FOUR_SCREEN = auto()
    SINGLE_SCREEN_LOWER = auto()
    SINGLE_SCREEN_UPPER = auto()

class RomFormat(Enum):
    INES_1_0 = auto()
    NES_2_0 = auto()

@dataclass(frozen=True, slots=True)
class CartridgeImage:
    """Immutable parsed ROM image. No runtime bank state."""
    format: RomFormat
    mapper_id: int
    submapper_id: int

    prg_rom: bytes
    chr_rom: bytes

    prg_ram_size: int       # bytes of volatile PRG RAM
    prg_nvram_size: int     # bytes of battery-backed PRG RAM
    chr_ram_size: int       # bytes of CHR RAM (8192 if CHR RAM, 0 if CHR ROM)
    chr_nvram_size: int     # battery-backed CHR (0 for NROM)

    mirroring: Mirroring
    has_battery: bool
    has_trainer: bool

    @property
    def prg_rom_banks(self) -> int:
        return len(self.prg_rom) // 16384

    @property
    def chr_is_ram(self) -> bool:
        return len(self.chr_rom) == 0 and self.chr_ram_size > 0
```

---

### 5. `simplenes/cartridge/ines.py`

```python
class RomParser:
    """Static ROM parser. No instances needed."""
    
    INES_MAGIC = b"NES\x1a"
    
    @staticmethod
    def parse(data: bytes) -> CartridgeImage:
        """Parse ROM bytes into CartridgeImage.

        Parser only extracts static ROM information.
        Mapper support validation is handled by NESMachine.

        Raises:
            InvalidRomError: Bad magic, corrupted header, truncated data.
            UnsupportedNES2Error: NES 2.0 format detected.
        """
```

**Parsing Logic**:

```
1. 验证长度 >= 16
2. 验证 magic
3. PRG ROM size = header[4] * 16384
4. CHR ROM size = header[5] * 8192
5. flags6 = header[6], flags7 = header[7]

6. NES 2.0 检测: (flags7 & 0x0C) == 0x08 → UnsupportedNES2Error

7. mirroring:
   if flags6 & 0x08:      → FOUR_SCREEN (优先于 vertical bit)
   elif flags6 & 0x01:   → VERTICAL
   else:                  → HORIZONTAL

8. mapper_id = (flags7 & 0xF0) | (flags6 >> 4)
9. has_battery = bool(flags6 & 0x02)
10. has_trainer  = bool(flags6 & 0x04)

11. PRG RAM total size (iNES 1.0 header byte 8):
    prg_ram_banks = header[8]
    prg_ram_total = 8192 if prg_ram_banks == 0 else prg_ram_banks * 8192

12. Battery-backed PRG RAM 分离:
    if has_battery:
        prg_ram_size   = 0
        prg_nvram_size = prg_ram_total
    else:
        prg_ram_size   = prg_ram_total
        prg_nvram_size = 0

13. CHR RAM size:
    if chr_rom_size == 0:
        chr_ram_size = 8192
    else:
        chr_ram_size = 0
    chr_nvram_size = 0  # Phase 1: CHR NVRAM not implemented

14. trainer offset: if has_trainer, 跳过 512 bytes
15. 验证总长度: 16 + (512 if trainer) + prg_rom_size + chr_rom_size

16. 构造 CartridgeImage
```

**边界条件**：

| 条件 | 行为 |
|------|------|
| `len(data) < 16` | `InvalidRomError("ROM too small")` |
| magic != `NES\x1a` | `InvalidRomError("Invalid NES header")` |
| PRG ROM size == 0 | `InvalidRomError("No PRG ROM")` |
| NES 2.0 | `UnsupportedNES2Error` |
| ROM 数据不足 | `InvalidRomError("ROM data truncated")` |
| header[8]==0, no battery | `prg_ram_size=8192`, `prg_nvram_size=0` |
| header[8]==0, has_battery | `prg_ram_size=0`, `prg_nvram_size=8192` |
| header[8]>0, no battery | `prg_ram_size=header[8]*8192`, `prg_nvram_size=0` |
| header[8]>0, has_battery | `prg_ram_size=0`, `prg_nvram_size=header[8]*8192` |
| CHR ROM size == 0 | `chr_rom=b""`, `chr_ram_size=8192`, `chr_is_ram=True` |
| CHR ROM size > 0 | `chr_ram_size=0`, `chr_is_ram=False` |
| flags6 bit3 + bit0 同时置位 | `FOUR_SCREEN` |
| parse 阶段 **不**检查 mapper 支持性 | parser 独立于 mapper 实现，由 NESMachine 校验 |

---

### 6. `simplenes/cartridge/mapper.py`

```python
@runtime_checkable
class Mapper(Protocol):
    """Protocol defining the Mapper interface."""
    def cpu_read(self, address: int) -> int: ...
    def cpu_write(self, address: int, value: int) -> None: ...
    def ppu_read(self, address: int) -> int: ...
    def ppu_write(self, address: int, value: int) -> None: ...
    def observe_ppu_address(self, address: int) -> None: ...
    @property
    def mirroring(self) -> Mirroring: ...
```

---

### 7. `simplenes/cartridge/mappers/mapper000_nrom.py`

```python
class NROMMapper:
    """Mapper 0: NROM (No ROM mapper).
    
    - PRG ROM: 16 KiB / 32 KiB at $8000-$FFFF
    - PRG RAM: at $6000-$7FFF (volatile + battery-backed combined, max 8 KiB)
    - CHR ROM: 8 KiB at PPU $0000-$1FFF (read-only)
         or CHR RAM: 8 KiB at PPU $0000-$1FFF (read-write)
    - Fixed mirroring from header
    - No bank switching / IRQ
    """

    __slots__ = (
        "_image",
        "_prg_rom",
        "_prg_ram",           # $6000-$7FFF, max 8 KiB
        "_chr_memory",
        "_chr_is_ram",
        "_mirroring",
    )

    def __init__(self, image: CartridgeImage) -> None:
        self._image = image
        self._prg_rom = image.prg_rom  # bytes, semantically read-only
        self._mirroring = image.mirroring
        self._chr_is_ram = image.chr_is_ram

        # Validate PRG ROM size
        if len(self._prg_rom) not in (16384, 32768):
            raise InvalidRomError(
                f"NROM PRG ROM must be 16 KiB or 32 KiB, got {len(self._prg_rom)}"
            )

        # Validate CHR ROM size if not CHR RAM
        if not self._chr_is_ram and len(image.chr_rom) != 8192:
            raise InvalidRomError(
                f"NROM CHR ROM must be 8 KiB, got {len(image.chr_rom)}"
            )

        # PRG RAM: $6000-$7FFF — combine volatile + battery-backed
        prg_memory_size = image.prg_ram_size + image.prg_nvram_size
        # NROM CPU address window for PRG RAM is only 8 KiB
        if prg_memory_size > 8192:
            raise InvalidRomError(
                f"NROM PRG RAM/NVRAM must be <= 8 KiB, got {prg_memory_size}"
            )
        self._prg_ram = bytearray(prg_memory_size or 8192)

        # CHR memory
        if self._chr_is_ram:
            self._chr_memory = bytearray(image.chr_ram_size or 8192)
        else:
            self._chr_memory = bytearray(image.chr_rom)

    # --- PRG ROM mapping ---
    def _prg_offset(self, address: int) -> int:
        offset = (address - 0x8000) & 0x7FFF
        if len(self._prg_rom) == 16384:
            offset &= 0x3FFF
        return offset

    # --- CPU bus ---
    def cpu_read(self, address: int) -> int:
        if 0x6000 <= address <= 0x7FFF:
            return self._prg_ram[address - 0x6000]
        if 0x8000 <= address <= 0xFFFF:
            return self._prg_rom[self._prg_offset(address)]
        return 0

    def cpu_write(self, address: int, value: int) -> None:
        if 0x6000 <= address <= 0x7FFF:
            self._prg_ram[address - 0x6000] = value & 0xFF

    # --- PPU bus ---
    def ppu_read(self, address: int) -> int:
        if 0x0000 <= address <= 0x1FFF:
            return self._chr_memory[address & 0x1FFF]
        return 0

    def ppu_write(self, address: int, value: int) -> None:
        if self._chr_is_ram and 0x0000 <= address <= 0x1FFF:
            self._chr_memory[address & 0x1FFF] = value & 0xFF

    def observe_ppu_address(self, address: int) -> None:
        pass

    @property
    def mirroring(self) -> Mirroring:
        return self._mirroring
```

**边界条件**：

| 条件 | 行为 |
|------|------|
| PRG ROM 16 KiB | `$C000` 镜像到 `$8000` |
| PRG ROM 32 KiB | `$C000` 读第二半 |
| PRG ROM 非 16/32 KiB | `InvalidRomError` |
| CHR ROM != 8 KiB | `InvalidRomError` |
| PRG RAM/NVRAM total > 8 KiB | `InvalidRomError` |
| CHR RAM | `ppu_write` 可写 |
| CHR ROM | `ppu_write` 被跳过 |
| `cpu_write $8000+` | 无效 |
| `cpu_read/write $6000-$7FFF` | PRG RAM 正常读写（max 8 KiB） |

---

### 8. `simplenes/bus/cpu_bus.py`

```python
class CPUBus:
    """
    $0000-$07FF  : 2 KiB internal RAM
    $0800-$1FFF  : RAM mirrors
    $2000-$2007  : PPU registers
    $2008-$3FFF  : PPU register mirrors
    $4000-$4013  : APU registers
    $4014        : OAM DMA
    $4015        : APU status
    $4016        : Controller 1
    $4017        : Controller 2
    $4018-$401F  : disabled
    $4020-$5FFF  : Cartridge expansion
    $6000-$7FFF  : PRG RAM
    $8000-$FFFF  : PRG ROM / Mapper
    """

    __slots__ = (
        "_ram",
        "_ppu",
        "_apu",
        "_mapper",
        "_controller1",
        "_controller2",
        "_oam_dma_state",
    )

    def __init__(self, ppu, apu, mapper, controller1, controller2, oam_dma_state):
        self._ram = bytearray(2048)
        self._ppu = ppu
        self._apu = apu
        self._mapper = mapper
        self._controller1 = controller1
        self._controller2 = controller2
        self._oam_dma_state = oam_dma_state

    def read(self, address: int) -> int:
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

    def write(self, address: int, value: int) -> None:
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

**边界条件**：

| 条件 | 行为 |
|------|------|
| address 未经 `& 0xFFFF` | bus 防御性 mask |
| value 未经 `& 0xFF` | bus 自身 mask |
| `$2008-$3FFF` 写入 | mirror 到 `$2000-$2007` |
| OAM DMA 触发 | Phase 1: 只设置 state 标记 |
| unmapped 读 | 返回 0 |
| `$4018-$401F` | 返回 0（disabled area） |

---

### 9. `simplenes/bus/ppu_bus.py`

```python
from simplenes.cartridge.image import Mirroring
from simplenes.errors import PPUBusError

class PPUBus:
    """
    $0000-$1FFF  : CHR via Mapper
    $2000-$2FFF  : Nametables (2 KiB)
    $3000-$3EFF  : Nametable mirror
    $3F00-$3FFF  : Palette RAM
    """

    __slots__ = ("_mapper", "_nametables", "_palette_ram")
    PALETTE_MIRRORS = {0x10: 0x00, 0x14: 0x04, 0x18: 0x08, 0x1C: 0x0C}

    def __init__(self, mapper):
        self._mapper = mapper
        self._nametables = bytearray(2048)
        self._palette_ram = bytearray(32)

    def read(self, address: int) -> int:
        address &= 0x3FFF
        self._mapper.observe_ppu_address(address)
        if address < 0x2000:
            return self._mapper.ppu_read(address)
        if address < 0x3F00:
            return self._read_nametable(address)
        return self._read_palette(address)

    def write(self, address: int, value: int) -> None:
        address &= 0x3FFF
        self._mapper.observe_ppu_address(address)
        if address < 0x2000:
            self._mapper.ppu_write(address, value)
            return
        if address < 0x3F00:
            self._write_nametable(address, value)
            return
        self._write_palette(address, value)

    # --- Nametable mirroring ---
    # Horizontal: NT0/NT1 → 0,  NT2/NT3 → 1  → nt_select >>= 1
    # Vertical:   NT0/NT2 → 0,  NT1/NT3 → 1  → nt_select &= 1
    def _nametable_index(self, address: int) -> int:
        mirroring = self._mapper.mirroring
        nt_select = (address >> 10) & 3
        if mirroring == Mirroring.HORIZONTAL:
            nt_select = nt_select >> 1  # NT0/NT1→0, NT2/NT3→1
        elif mirroring == Mirroring.VERTICAL:
            nt_select = nt_select & 1   # NT0/NT2→0, NT1/NT3→1
        elif mirroring == Mirroring.FOUR_SCREEN:
            raise PPUBusError("Four-screen mirroring is not supported in Phase 1")
        elif mirroring == Mirroring.SINGLE_SCREEN_LOWER:
            nt_select = 0
        elif mirroring == Mirroring.SINGLE_SCREEN_UPPER:
            nt_select = 1
        return nt_select * 1024 + (address & 0x3FF)

    def _read_nametable(self, address: int) -> int:
        return self._nametables[self._nametable_index(address)]

    def _write_nametable(self, address: int, value: int) -> None:
        self._nametables[self._nametable_index(address)] = value & 0xFF

    # --- Palette ---
    def _palette_index(self, address: int) -> int:
        idx = address & 0x1F
        if idx in self.PALETTE_MIRRORS:
            idx = self.PALETTE_MIRRORS[idx]
        return idx

    def _read_palette(self, address: int) -> int:
        return self._palette_ram[self._palette_index(address)]

    def _write_palette(self, address: int, value: int) -> None:
        self._palette_ram[self._palette_index(address)] = value & 0xFF
```

**边界条件**：

| 条件 | 行为 |
|------|------|
| `$3000-$3EFF` | mirror 到 nametable |
| `$3F10/$3F14/$3F18/$3F1C` | mirror 到 `$3F00/$3F04/$3F08/$3F0C` |
| four-screen（绕过 NESMachine 直接调用） | `PPUBusError` |

---

### 10. Phase 0 Stub Components

#### 10.1 `simplenes/cpu/cpu.py`

```python
class CPU:
    """6502 CPU (Ricoh 2A03). Phase 0 stub."""
    __slots__ = ("bus", "interrupts", "total_cycles", "halted")

    def __init__(self, bus, interrupts):
        self.bus = bus
        self.interrupts = interrupts
        self.total_cycles = 0
        self.halted = False

    def reset(self) -> None:
        self.total_cycles = 0
        self.halted = False

    def step_instruction(self) -> int:
        """Execute one complete instruction. Returns cycles consumed.
        
        Returns 1 instead of 0 to keep Scheduler simple.
        """
        self.total_cycles += 1
        return 1
```

#### 10.2 `simplenes/ppu/ppu.py`

```python
class PPU:
    """PPU (Ricoh 2C02). Phase 0-1 stub with register interface."""
    __slots__ = (
        "bus", "interrupts",
        "control", "mask", "status", "oam_address",
        "v", "t", "fine_x", "write_toggle", "read_buffer",
        "scanline", "dot", "frame", "odd_frame",
        "framebuffer", "oam",
    )

    def __init__(self, bus, interrupts, *, region=None):
        self.bus = bus
        self.interrupts = interrupts
        self.control = 0
        self.mask = 0
        self.status = 0
        self.oam_address = 0
        self.v = 0
        self.t = 0
        self.fine_x = 0
        self.write_toggle = False
        self.read_buffer = 0
        self.scanline = 0
        self.dot = 0
        self.frame = 0
        self.odd_frame = False
        self.framebuffer = bytearray(256 * 240)
        self.oam = bytearray(256)

    def reset(self) -> None:
        self.control = 0
        self.mask = 0
        self.status = 0
        self.write_toggle = False
        self.read_buffer = 0
        self.scanline = 0
        self.dot = 0
        self.frame = 0
        self.odd_frame = False

    def read_register(self, address: int) -> int:
        reg = address & 7
        if reg == 2:  # PPUSTATUS
            result = self.status & 0xE0
            self.status &= 0x7F
            self.write_toggle = False
            return result
        return 0

    def write_register(self, address: int, value: int) -> None:
        pass

    def clock(self) -> None:
        self.dot += 1
        if self.dot >= 341:
            self.dot = 0
            self.scanline += 1
            if self.scanline >= 262:
                self.scanline = 0
                self.frame += 1
                self.odd_frame = not self.odd_frame
            if self.scanline == 241:
                self.status |= 0x80
            elif self.scanline == 261:
                self.status &= 0x7F
```

#### 10.3 `simplenes/apu/apu.py`

```python
class APU:
    """APU (Ricoh 2A03 audio). Silent stub for MVP."""
    __slots__ = ("interrupts",)

    def __init__(self, interrupts, *, region=None):
        self.interrupts = interrupts

    def read_status(self) -> int:
        return 0

    def write_register(self, address: int, value: int) -> None:
        pass

    def clock_cpu_cycle(self) -> None:
        pass
```

#### 10.4 `simplenes/input/controller.py`

```python
class Controller:
    """Standard NES controller with serial shift register.
    
    Button bits:
        bit 0: A
        bit 1: B
        bit 2: Select
        bit 3: Start
        bit 4: Up
        bit 5: Down
        bit 6: Left
        bit 7: Right
    """
    __slots__ = ("_buttons", "_shift_register", "_strobe")

    def __init__(self) -> None:
        self._buttons = 0
        self._shift_register = 0
        self._strobe = False

    def set_buttons(self, buttons: int) -> None:
        self._buttons = buttons & 0xFF

    def write_strobe(self, value: int) -> None:
        new_strobe = bool(value & 1)
        if new_strobe:
            self._shift_register = self._buttons
        self._strobe = new_strobe

    def read(self) -> int:
        if self._strobe:
            return self._buttons & 1
        value = self._shift_register & 1
        self._shift_register = (self._shift_register >> 1) | 0x80
        return value
```

#### 10.5 `simplenes/dma/oam_dma.py`

```python
@dataclass
class OAMDMAState:
    """OAM DMA state machine."""
    active: bool = False
    page: int = 0
    address: int = 0
    data: int = 0
    dummy_cycle: bool = True
    read_phase: bool = True

    def trigger(self, value: int) -> None:
        self.active = True
        self.page = value & 0xFF
        self.address = 0
        self.dummy_cycle = True
        self.read_phase = True

    def reset(self) -> None:
        self.active = False
        self.page = 0
        self.address = 0
        self.data = 0
        self.dummy_cycle = True
        self.read_phase = True
```

#### 10.6 `simplenes/frontend/protocol.py`

```python
@runtime_checkable
class Frontend(Protocol):
    """Protocol for NES emulator frontends (headless, pygame, etc.)."""
    def should_close(self) -> bool: ...
    def poll_input(self) -> int: ...
    def present(self, framebuffer: memoryview) -> None: ...
    def close(self) -> None: ...
```

#### 10.7 `simplenes/frontend/headless.py`

```python
class HeadlessFrontend:
    """Headless frontend for CI, testing, traces, benchmarks."""
    def __init__(self) -> None:
        self._should_close = False
        self._input_state = 0

    def should_close(self) -> bool:
        return self._should_close

    def poll_input(self) -> int:
        return self._input_state

    def present(self, framebuffer: memoryview) -> None:
        pass

    def close(self) -> None:
        self._should_close = True

    def stop(self) -> None:
        self._should_close = True

    def set_input(self, state: int) -> None:
        self._input_state = state & 0xFF
```

---

### 11. `simplenes/scheduler.py`

```python
class Scheduler:
    """Master scheduler for CPU/PPU/APU timing.

    For each CPU cycle, advance PPU by 3 dots and APU by 1 CPU cycle.
    """

    __slots__ = ("_cpu", "_ppu", "_apu", "_timing")

    def __init__(self, cpu, ppu, apu, timing=None):
        self._cpu = cpu
        self._ppu = ppu
        self._apu = apu
        self._timing = timing or NTSC_TIMING

    def step_instruction(self) -> int:
        cycles = self._cpu.step_instruction()
        for _ in range(cycles):
            for _ in range(self._timing.ppu_dots_per_cpu_cycle):
                self._ppu.clock()
            self._apu.clock_cpu_cycle()
        return cycles

    def run_frame(self) -> None:
        current = self._ppu.frame
        while self._ppu.frame == current:
            self.step_instruction()
```

---

### 12. `simplenes/machine.py`

```python
class NESMachine:
    """NES emulation machine - the single composition root."""

    __slots__ = (
        "_interrupts", "_mapper", "_ppu_bus", "_ppu",
        "_apu", "_controller1", "_controller2",
        "_oam_dma", "_cpu_bus", "_cpu", "_scheduler",
    )

    def __init__(self, cartridge, *, region=Region.NTSC):
        from simplenes.cartridge.mappers.mapper000_nrom import NROMMapper

        if region is not Region.NTSC:
            raise ValueError(
                f"Only NTSC is supported in Phase 1, got {region}"
            )

        if cartridge.mapper_id != 0:
            raise UnsupportedMapperError(cartridge.mapper_id)

        if cartridge.mirroring == Mirroring.FOUR_SCREEN:
            raise InvalidRomError(
                "Four-screen mirroring is not supported in Phase 1"
            )

        self._interrupts = InterruptLines()
        self._mapper = NROMMapper(cartridge)
        self._ppu_bus = PPUBus(self._mapper)
        self._ppu = PPU(bus=self._ppu_bus, interrupts=self._interrupts)
        self._apu = APU(interrupts=self._interrupts)
        self._controller1 = Controller()
        self._controller2 = Controller()
        self._oam_dma = OAMDMAState()
        self._cpu_bus = CPUBus(
            ppu=self._ppu, apu=self._apu, mapper=self._mapper,
            controller1=self._controller1,
            controller2=self._controller2,
            oam_dma_state=self._oam_dma,
        )
        self._cpu = CPU(bus=self._cpu_bus, interrupts=self._interrupts)
        self._scheduler = Scheduler(
            cpu=self._cpu, ppu=self._ppu, apu=self._apu, timing=NTSC_TIMING,
        )

    def reset(self) -> None:
        self._cpu.reset()
        self._ppu.reset()
        self._oam_dma.reset()

    def step_instruction(self) -> int:
        return self._scheduler.step_instruction()

    def run_frame(self) -> None:
        self._scheduler.run_frame()

    def set_controller_state(self, port: int, state: int) -> None:
        """Set controller button state. 1-based: 1=controller1, 2=controller2.
        Raises ValueError if port is not 1 or 2."""
        if port == 1:
            self._controller1.set_buttons(state)
        elif port == 2:
            self._controller2.set_buttons(state)
        else:
            raise ValueError(f"Controller port must be 1 or 2, got {port}")

    @property
    def framebuffer(self) -> memoryview:
        return memoryview(self._ppu.framebuffer)
```

---

### 13. CLI & Entry Point

```python
# cli.py
def main() -> None:
    print("simplenes-py v0.1.0 — CLI not yet implemented")

# __main__.py
from simplenes.cli import main
if __name__ == "__main__":
    main()
```

#### pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/simplenes"]

[project]
name = "simplenes-py"
version = "0.1.0"
description = "A NES emulator implemented in Python"
requires-python = ">=3.12"
dependencies = []

[project.scripts]
simplenes = "simplenes.cli:main"

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "ruff>=0.6",
]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

---

## Data Model Changes Summary

| 数据结构 | 模块 | 类型 | 说明 |
|----------|------|------|------|
| `Region` | `timing.py` | Enum | NTSC / PAL / DENDY |
| `TimingConstants` | `timing.py` | frozen dataclass | 时钟常量 |
| `InterruptLines` | `interrupts.py` | mutable dataclass | 共享中断线 |
| `CartridgeImage` | `cartridge/image.py` | frozen dataclass | PRG RAM size 含 battery 分离 |
| `Mirroring` | `cartridge/image.py` | Enum | nametable mirroring |
| `RomFormat` | `cartridge/image.py` | Enum | iNES 1.0 / NES 2.0 |
| `OAMDMAState` | `dma/oam_dma.py` | mutable dataclass | |
| `Mapper` | `cartridge/mapper.py` | Protocol | |

---

## Control Flow

### iNES ROM 加载流程

```text
RomParser.parse(bytes)
  ├─ magic / NES 2.0 / mirroring
  ├─ header[8] → prg_ram_total
  ├─ has_battery → prg_nvram_size=prg_ram_total, prg_ram_size=0
  │                (else prg_ram_size=prg_ram_total, prg_nvram_size=0)
  └─ CartridgeImage(frozen)
        └─ NESMachine
              ├─ region≠NTSC → ValueError
              ├─ mapper≠0 → UnsupportedMapperError
              ├─ FOUR_SCREEN → InvalidRomError
              └─ NROMMapper(image)
                    ├─ PRG ROM 16/32 KiB 校验
                    ├─ CHR ROM 8 KiB 校验
                    ├─ PRG RAM ≤ 8 KiB 校验
                    └─ 分配 PRG RAM、CHR 内存
```

### NESMachine Frame 运行流程 (Phase 1)

```text
run_frame()
  └─ scheduler.run_frame()
        └─ while ppu.frame == current:
               └─ step_instruction()
                     ├─ cpu.step_instruction() → 1
                     └─ ppu.clock() × 3 + apu.clock_cpu_cycle()
```

---

## Edge Cases

### ROM 解析层

| # | 场景 | 预期行为 |
|---|------|----------|
| E-1 | 空文件 | `InvalidRomError` |
| E-1a | header[8]==0, no battery | `prg_ram_size=8192`, `prg_nvram_size=0` |
| E-1b | header[8]==0, has_battery | `prg_ram_size=0`, `prg_nvram_size=8192` |
| E-1c | header[8]>0, no battery | `prg_ram_size=header[8]*8192`, `prg_nvram_size=0` |
| E-1d | header[8]>0, has_battery | `prg_ram_size=0`, `prg_nvram_size=header[8]*8192` |
| E-2 | 只有 header 无 ROM 数据 | `InvalidRomError("ROM data truncated")` |
| E-3 | header PRG 超过实际数据 | `InvalidRomError("ROM data truncated")` |
| E-4 | NES 2.0 header | `UnsupportedNES2Error` |
| E-5 | 损坏 header，PRG=0 | `InvalidRomError` |
| E-6 | CHR ROM size=0 | `chr_ram_size=8192` |
| E-6b | CHR ROM size>0 | `chr_ram_size=0` |
| E-7 | UNIF header | `InvalidRomError("Invalid NES header")` |
| E-7b | header 垃圾 → mapper≠0 | NESMachine `UnsupportedMapperError` |
| E-7c | bit3+bit0 同时置位 | `FOUR_SCREEN` |

### Bus 层

| # | 场景 | 预期行为 |
|---|------|----------|
| E-8 | CPU 写 $4014 | OAMDMAState.active=True |
| E-9 | CPU 读 $2002 | 返回 status & 0xE0，清 VBlank |
| E-10 | CPU 写 $2000 | Phase 1 忽略 |
| E-11 | PPU $3000-$3EFF 读 | mirror 到 nametable |
| E-12 | PPU $3F10 读 | mirror 到 $3F00 |
| E-13 | PPU $3F04 写 | 写到 palette[4] |
| E-14 | PPU $3F14 写 | mirror 到 palette[4] |

### Mapper 层

| # | 场景 | 预期行为 |
|---|------|----------|
| E-15 | NROM 16 KiB PRG，读 $C000 | 返回 PRG[0]（镜像） |
| E-16 | NROM 32 KiB PRG，读 $C000 | 返回 PRG[16384]（非镜像） |
| E-17 | NROM 写 CHR ROM | 写入被忽略 |
| E-18 | NROM 写 CHR RAM | 正常写入 |
| E-19 | NROM CPU 写 $8000+ | 无效 |
| E-20 | PRG ROM size 非 16/32 KiB | `InvalidRomError` |
| E-20a | CHR ROM size != 8192 | `InvalidRomError` |
| E-20b | $6000-$7FFF 读写（volatile + battery） | 正常读写 |
| E-20c | PRG RAM/NVRAM total > 8192 | `InvalidRomError` |

### NESMachine 层

| # | 场景 | 预期行为 |
|---|------|----------|
| E-21 | 非法 cartridge | exception propagate |
| E-22 | reset() 后 | CPU total_cycles=0, PPU v=t=0 |
| E-23 | framebuffer | memoryview(256*240) |
| E-24 | mapper_id ≠ 0 | `UnsupportedMapperError` |
| E-25 | FOUR_SCREEN | `InvalidRomError` |
| E-26 | set_controller_state(0) | `ValueError` |
| E-27 | set_controller_state(3) | `ValueError` |
| E-28 | region ≠ NTSC | `ValueError` |

---

## Implementation Plan

### Step 0.0: 目录结构 + pyproject.toml

```
1. 创建 src/simplenes/ 所有目录与 __init__.py
2. 创建 tests/ 目录树
3. 删除 main.py
4. 更新 pyproject.toml
5. pip install -e ".[dev]"
验收：python -m simplenes, pytest, ruff check
```

### Step 0.1-0.4: errors / timing / interrupts / stubs

```
实现 errors.py, timing.py, interrupts.py
实现 CPU/PPU/APU/Controller/OAMDMAState/Frontend 全部 stub
验收：每个类可实例化，功能正确
```

### Step 1.0-1.1: `image.py` + `ines.py`

```
CartridgeImage + RomParser
- battery PRG RAM 分离到 prg_nvram_size
- has_battery = bool(flags6 & 0x02)
```

### Step 1.2: `mapper000_nrom.py`

```
NROMMapper:
- PRG RAM/NVRAM total ≤ 8 KiB 校验
- cpu_read/write $6000-$7FFF
```

### Step 1.3-1.4: `cpu_bus.py` + `ppu_bus.py`

```
PPUBus: from simplenes.cartridge.image import Mirroring
         from simplenes.errors import PPUBusError
```

### Step 1.5: `machine.py` + `scheduler.py`

### Step 1.7: Tests

---

## Test Matrix

### tests/unit/test_errors.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_simplenes_error_is_base` | SimpleNESError 是 base |
| `test_rom_error_hierarchy` | InvalidRomError → RomError → SimpleNESError |
| `test_emulation_error_hierarchy` | CPUBusError → EmulationError → SimpleNESError |
| `test_can_catch_all_with_base` | except SimpleNESError |
| `test_unsupported_mapper_has_id` | `UnsupportedMapperError(5).mapper_id == 5` |

### tests/unit/test_interrupts.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_default_all_false` | 初始全 False |
| `test_nmi_pending` | nmi_pending 独立 |
| `test_irq_active_any_source` | 任一 IRQ 源 |
| `test_clear_irqs` | clear 后全 False |
| `test_irq_active_false_after_clear` | clear 后 irq_active=False |

### tests/unit/test_cartridge.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_parse_nrom_16k_prg_8k_chr` | 完整合法 ROM |
| `test_parse_nrom_32k_prg` | 32K PRG |
| `test_parse_nes2_rejected` | NES 2.0 → UnsupportedNES2Error |
| `test_parse_empty_data` | 空 → InvalidRomError |
| `test_parse_bad_magic` | bad magic → InvalidRomError |
| `test_parse_chr_ram` | CHR=0 → chr_ram_size=8192 |
| `test_parse_mirroring_horizontal` | bit0=0 → HORIZONTAL |
| `test_parse_mirroring_vertical` | bit0=1 → VERTICAL |
| `test_parse_four_screen` | bit3=1 → FOUR_SCREEN |
| `test_parse_four_screen_overrides_vertical` | bit3+bit0 → FOUR_SCREEN |
| `test_parse_mapper_id` | bit layout 正确 |
| `test_parse_battery` | has_battery 正确 |
| `test_parse_trainer` | has_trainer + 偏移 |
| `test_cartridge_image_immutable` | frozen |
| `test_prg_rom_banks` | 16K→1, 32K→2 |
| `test_chr_is_ram` | CHR 空→True |
| `test_parse_prg_ram_default_8k` | header[8]=0 → prg_ram_size=8192 |
| `test_parse_prg_ram_one_bank` | header[8]=1 → prg_ram_size=8192 |
| `test_parse_prg_ram_multiple_banks` | header[8]=4 → prg_ram_size=32768 |
| `test_parse_battery_prg_nvram_size` | has_battery=True → prg_nvram_size>0, prg_ram_size=0 |

### tests/unit/test_mapper.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_nrom_16k_prg_read` | $C000 镜像 |
| `test_nrom_32k_prg_read` | $C000 非镜像 |
| `test_nrom_chr_rom_read` | 正确读取 |
| `test_nrom_chr_rom_write_ignored` | 写无效 |
| `test_nrom_chr_ram_read_write` | 可读写 |
| `test_nrom_cpu_write_ignored` | $8000+ 写无效 |
| `test_nrom_mirroring` | mirroring 正确 |
| `test_nrom_invalid_prg_size` | → InvalidRomError |
| `test_nrom_invalid_chr_rom_size` | → InvalidRomError |
| `test_nrom_invalid_prg_ram_size` | PRG RAM > 8 KiB → InvalidRomError |
| `test_nrom_prg_ram_read_write` | $6000-$7FFF 正常读写 |
| `test_nrom_prg_nvram_read_write` | has_battery ROM: $6000-$7FFF 正常读写 |

### tests/unit/test_cpu_bus.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_ram_read_write` | $0000-$07FF |
| `test_ram_mirror` | $0800→$0000 |
| `test_ppu_reg_mirror` | $2008→$2000 |
| `test_oam_dma_trigger` | $4014→active |
| `test_mapper_range` | $8000 委托 mapper |

### tests/unit/test_ppu_bus.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_chr_via_mapper_read` | $0000→mapper |
| `test_nametable_horizontal_mirror` | NT0/NT1→相同, NT2/NT3→相同 |
| `test_nametable_vertical_mirror` | NT0/NT2→相同, NT1/NT3→相同 |
| `test_palette_mirror_3f10` | $3F10→$3F00 |
| `test_palette_mirror_3f14` | $3F14→$3F04 |
| `test_observe_ppu_address_called` | 观察被调用 |
| `test_address_masked` | $4000=$0000 |
| `test_four_screen_mirroring_rejected_by_ppu_bus` | FOUR_SCREEN → PPUBusError |

### tests/integration/test_machine.py

| Test Case | 验证内容 |
|-----------|----------|
| `test_create_machine_from_rom_bytes` | 合法 ROM→NESMachine |
| `test_create_machine_invalid_rom` | 非法→exception |
| `test_create_machine_mapper_not_zero` | mapper≠0→UnsupportedMapperError |
| `test_create_machine_four_screen` | FOUR_SCREEN→InvalidRomError |
| `test_create_machine_rejects_non_ntsc` | region=PAL→ValueError |
| `test_reset` | reset 不抛异常 |
| `test_run_frame` | frame 递增 |
| `test_framebuffer` | memoryview, 256*240 |
| `test_controller_state_port_1` | port=1 更新 |
| `test_controller_state_port_2` | port=2 更新 |
| `test_controller_state_invalid_port` | port=0→ValueError |
| `test_controller_state_invalid_port_3` | port=3→ValueError |

---

## Risks / Open Questions

### R-1: PPU Clock Stub 兼容性

Phase 1 的 `ppu.clock()` 仅推进 scanline/dot/frame，无渲染逻辑。Phase 3+ 会重写 clock 内部。

缓解：clock 接口 `def clock(self) -> None` 稳定不变。

### R-2: PPU/APU Register 接口锁定

PPU stub 的 `read_register`/`write_register` 目前返回简单值。Phase 3 会变为完整寄存器行为。

缓解：对外接口 `read_register(address: int) -> int` 和 `write_register(address: int, value: int) -> None` 稳定不变。

### R-3: CPU stub 返回 1 cycle

CPU stub 返回固定值 1，非真实指令 cycle count。

缓解：Phase 2 CPU 实现后 `step_instruction()` 返回真实 cycles (>=2)，Scheduler 无需额外适配路径。

### R-4: Four-screen mirroring

Phase 1 在 `NESMachine` 层拒绝 FOUR_SCREEN ROM（`InvalidRomError`），`PPUBus` 层防御式 `PPUBusError`。`_nametables` 固定 2 KiB。

缓解：后续支持 four-screen 时扩大 nametable RAM 至 4 KiB，移除 `NESMachine` 拒绝逻辑。

### Q-1: MapperRegistry

Phase 1 仅 NROM，Mapper 在 `NESMachine.__init__` 中硬编码构造。

后续：Phase 8 添加第二个 mapper 时引入 `MapperRegistry` + `_factories` dict。

### Q-2: Trainer 数据

Parser 层处理 trainer 偏移（跳过 512 bytes），`CartridgeImage.prg_rom` 不含 trainer 数据。`has_trainer` 仅做标记。

---

## Acceptance Criteria

### Phase 0

- [ ] `python -m simplenes` 可执行
- [ ] `pytest` 可运行
- [ ] `ruff check src/` 无错误
- [ ] `pip install -e ".[dev]"` 后 `import simplenes` 成功

### Phase 1

- [ ] iNES ROM → `CartridgeImage`（含 `prg_nvram_size` 按 battery 分离）
- [ ] NES 2.0 → `UnsupportedNES2Error`
- [ ] region ≠ NTSC → `ValueError`
- [ ] mapper_id ≠ 0 → `UnsupportedMapperError`
- [ ] FOUR_SCREEN → `InvalidRomError`
- [ ] NROM PRG ROM 16/32 KiB 映射正确
- [ ] NROM PRG RAM ≤ 8 KiB 校验（超限 → `InvalidRomError`）
- [ ] NROM PRG RAM $6000-$7FFF 读写正确（volatile + battery-backed）
- [ ] NROM CHR ROM 写保护，CHR RAM 正常读写；CHR ROM ≠ 8 KiB → `InvalidRomError`
- [ ] CPU Bus / PPU Bus 地址空间正确
- [ ] HORIZONTAL: NT0/NT1→0, NT2/NT3→1；VERTICAL: NT0/NT2→0, NT1/NT3→1
- [ ] Palette mirroring 正确
- [ ] `set_controller_state(1/2)` 正确，非法 port → `ValueError`
- [ ] `run_frame()` PPU frame 递增
- [ ] 所有测试通过
