"""CLI entry point."""

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
