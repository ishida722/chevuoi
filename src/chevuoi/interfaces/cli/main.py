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


def setup_file_logging(log_file: Path) -> None:
    """時刻付きフォーマットでログをファイルにも残す。

    カードごとの処理開始・終了時刻を後から追えるようにするため、
    標準エラーとは別にファイルへ追記する。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)


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
    # httpx の INFO ログはリクエスト URL（key/token の認証クエリ）を出すため抑止する
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = parse_args(argv)
    config = load_config(args.config)
    setup_file_logging(config.log_file)
    injector = Injector([AppModule(config)])
    match args.command:
        case "run":
            injector.get(RunUsecase).execute()
        case "gc":
            injector.get(GcUsecase).execute(older_than_days=args.older_than)
    return 0


if __name__ == "__main__":
    sys.exit(main())
