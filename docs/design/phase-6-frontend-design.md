# Phase 6：Pygame Frontend 详细实现设计

## Summary

Phase 0–5 已完成核心模拟器：NESMachine 可正确执行 NROM 游戏，输出 256×240 palette-index framebuffer，支持单手柄输入。Phase 6 在已有核心上构建 pygame 图形前端，添加窗口、缩放、输入映射、帧节流和截图能力。

---

## Modules Affected

| Module | Action |
|--------|--------|
| `src/simplenes/frontend/protocol.py` | **不变** — Frontend protocol 已达预期 |
| `src/simplenes/frontend/headless.py` | **不变** — headless frontend 已可用 |
| `src/simplenes/frontend/palette.py` | **新建** — NES 2C02 palette RGB table（64 色）；纯函数 `framebuffer_to_rgba()` |
| `src/simplenes/frontend/pygame_frontend.py` | **新建** — PygameFrontend + 主循环 `run()`；全部 pygame import lazy |
| `src/simplenes/frontend/__init__.py` | **修改** — 暴露 palette、`framebuffer_to_rgba` |
| `src/simplenes/cli.py` | **修改** — 实现 CLI 参数解析与 frontend 启动 |
| `pyproject.toml` | **修改** — 新增 `frontend` optional-dependencies（pygame） |
| `tests/unit/test_frontend.py` | **新建** — palette + HeadlessFrontend 测试（永远运行） |
| `tests/unit/test_pygame_frontend.py` | **新建** — pygame-only 测试（自动 skip when missing） |

---

## Architecture Decisions

### AD-6.1: pygame 作为 optional dependency

核心模拟器零外部依赖。pygame 作为 `[project.optional-dependencies]` 中的 `frontend` 组引入：

```toml
[project.optional-dependencies]
frontend = ["pygame>=2"]
```

Frontend 模块的 **全部** pygame import 使用 lazy import（在函数或方法内部 `import pygame`），确保没有 pygame 环境时核心仍然可导入。模块顶层 **不得出现任何 `import pygame` 或引用 `pygame.K_*` 等常量**。

### AD-6.2: Frontend Protocol 保持不变

已有 `Frontend` Protocol：

```python
class Frontend(Protocol):
    def should_close(self) -> bool: ...
    def poll_input(self) -> int: ...
    def present(self, framebuffer: memoryview) -> None: ...
    def close(self) -> None: ...
```

`PygameFrontend` 实现同一 protocol。主循环（`run` 函数）通过 `PygameFrontend` 公开 API 操作，不访问私有字段。

### AD-6.3: 主循环放在 pygame_frontend.py

主循环 `run(machine, frontend, *, fps=60)` 位于 `pygame_frontend.py`，不放置在 `machine.py`。machine 保持纯核心职责。headless 场景自己驱动循环（如 `run_frame()` 或 `step_instruction()`）。

### AD-6.4: Framebuffer → 屏幕采用 palette table + integer scaling

PPU framebuffer 是 `bytearray(256×240)` palette indices。流程：

```
framebuffer → framebuffer_to_rgba() → bytes RGBA
              → pygame.image.frombuffer → Surface(256,240)
              → pygame.transform.scale → Surface(256*scale,240*scale)
              → screen.blit → pygame.display.flip()
```

**不使用 `pygame.surfarray`**——需要 numpy 依赖。采用 `pygame.image.frombuffer()` 从预构建的 RGBA bytes 创建 Surface。

### AD-6.5: Palette 使用标准 2C02 NTSC palette

64 种 palette colors × 8 bits per channel。提供 `PALETTE_RGB: list[tuple[int,int,int]]`（长度为 64）。`palette_index → RGB` 通过 `palette_index & 0x3F` 查询。

### AD-6.6: 纯函数 `framebuffer_to_rgba()`

抽出纯函数，不依赖 pygame：

```python
def framebuffer_to_rgba(framebuffer: memoryview) -> bytes:
    """Convert palette-index framebuffer (256×240) to RGBA bytes (256×240×4)."""
    buf = bytearray(256 * 240 * 4)
    for i in range(256 * 240):
        idx = framebuffer[i] & 0x3F
        r, g, b = PALETTE_RGB[idx]
        offset = i * 4
        buf[offset]     = r
        buf[offset + 1] = g
        buf[offset + 2] = b
        buf[offset + 3] = 255
    return bytes(buf)
```

好处：

- `present()` 和 `take_screenshot()` 复用同一转换逻辑
- 不依赖 pygame，可独立单测
- 截图通过 `pygame.image.fromstring(rgba, (256,240), "RGBA")` 创建 Surface

### AD-6.7: 输入映射

单手柄（controller 1）默认 keymap。**Keymap 在 `__init__` 内部构建，不引用模块顶层 `pygame.K_*` 常量**：

```python
@staticmethod
def _build_keymap(pg):
    """Build default keymap dict from pygame key constants."""
    return {
        pg.K_z:      0x01,  # A
        pg.K_x:      0x02,  # B
        pg.K_RSHIFT: 0x04,  # Select
        pg.K_RETURN: 0x08,  # Start
        pg.K_UP:     0x10,  # Up
        pg.K_DOWN:   0x20,  # Down
        pg.K_LEFT:   0x40,  # Left
        pg.K_RIGHT:  0x80,  # Right
    }
```

Controller 2 暂不映射（`set_controller_state(2, 0)`）。

### AD-6.8: 截图功能

按 `F12` → `PygameFrontend.process_events()` 检测事件，调用 `take_screenshot()` 保存当前 framebuffer 为 PNG。文件名使用日期时间戳：`screenshot-2026-07-05-120000.png`。转换通过 `framebuffer_to_rgba()` → `pygame.image.fromstring` → `pygame.image.save` 完成。

### AD-6.9: 事件处理公开 API

Runner 不直接访问 pygame event loop 或 frontend 私有字段。所有 pygame-specific 事件处理封装在：

```python
class PygameFrontend:
    def process_events(self, framebuffer: memoryview) -> None:
        """Process pygame events: QUIT, F12 screenshot."""
        ...
```

### AD-6.10: `close()` 幂等、主循环安全退出

`process_events()` 收到 QUIT 后调用 `close()`，但本轮循环不能继续访问已 quit 的 pygame。因此：

- `close()` 先检查 `_should_close`，已关闭则直接返回，避免重复 `pygame.quit()`
- `run()` 在 `process_events()` 后立即检查 `should_close()` 并 break

---

## Data Model Changes

### 1. `src/simplenes/frontend/palette.py` (新建)

```python
"""NES 2C02 NTSC palette — 64 RGB colours + framebuffer conversion."""

PALETTE_RGB: list[tuple[int, int, int]] = [
    (84, 84, 84),     # $00
    (0, 30, 116),      # $01
    (8, 16, 144),      # $02
    # ... 64 entries total ...
    (252, 252, 252),   # $3F
]


def palette_to_rgb(index: int) -> tuple[int, int, int]:
    """Map palette index (0–63) to 8-bit RGB tuple."""
    return PALETTE_RGB[index & 0x3F]


def framebuffer_to_rgba(framebuffer: memoryview) -> bytes:
    """Convert palette-index framebuffer (256×240) to RGBA bytes (256×240×4).
    Input must be at least 61440 bytes (256×240).
    """
    buf = bytearray(256 * 240 * 4)
    for i in range(256 * 240):
        r, g, b = PALETTE_RGB[framebuffer[i] & 0x3F]
        offset = i * 4
        buf[offset]     = r
        buf[offset + 1] = g
        buf[offset + 2] = b
        buf[offset + 3] = 255
    return bytes(buf)
```

### 2. `src/simplenes/frontend/pygame_frontend.py` (新建)

全部 `import pygame` 放在函数/方法内部。模块顶层不得引用 pygame。

```python
"""Pygame frontend — window, input, scaling, frame pacing, screenshot."""

from __future__ import annotations

from datetime import datetime
from simplenes.frontend.palette import framebuffer_to_rgba
from simplenes.machine import NESMachine


class PygameFrontend:
    __slots__ = (
        "_scale", "_title", "_should_close",
        "_pg",           # lazy-loaded pygame module reference
        "_screen",       # pygame.Surface
        "_clock",        # pygame.time.Clock
        "_keymap",       # dict[int, int] — pygame keycode → NES button bit
    )

    def __init__(self, *, scale: int = 2, title: str = "simpleNES") -> None:
        if not isinstance(scale, int) or not (1 <= scale <= 4):
            raise ValueError(f"scale must be 1-4, got {scale}")
        self._scale = scale
        self._title = title
        self._should_close = False

        import pygame
        self._pg = pygame
        self._pg.init()
        self._pg.display.set_caption(title)
        self._screen = self._pg.display.set_mode(
            (256 * scale, 240 * scale)
        )
        self._clock = self._pg.time.Clock()
        self._keymap = self._build_keymap(self._pg)

    # ----------------------------------------------------------------
    # Protocol methods
    # ----------------------------------------------------------------

    def should_close(self) -> bool:
        return self._should_close

    def poll_input(self) -> int:
        keys = self._pg.key.get_pressed()
        state = 0
        for keycode, bit in self._keymap.items():
            if keys[keycode]:
                state |= bit
        return state

    def present(self, framebuffer: memoryview) -> None:
        rgba = framebuffer_to_rgba(framebuffer)
        surface = self._pg.image.frombuffer(rgba, (256, 240), "RGBA")
        if self._scale != 1:
            surface = self._pg.transform.scale(
                surface, (256 * self._scale, 240 * self._scale)
            )
        self._screen.blit(surface, (0, 0))
        self._pg.display.flip()

    def close(self) -> None:
        """Idempotent close — does nothing if already closed."""
        if self._should_close:
            return
        self._should_close = True
        self._pg.quit()

    # ----------------------------------------------------------------
    # Event handling (public — called by runner)
    # ----------------------------------------------------------------

    def process_events(self, framebuffer: memoryview) -> None:
        """Process pygame events: QUIT closes window; F12 takes screenshot."""
        for event in self._pg.event.get():
            if event.type == self._pg.QUIT:
                self.close()
            elif event.type == self._pg.KEYDOWN:
                if event.key == self._pg.K_F12:
                    self.take_screenshot(framebuffer)

    def tick(self, fps: int = 60) -> None:
        """Frame pacing: limit to `fps` frames per second."""
        self._clock.tick(fps)

    # ----------------------------------------------------------------
    # Screenshot
    # ----------------------------------------------------------------

    def take_screenshot(self, framebuffer: memoryview) -> None:
        """Save current framebuffer as a PNG screenshot."""
        rgba = framebuffer_to_rgba(framebuffer)
        surface = self._pg.image.fromstring(rgba, (256, 240), "RGBA")
        filename = datetime.now().strftime("screenshot-%Y-%m-%d-%H%M%S.png")
        self._pg.image.save(surface, filename)

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------

    @staticmethod
    def _build_keymap(pg) -> dict:
        return {
            pg.K_z:      0x01,   # A
            pg.K_x:      0x02,   # B
            pg.K_RSHIFT: 0x04,   # Select
            pg.K_RETURN: 0x08,   # Start
            pg.K_UP:     0x10,   # Up
            pg.K_DOWN:   0x20,   # Down
            pg.K_LEFT:   0x40,   # Left
            pg.K_RIGHT:  0x80,   # Right
        }


def run(machine: NESMachine, frontend: PygameFrontend, *, fps: int = 60) -> None:
    """Main emulation loop: input → emulate → present → pace.

    Stops when frontend.should_close() returns True (window closed, etc.).
    """
    while not frontend.should_close():
        frontend.process_events(machine.framebuffer)
        if frontend.should_close():
            break

        state1 = frontend.poll_input()
        machine.set_controller_state(1, state1)
        machine.set_controller_state(2, 0)

        machine.run_frame()
        frontend.present(machine.framebuffer)
        frontend.tick(fps)
```

**关键变更**（相比最初版）：
- 移除未使用的 `from collections.abc import Callable`
- `close()` 幂等：先检查 `_should_close`，避免重复 `pygame.quit()`
- `run()` 在 `process_events()` 后立即 `break`

### 3. `src/simplenes/frontend/__init__.py` (修改)

```python
"""NES emulator frontends.

HeadlessFrontend is always available.
PygameFrontend requires pygame (pip install simplenes-py[frontend]).
"""

from simplenes.frontend.protocol import Frontend
from simplenes.frontend.headless import HeadlessFrontend
from simplenes.frontend.palette import PALETTE_RGB, framebuffer_to_rgba, palette_to_rgb

__all__ = [
    "Frontend",
    "HeadlessFrontend",
    "PALETTE_RGB",
    "framebuffer_to_rgba",
    "palette_to_rgb",
]
```

`PygameFrontend` 不在 `__init__` 顶层 import —— 需要显式 `from simplenes.frontend.pygame_frontend import PygameFrontend, run`。

---

## Control Flow

```
main (cli.py)
  → load_rom(rom_path)                        # iNES parse → CartridgeImage
  → create NESMachine(cartridge)              # composition root
  → create PygameFrontend(scale=2)            # lazy import pygame (may fail here)
  → run(machine, frontend)                    # main loop
      ┌─ while not frontend.should_close():
      │    ├─ frontend.process_events(machine.framebuffer)
      │    │     └─ QUIT → close() / F12 → take_screenshot()
      │    ├─ if should_close(): break        ← prevent accessing quit pygame
      │    ├─ state = frontend.poll_input()
      │    ├─ machine.set_controller_state(1, state)
      │    ├─ machine.run_frame()
      │    ├─ frontend.present(machine.framebuffer)
      │    │     └─ framebuffer_to_rgba() → frombuffer → scale → blit → flip
      │    └─ frontend.tick(60)
      └─ frontend.close()                     (idempotent, no-op if already quit)
```

---

## CLI 设计

```text
simplenes run <rom.nes> [--scale 2] [--fps 60] [--title "simpleNES"]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `rom.nes` | 必需 | iNES ROM 文件路径 |
| `--scale` | 2 | 缩放倍率（1/2/3/4） |
| `--fps` | 60 | 目标帧率 |
| `--title` | "simpleNES" | 窗口标题 |

```python
# src/simplenes/cli.py (关键骨架)

import argparse
import sys

from simplenes.cartridge.ines import RomParser
from simplenes.errors import InvalidRomError, UnsupportedMapperError
from simplenes.machine import NESMachine


def main() -> None:
    parser = argparse.ArgumentParser("simplenes")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run a ROM with the pygame frontend")
    run_cmd.add_argument("rom", help="Path to iNES ROM file (.nes)")
    run_cmd.add_argument("--scale", type=int, default=2,
                         help="Window scale factor (1-4)")
    run_cmd.add_argument("--fps", type=int, default=60,
                         help="Target frames per second")
    run_cmd.add_argument("--title", default="simpleNES", help="Window title")

    args = parser.parse_args()

    if args.command == "run":
        _run_game(args.rom, args.scale, args.fps, args.title)


def _run_game(rom_path: str, scale: int, fps: int, title: str) -> None:
    # ----- ROM loading (core-only, no pygame needed) -----
    try:
        with open(rom_path, "rb") as f:
            data = f.read()
    except FileNotFoundError:
        sys.exit(f"ROM file not found: {rom_path}")

    try:
        image = RomParser.parse(data)
        machine = NESMachine(image)
    except (InvalidRomError, UnsupportedMapperError) as e:
        sys.exit(f"Error: {e}")

    # ----- Pygame frontend (lazy import) -----
    try:
        from simplenes.frontend.pygame_frontend import PygameFrontend, run
        frontend = PygameFrontend(scale=scale, title=title)
    except ModuleNotFoundError as e:
        if e.name == "pygame":
            sys.exit(
                "pygame is required for the graphical frontend.\n"
                "Install with: pip install simplenes-py[frontend]"
            )
        raise

    # ----- Main loop -----
    try:
        run(machine, frontend, fps=fps)
    except KeyboardInterrupt:
        pass
    finally:
        frontend.close()
```

**关键变更**（相比最初版）：`PygameFrontend(...)` 构造一起放入 `try/except ModuleNotFoundError`，因为真实的 `import pygame` 发生在 `__init__()` 内部。只捕获 `name == "pygame"`，避免吞掉其他 import bug。

---

## Edge Cases

| 场景 | 行为 |
|------|------|
| pygame 未安装 | CLI: `ModuleNotFoundError(name="pygame")` → 友好错误；核心模块 import 不受影响 |
| ROM 非法 | 解析阶段失败，未起窗口 |
| 窗口关闭按钮 | `process_events` 检测 `QUIT` → `close()` → `should_close()` → `break` → 退出 |
| QUIT 后同帧不继续执行 | `run()` 在 `process_events()` 后立即 `break` |
| `close()` 多次调用 | 幂等：第二次不重复 `pygame.quit()` |
| F12 截图 | `process_events` 检测 `KEYDOWN F12` → `take_screenshot(framebuffer)` → 保存 PNG |
| 按键持续按下 | `pygame.key.get_pressed()` 每帧读取，支持持续输入 |
| scale 非法 | `PygameFrontend.__init__` 报 `ValueError`（1-4 范围） |
| 极小缩放 (scale=1) | 256×240 原始窗口，不放大 |
| 多事件积压 | `process_events` 用 `for event in ...` 全部处理 |
| `framebuffer_to_rgba` 索引越界 | `idx & 0x3F` 确保 0–63 |
| 未安装 pygame 时 import `frontend` | palette 和 headless 可用；pygame_frontend 需显式 import 时失败 |
| 输入 framebuffer 长度不足 | `framebuffer_to_rgba` 文档声明要求 ≥ 61440 bytes |

---

## Non-Goals (Phase 6)

- 多手柄映射（controller 2 keymap）
- 全屏模式
- 窗口 resize
- VSync 关闭
- 帧率不限速模式
- 音频输出（Phase 7）
- 菜单栏 / UI overlay
- Game Genie
- Save state
- 自定义 keymap 配置
- PAL palette（MVP 仅 NTSC）

---

## Tests

### 测试文件拆分

为隔离 pygame 依赖，拆成两个文件：

| 文件 | 内容 | pyafe 环境行为 |
|------|------|---------------|
| `tests/unit/test_frontend.py` | palette（5）+ HeadlessFrontend（6） | 永远运行 |
| `tests/unit/test_pygame_frontend.py` | PygameFrontend（4） | 无 pygame 时自动 skip |

### `tests/unit/test_frontend.py`

```python
"""Unit tests for palette + HeadlessFrontend.  Always runs, no pygame needed."""

from simplenes.frontend.palette import (
    PALETTE_RGB, palette_to_rgb, framebuffer_to_rgba,
)
from simplenes.frontend.headless import HeadlessFrontend


# ======================================================================
# Palette
# ======================================================================

def test_palette_length_64():
    assert len(PALETTE_RGB) == 64

def test_palette_index_wraparound():
    assert palette_to_rgb(0) == palette_to_rgb(64)
    assert palette_to_rgb(1) == palette_to_rgb(65)

def test_palette_rgb_range():
    for r, g, b in PALETTE_RGB:
        assert 0 <= r <= 255
        assert 0 <= g <= 255
        assert 0 <= b <= 255

def test_framebuffer_to_rgba_length():
    fb = memoryview(bytearray(256 * 240))
    rgba = framebuffer_to_rgba(fb)
    assert len(rgba) == 256 * 240 * 4

def test_framebuffer_to_rgba_alpha_opaque():
    fb = memoryview(bytearray(256 * 240))
    rgba = framebuffer_to_rgba(fb)
    for i in range(0, len(rgba), 4):
        assert rgba[i + 3] == 255


# ======================================================================
# HeadlessFrontend
# ======================================================================

def test_headless_initially_not_closed():
    frontend = HeadlessFrontend()
    assert not frontend.should_close()

def test_headless_poll_input_defaults_zero():
    frontend = HeadlessFrontend()
    assert frontend.poll_input() == 0

def test_headless_set_input_roundtrips():
    frontend = HeadlessFrontend()
    frontend.set_input(0xAB)
    assert frontend.poll_input() == 0xAB

def test_headless_present_does_not_raise():
    frontend = HeadlessFrontend()
    fb = memoryview(bytearray(256 * 240))
    frontend.present(fb)

def test_headless_stop_sets_should_close():
    frontend = HeadlessFrontend()
    frontend.stop()
    assert frontend.should_close()

def test_headless_close_sets_should_close():
    frontend = HeadlessFrontend()
    frontend.close()
    assert frontend.should_close()
```

### `tests/unit/test_pygame_frontend.py`

```python
"""Unit tests for PygameFrontend.  Skipped if pygame is not installed."""

import os
import pytest

# Must set dummy driver BEFORE importing pygame
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

pygame = pytest.importorskip("pygame", reason="pygame not installed")

from simplenes.frontend.pygame_frontend import PygameFrontend


def test_pygame_frontend_rejects_invalid_scale():
    with pytest.raises(ValueError):
        PygameFrontend(scale=0)
    with pytest.raises(ValueError):
        PygameFrontend(scale=5)


def test_pygame_frontend_initially_not_closed():
    frontend = PygameFrontend(scale=1)
    try:
        assert not frontend.should_close()
    finally:
        frontend.close()


def test_pygame_frontend_close_sets_flag():
    frontend = PygameFrontend(scale=1)
    frontend.close()
    assert frontend.should_close()
    # idempotent: second call does not raise
    frontend.close()


def test_present_constructs_correct_rgba_buffer(monkeypatch):
    frontend = PygameFrontend(scale=1)
    try:
        fb = memoryview(bytearray(256 * 240))

        called_args = {}

        def fake_frombuffer(rgba_bytes, size, format):
            called_args["size"] = size
            called_args["format"] = format
            called_args["len"] = len(rgba_bytes)
            return pygame.Surface(size)

        monkeypatch.setattr(frontend._pg.image, "frombuffer", fake_frombuffer)
        monkeypatch.setattr(frontend._pg.display, "flip", lambda: None)
        monkeypatch.setattr(frontend._pg.transform, "scale",
                            lambda s, sz: s)

        frontend.present(fb)
        assert called_args["size"] == (256, 240)
        assert called_args["format"] == "RGBA"
        assert called_args["len"] == 256 * 240 * 4
    finally:
        frontend.close()
```

**测试设计要点**：

- 两文件拆分：palette/headless tests 不受 pygame 安装状态影响，永远运行。
- `test_pygame_frontend.py` 用 `pytest.importorskip("pygame")` 在模块顶部 skip，只影响这 4 个 test。
- `os.environ.setdefault("SDL_VIDEODRIVER", "dummy")` 在 `importorskip` 之前。
- `fake_frombuffer()` 返回 `pygame.Surface(size)`，确保后续 `blit` 不接收 `None`。
- 不使用 `pytest-mock`（`mocker` fixture）、`unittest.mock` 等额外依赖——仅用 pytest 内置 `monkeypatch`。
- 每轮 pygame test 创建 frontend 后 `finally: close()` 避免残留窗口。

---

## Implementation Plan

### Step 1: 添加 pygame 依赖
- `pyproject.toml`: 添加 `[project.optional-dependencies]` → `frontend = ["pygame>=2"]`
- CI 需 `uv sync --extra frontend` 安装 pygame 运行前端测试

### Step 2: 创建 `palette.py`
- 嵌入标准 2C02 NTSC palette 64 色 RGB
- `palette_to_rgb(index)` 纯函数
- `framebuffer_to_rgba(framebuffer)` 纯函数

### Step 3: 创建 `pygame_frontend.py`
- `PygameFrontend` 类：全 lazy pygame import（`import pygame` 在 `__init__` 内）
- `_build_keymap(pg)` 静态方法
- `should_close()` / `poll_input()` / `present()` / `close()` — protocol 方法
- `close()` 幂等
- `process_events(framebuffer)` — QUIT + F12 截图
- `tick(fps)` — 帧节流
- `take_screenshot(framebuffer)` — PNG 保存
- `run(machine, frontend, *, fps)` — 主循环（含 `should_close()` break）

### Step 4: 更新 `frontend/__init__.py`
- 添加 `PALETTE_RGB`、`palette_to_rgb`、`framebuffer_to_rgba` 到 exports

### Step 5: 更新 `cli.py`
- `argparse` 子命令 `run`
- ROM 加载 + NESMachine 创建
- PygameFrontend 构造放入 `try/except ModuleNotFoundError(name="pygame")`
- `run()` 主循环

### Step 6: 编写测试
- `tests/unit/test_frontend.py`：palette（5）+ HeadlessFrontend（6）——永远运行
- `tests/unit/test_pygame_frontend.py`：PygameFrontend（4）——`importorskip("pygame")` + `monkeypatch`

### Step 7: 回归验证
```bash
uv run ruff check src/ tests/
uv run pytest tests/ -q                         # 无 pygame 时 skip 4，其余 pass
uv run pytest tests/ -q --no-header 2>&1 | grep -c "passed"  # 验证总数
```

---

## Risks / Open Questions

### R-6.1: Pygame 在 CI 无 display 环境
**缓解**：`os.environ.setdefault("SDL_VIDEODRIVER", "dummy")` 在 `pytest.importorskip("pygame")` **之前**设置。pygame 在 dummy driver 下无需 X11/Wayland/Windows GUI。`pytest.importorskip("pygame")` 确保无 pygame 时跳过而非失败。测试文件拆分后即使 skip 也不影响 palette/headless 测试。

### R-6.2: `framebuffer_to_rgba` 性能
Python 循环逐像素写 RGBA buffer 在纯 Python 下有可观测开销。256×240=61,440 pixels × 4 bytes = ~245 KB per frame。**缓解**：Phase 6 先实现正确性；Phase 9 profiling 后针对性优化（pre-alloc buffer reuse、Cython 等）。

### R-6.3: 帧节流精度
`pygame.time.Clock.tick(60)` 已足够稳定。NTSC 真实帧率 ≈ 60.0988 Hz。**缓解**：0.1 Hz 偏差对 NROM 游戏不可察觉。

### R-6.4: Palette 来源一致性
不同 NES emulator 使用略微不同的 NTSC palette。**缓解**：使用广泛验证的 palette（如 FCEUX 或 Nestopia）；future phase 可支持外部 palette 文件。

---

## Verification Criteria

1. `uv run ruff check src/ tests/` 零警告
2. 现有 189 个测试全部通过（不含 pygame 时 skip 4 个 pygame tests）
3. 新增 `test_frontend.py` 11 个全部通过（palette 5 + headless 6）
4. 新增 `test_pygame_frontend.py` 4 个全部通过（有 pygame 时），否则 skip
5. `simplenes run <rom.nes>` 启动 pygame 窗口
6. 窗口正确显示游戏画面（NROM 游戏如 Donkey Kong）
7. 按键操作响应正确
8. F12 截图保存为 PNG
9. 关闭窗口正常退出（不报错、不残留进程）
10. 未安装 pygame 时 CLI 报清晰错误："pip install simplenes-py[frontend]"
11. `import simplenes.frontend` 在无 pygame 环境下不报错（可 import palette + headless）

