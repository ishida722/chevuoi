from __future__ import annotations

import functools
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer
from injector import Injector

from chevuoi.application.usecases.gc_usecase import GcUsecase
from chevuoi.application.usecases.run_usecase import RunUsecase
from chevuoi.infrastructure.config.settings import load_config
from chevuoi.interface.di_modules import AppModule
from chevuoi.interfaces.cli.commands import card, workflow
from chevuoi.interfaces.cli.context import get_injector

DEFAULT_CONFIG = Path.home() / ".config" / "vuoi" / "config.toml"
# 標準エラー・ログファイル共通の書式。行頭に日付時刻を付けて後から追えるようにする
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

app = typer.Typer(
    name="vuoi",
    help="Trello 駆動の開発ループ vuoi",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
app.add_typer(workflow.app, name="workflow")
app.add_typer(card.app, name="card")


def setup_file_logging(log_file: Path) -> None:
    """時刻付きフォーマットでログをファイルにも残す。

    カードごとの処理開始・終了時刻を後から追えるようにするため、
    標準エラーとは別にファイルへ追記する。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)


def build_injector(config_path: Path) -> Injector:
    """設定を読み、ファイルログを有効にしてから Injector を組み立てる。"""
    config = load_config(config_path)
    setup_file_logging(config.log_file)
    return Injector([AppModule(config)])


@app.callback()
def setup(
    ctx: typer.Context,
    config: Annotated[
        Path, typer.Option("--config", help="設定ファイルのパス")
    ] = DEFAULT_CONFIG,
) -> None:
    """全サブコマンド共通の初期化。

    ここでは設定ファイルを読まず、Injector のファクトリだけを ctx.obj に載せる。
    実際の構築は各コマンドが get_injector() を呼んだ時点で行う。
    """
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    # httpx の INFO ログはリクエスト URL（key/token の認証クエリ）を出すため抑止する
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ctx.obj = functools.partial(build_injector, config)


@app.command("run", help="Trello をポーリングして1巡")
def run(ctx: typer.Context) -> None:
    get_injector(ctx).get(RunUsecase).execute()


@app.command("gc", help="終端済み worktree の掃除")
def gc(
    ctx: typer.Context,
    older_than: Annotated[int, typer.Option("--older-than", help="経過日数（既定: 7）")] = 7,
) -> None:
    get_injector(ctx).get(GcUsecase).execute(older_than_days=older_than)


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。typer は常に SystemExit で終わるので、終了コードに変換して返す。"""
    try:
        app(args=argv, prog_name="vuoi")
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
