from __future__ import annotations

from pathlib import Path

import typer
from injector import Binder, Module, singleton

from chevuoi.domain.ports.run_launcher import RunLauncher
from chevuoi.infrastructure.runs.subprocess_run_launcher import SubprocessRunLauncher
from chevuoi.interfaces.cli.context import get_injector

CONFIG_PATH_KEY = "config_path"


class McpModule(Module):
    """MCP サーバーだけが使う口。起動する vuoi exec に同じ設定ファイルを渡す。"""

    def __init__(self, config_path: Path) -> None:
        # 子プロセスは作業ツリーを cwd にして動くので、相対パスのままだと解決先がずれる
        self._config_path = config_path.expanduser().resolve()

    def configure(self, binder: Binder) -> None:
        # 起動した子プロセスを保持して回収するため singleton
        binder.bind(
            RunLauncher,  # type: ignore[type-abstract]
            to=SubprocessRunLauncher(self._config_path),
            scope=singleton,
        )


def serve(ctx: typer.Context) -> None:
    """MCP サーバーを stdio で起動する。Claude Code には `claude mcp add vuoi -- vuoi mcp` で登録する。"""
    # MCP SDK の読み込みは重いので、このコマンドを使うときだけ読む
    from chevuoi.application.usecases.exec_run_usecase import ExecRunUsecase
    from chevuoi.interfaces.mcp.server import build_server

    injector = get_injector(ctx).create_child_injector(
        [McpModule(ctx.meta[CONFIG_PATH_KEY])]
    )
    build_server(injector.get(ExecRunUsecase)).run("stdio")
