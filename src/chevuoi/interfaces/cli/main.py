from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from injector import Injector

from chevuoi.application.usecases.gc_usecase import GcUsecase
from chevuoi.application.usecases.run_usecase import RunUsecase
from chevuoi.infrastructure.config.settings import load_config
from chevuoi.interface.di_modules import AppModule

DEFAULT_CONFIG = Path.home() / ".config" / "vuoi" / "config.toml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vuoi")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Trello をポーリングして1巡")
    gc = sub.add_parser("gc", help="終端済み worktree の掃除")
    gc.add_argument("--older-than", type=int, default=7, help="経過日数（既定: 7）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    config = load_config(args.config)
    injector = Injector([AppModule(config)])
    match args.command:
        case "run":
            injector.get(RunUsecase).execute()
        case "gc":
            injector.get(GcUsecase).execute(older_than_days=args.older_than)
    return 0


if __name__ == "__main__":
    sys.exit(main())
