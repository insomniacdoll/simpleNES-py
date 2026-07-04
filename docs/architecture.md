# simpleNES-py 架构设计文档

## Summary

simpleNES-py 目标是构建一个以正确性优先、可测试、可演进的 Python NES 模拟器。项目应先形成纯 Python 参考核心，完成 NROM 最小闭环，再逐步扩展 PPU 精度、APU、更多 Mapper、调试与性能优化。

核心架构采用硬件边界建模：`NESMachine` 作为组合根，`CPU` / `PPU` / `APU` 模拟各自硬件状态，`CPUBus` / `PPUBus` 分离地址空间，`Mapper` 表示卡带运行时硬件行为，`Scheduler` 统一推进时间、DMA 与中断，`Frontend` 只作为核心客户端。

---

## Scope / Non-Scope

### MVP Scope

第一阶段建议严格限制为：

- Python 3.12+
- NTSC
- iNES 1.0
- 检测并明确拒绝暂不支持的 NES 2.0
- Mapper 0 / NROM
- 官方 6502 指令集
- CPU instruction-level execution
- PPU 对外 dot-level clock
- PPU 寄存器、nametable、palette、背景渲染
- 基础 sprite
- 单手柄输入
- OAM DMA
- 静音 APU stub
- Headless frontend
- Pygame frontend 可选
- CPU trace / nestest 验收
- PPU register tests / framebuffer hash regression

### Full Scope

后续完整版本可逐步支持：

- NES 2.0
- Mapper 1 / 2 / 3 / 4
- 完整 APU，包括 DMC
- 精确 OAM DMA / DMC DMA / IRQ / NMI 边界
- Save RAM / Save State
- Debugger / disassembler / inspectors
- Movie replay / rewind
- PAL / Dendy
- 可选 Cython 或 Rust 热点后端

### Non-Scope for MVP

MVP 不实现：

- MMC1 / MMC3
- 完整 APU 与 DMC
- PAL / Dendy
- 非官方 opcode
- 精确 sprite overflow bug
- NES 2.0 完整解析
- Save State
- rewind
- debugger GUI
- ZIP ROM

---

## System Boundaries

### 顶层结构

```text
Frontend
  └── NESMachine
        ├── Scheduler
        ├── CPU ── CPUBus ── RAM / PPU regs / APU regs / Controller / Mapper
        ├── PPU ── PPUBus ── CHR / Nametable / Palette / Mapper
        ├── APU
        ├── DMA coordinator
        ├── InterruptLines
        └── Mapper ── CartridgeImage
```

### 稳定边界

| 边界 | 责任 |
|---|---|
| `CartridgeImage` | 描述 ROM 静态信息，不包含运行时 bank 状态 |
| `Mapper` | 描述卡带运行时硬件行为，包含 PRG/CHR/RAM bank 与 IRQ 状态 |
| `CPUBus` | 描述 CPU 地址空间与寄存器映射 |
| `PPUBus` | 描述 PPU 地址空间、nametable mirroring、palette mirroring |
| `CPU` | 只维护 CPU 寄存器、状态、指令执行与中断采样 |
| `PPU` | 只维护 PPU 寄存器、渲染管线、OAM、framebuffer、NMI 产生 |
| `APU` | 维护音频通道、frame counter、IRQ、采样缓冲 |
| `Scheduler` | 推进 CPU/PPU/APU 时钟，协调 DMA、DMC 与中断边界 |
| `NESMachine` | 组合根与对外稳定 API |
| `Frontend` | 窗口、输入、音频输出、frame pacing、UI；不得被核心 import |

### 推荐目录

```text
src/simple_nes/
  machine.py
  scheduler.py
  timing.py
  interrupts.py
  errors.py
  cpu/
  ppu/
  apu/
  bus/
  cartridge/
  input/
  dma/
  frontend/
  debug/
  state/
  _speed/
tests/
  unit/
  traces/
  roms/
  integration/
  regression/
  fixtures/
docs/
  architecture.md
```

实际包名应与现有 `pyproject.toml` 保持一致；若当前项目尚未确定包名，建议后续由 designer 阶段统一命名。

---

## Architecture Decisions

### AD-1: 纯 Python 核心作为正确性参考

核心运行时默认零第三方依赖，优先保证可读性、可测试性与硬件边界清晰。性能优化只能在测试充分后基于 profiling 进行。

### AD-2: `NESMachine` 是唯一组合根

所有组件由 `NESMachine` 创建和连接。外部客户端只通过稳定 API 操作模拟器：

```python
reset()
step_instruction()
step_cpu_cycle()
run_frame()
set_controller_state(port, state)
framebuffer
save_state()
load_state(data)
```

Frontend 不直接访问 CPU / PPU 内部字段。

### AD-3: CPU Bus 与 PPU Bus 完全分离

NES 的 CPU 地址空间和 PPU 地址空间行为不同，必须分离建模。不要使用运行时遍历 device list 的通用总线框架；热路径使用直接地址判断。

### AD-4: Cartridge 与 Mapper 分离

`CartridgeImage` 是 ROM 解析后的不可变静态信息。`Mapper` 是运行时硬件状态机，负责 PRG/CHR/RAM 映射、bank register、mirroring、IRQ 状态与状态保存。

### AD-5: Scheduler 统一时间推进

MVP 使用指令级 CPU 调度：CPU 执行一条指令返回 cycles，Scheduler 为每个 CPU cycle 推进 3 个 PPU dot 和 1 个 APU CPU-cycle。后续可演进到 master-clock / bus-cycle accurate 模式。

`run_frame()` 应以 PPU frame 变化为准，不使用固定 CPU cycle 数判断一帧。

### AD-6: 中断使用共享 `InterruptLines`

PPU / APU / Mapper 不直接 callback CPU。它们更新共享 interrupt lines，CPU 在正确边界采样 NMI / IRQ。

### AD-7: DMA 由 Scheduler 协调

CPU 写 `$4014` 时只创建 OAM DMA 请求，不立即复制 256 字节。DMA 执行期间 CPU 暂停，PPU/APU 继续运行。DMC DMA 后续也应纳入 Scheduler。

### AD-8: Framebuffer 保存 palette index

核心 framebuffer 使用 `bytearray(256 * 240)` 保存 palette index，不保存 RGB tuple。RGB/RGBA 转换属于 frontend 责任。

### AD-9: Frontend 与核心解耦

核心不 import pygame。Headless frontend 用于 CI、trace、test ROM、frame hash、benchmark；Pygame frontend 负责窗口、输入、音频和真实时间节流。

### AD-10: Save RAM 与 Save State 分离

Save RAM 只保存电池 PRG NVRAM，可跨版本读取。Save State 保存完整模拟器状态，必须带 schema version 和 ROM hash，不建议长期格式直接 pickle 对象图。

---

## Component Architecture

### Cartridge

职责：

- 解析 iNES 1.0 header
- 检测 NES 2.0 并明确拒绝或转交后续 parser
- 构建不可变 `CartridgeImage`
- 提供 mapper id、mirroring、PRG/CHR ROM/RAM 信息

MVP 只实际使用：`mapper_id`、`prg_rom`、`chr_rom`、`mirroring`、`has_battery`、`has_trainer`。

### Mapper

统一接口：

- `cpu_read(address)` / `cpu_write(address, value)`
- `ppu_read(address)` / `ppu_write(address, value)`
- `observe_ppu_address(address)`
- `mirroring`
- `save_state()` / `load_state()`

实现顺序：

1. Mapper 0 / NROM
2. Mapper 2 / UxROM
3. Mapper 3 / CNROM
4. Mapper 1 / MMC1
5. Mapper 4 / MMC3

### CPU

职责：

- 6502 official opcode execution
- addressing modes
- status flags
- stack
- RESET / NMI / IRQ / BRK
- cycle counting
- CPU trace

关键要求：

- A/X/Y/SP/status 写入后 `& 0xFF`
- PC 写入后 `& 0xFFFF`
- 实现 branch extra cycle、page crossing extra cycle
- 实现 JMP indirect page-wrap bug
- zero-page wrap
- 2A03 decimal mode 行为
- trace 关闭时不得分配字符串或执行反汇编

### CPUBus

地址映射：

```text
$0000-$07FF  2 KiB CPU RAM
$0800-$1FFF  CPU RAM mirrors
$2000-$2007  PPU registers
$2008-$3FFF  PPU register mirrors
$4000-$4013  APU registers
$4014        OAM DMA
$4015        APU status
$4016        Controller 1 / strobe
$4017        Controller 2 / APU frame counter
$4018-$401F  disabled / test area
$4020-$5FFF  Cartridge expansion
$6000-$7FFF  PRG RAM
$8000-$FFFF  PRG ROM / Mapper
```

### PPU

职责：

- PPU registers
- VRAM address registers `v` / `t`
- `fine_x` / write toggle
- PPUDATA read buffer
- OAM
- background pipeline
- sprite rendering
- VBlank / NMI
- framebuffer output

MVP 推进策略：外部始终暴露 `clock()`，每次推进一个 PPU dot。内部可分阶段从简化渲染演进到逐 dot pipeline。

### PPUBus

地址映射：

```text
$0000-$1FFF  Pattern tables / CHR via Mapper
$2000-$2FFF  Nametables
$3000-$3EFF  Nametable mirror
$3F00-$3FFF  Palette RAM
```

必须支持 horizontal、vertical、four-screen、single-screen mirroring，并正确处理 `$3F10/$3F14/$3F18/$3F1C` palette mirrors。

### APU

MVP 使用静音 stub，保留寄存器读写与时钟接口。完整版本拆分为 pulse、triangle、noise、DMC、frame counter、mixer、resampler。

### Controller

标准手柄使用串行移位寄存器。按钮位：

```text
bit 0 A
bit 1 B
bit 2 Select
bit 3 Start
bit 4 Up
bit 5 Down
bit 6 Left
bit 7 Right
```

### Debug

调试能力建立在核心 API 上，不嵌入普通热路径。支持 step instruction、step CPU cycle、breakpoint、watchpoint、disassembler、trace export、PPU inspectors 等。

---

## Testing Architecture

测试是模拟器正确性的基础设施，应与功能同步建设。

### Unit Tests

- CPU: addressing modes、flags、ADC/SBC、branch cycles、page crossing、stack、interrupt、wraparound
- Bus: RAM mirror、PPU register mirror、controller、mapper range、DMA request
- PPU: register side effects、write toggle、PPUDATA buffer、palette mirror、nametable mirror、NMI
- Mapper: NROM 16/32 KiB、CHR ROM/RAM、后续 mapper bank switching

### Trace Tests

`nestest` 应作为 CPU 完成的关键验收：逐条指令比对 PC、opcode、operand、registers、status、SP、cycle、PPU scanline/dot。

### Test ROM Runner

统一 runner 应支持：

- 最大 frame 数
- 结果地址读取
- framebuffer hash
- RAM / mapper state hash
- 失败时报告首个差异点

### Regression

固定记录 ROM SHA256、执行 N 帧后的 CPU 状态、RAM hash、mapper state hash、framebuffer hash、audio hash。

---

## Performance Strategy

### 热点优先级

1. `PPU.clock`
2. PPU Bus read/write
3. CPU Bus read/write
4. CPU opcode execution
5. APU clock / mixer
6. framebuffer conversion

### Python 热路径原则

使用：

- `bytearray` / `bytes` / `list[int]`
- `__slots__`
- 局部变量
- 紧凑整数表
- 直接 `if/elif` 地址分派
- 预分配缓冲区

避免：

- 每条指令创建 dataclass
- 每像素创建 tuple
- 热循环使用 Enum
- 大量 property / Protocol 虚调用
- 事件总线
- 动态设备遍历
- 逐像素 pygame API
- 热路径日志

### 原生加速路线

```text
纯 Python 参考实现
  → trace / test ROM 正确
  → profiling
  → Cython/Rust 加速 PPU
  → 加速 Bus
  → 加速 CPU
  → 加速 APU
```

加速后端必须保持 `NESMachine` 对外 API 不变。

---

## Roadmap

### Phase 0: 项目骨架

- pyproject
- package layout
- errors
- region / timing
- empty machine
- headless runner
- logging
- CI

验收：项目可导入、空 machine 可创建、测试框架可运行。

### Phase 1: ROM 与 Mapper 0

- iNES 1.0 parser
- NES 2.0 检测
- `CartridgeImage`
- NROM
- CPU Bus / PPU Bus 基础映射

验收：header 单测、NROM 16/32 KiB 映射、CHR ROM/RAM 正确。

### Phase 2: CPU

- official opcode
- addressing modes
- interrupts
- cycle counting
- trace

验收：nestest trace 一致，CPU 单测通过。

### Phase 3: PPU 寄存器

- PPUCTRL / PPUMASK / PPUSTATUS
- PPUSCROLL / PPUADDR / PPUDATA
- nametable / palette mirroring
- VBlank / NMI

验收：PPU register test ROM 逐步通过。

### Phase 4: 背景渲染

- background tile fetch
- scroll
- palette
- framebuffer

验收：NROM 游戏能显示正确背景。

### Phase 5: 精灵、Controller、OAM DMA

- OAM
- sprite rendering
- sprite zero hit
- controller
- OAM DMA

验收：游戏可操作，主要精灵显示正确。

### Phase 6: Frontend

- pygame window
- input
- scaling
- frame pacing
- screenshot

验收：可稳定运行 NROM 游戏。

### Phase 7: APU

- pulse
- triangle
- noise
- frame counter
- mixer
- audio buffer
- DMC later

验收：APU test ROM 逐步通过，音频无明显断流。

### Phase 8: 更多 Mapper

顺序：2 → 3 → 1 → 4。

验收：每个 Mapper 有独立 ROM 测试，典型游戏回归通过。

### Phase 9: 性能优化

- profiler
- benchmark suite
- 减少对象分配
- 热路径扁平化
- 可选 Cython/Rust

验收：NROM 达到实时，复杂 Mapper 游戏接近或达到实时。

### Phase 10: 增强功能

- NES 2.0
- Save State
- debugger
- movie replay
- rewind
- Game Genie
- PAL / Dendy
- ZIP ROM
- mapper database

---

## Tradeoffs

| 决策 | 收益 | 代价 |
|---|---|---|
| 纯 Python 优先 | 可读、可测、易迭代 | 初期可能无法稳定 60 FPS |
| 指令级 CPU scheduler | 实现简单，利于 CPU trace | DMA / DMC / 中断边界后续需升级 |
| PPU 外部 dot-level clock | 保留精度演进空间 | PPU 实现复杂度较高 |
| Bus 直接地址判断 | 热路径快、符合硬件映射 | 扩展设备不如通用总线优雅 |
| Frontend 完全解耦 | 易 headless/CI/benchmark | 需要明确 framebuffer/audio 协议 |
| Save State 显式 schema | 可维护、可迁移 | 初期实现成本高于 pickle |
| Python 后端作为 oracle | 加速后可验证一致性 | 需要维护后端一致性测试 |

---

## Risks

### R-1: PPU 精度不足导致游戏兼容性差

缓解：先实现寄存器行为与测试 ROM，再推进背景、sprite、scroll、NMI 边界。

### R-2: CPU trace 与真实时序不一致

缓解：nestest 作为 CPU gate；trace 格式包含 CPU cycle 与 PPU scanline/dot。

### R-3: DMA 早期简化形成架构债

缓解：即使 MVP 也通过 Scheduler 持有 DMA state，避免在 bus write 中立即复制。

### R-4: Frontend 与核心耦合

缓解：核心不得 import pygame；所有真实时间节流放在 frontend。

### R-5: 性能优化过早破坏正确性

缓解：纯 Python 后端先通过 test ROM；Cython/Rust 只能替换经 profiling 证明的热点，并保留 API 与回归一致性测试。

### R-6: Mapper 扩展污染 CPU/PPU

缓解：所有 bank、IRQ、mirroring 运行时行为留在 Mapper；CPU/PPU 只通过 bus 与 mapper 交互。

---

## Next Step Recommendation

建议下一步进入 designer 阶段，基于本文档产出 Phase 0 与 Phase 1 的实现级设计，重点明确：

1. 当前项目包名与目录迁移方案；
2. `CartridgeImage`、iNES parser、NROM mapper 的接口；
3. `CPUBus` / `PPUBus` 最小可测试实现；
4. `NESMachine` 空组合根与 headless runner；
5. Phase 1 单元测试清单与验收标准。
