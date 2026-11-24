from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from chevuoi.application.usecases.exec_usecase import ExecOutcome, ExecUsecase
from chevuoi.domain.exceptions import ChevuoiError
from chevuoi.interfaces.cli.context import get_injector

logger = logging.getLogger(__name__)

# 終了コード。blocked は「動いたが止まった」なので失敗（1）と分ける
EXIT_DONE = 0
EXIT_ERROR = 1
EXIT_BLOCKED = 2


def exec_workflow(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="ワークフロー名（ディレクトリ名）")],
    brief: Annotated[
        Path,
        typer.Option("--brief", help="ワークフローに渡す方針（ブリーフ）のファイル"),
    ],
    json_path: Annotated[
        Path | None,
        typer.Option("--json", help="結果を JSON で書き出すファイル"),
    ] = None,
    test_commands: Annotated[
        list[str] | None,
        typer.Option(
            "--test-command",
            help="テストゲートのコマンド（複数指定可）。指定すると設定の test_commands より優先する",
        ),
    ] = None,
) -> None:
    """今いる作業ツリーでワークフローを 1 回実行する。

    worktree の作成・コミット・PR 作成は行わない。終了コードは done=0、blocked=2、エラー=1。
    """
    try:
        text = brief.read_text(encoding="utf-8")
    except OSError as e:
        _finish(ExecOutcome(status="error", workflow=name, error=f"ブリーフを読めません: {e}"), json_path)
        return
    if not text.strip():
        # 空の指示で長時間のワークフローを動かさない
        _finish(ExecOutcome(status="error", workflow=name, error=f"ブリーフが空です: {brief}"), json_path)
        return
    try:
        outcome = get_injector(ctx).get(ExecUsecase).execute(
            name, text, cwd=Path.cwd(), test_commands=test_commands or ()
        )
    except ChevuoiError as e:
        outcome = ExecOutcome(status="error", workflow=name, error=str(e))
    except Exception as e:  # noqa: BLE001 実行前の想定外の失敗も結果として返す
        logger.exception("vuoi exec failed: %s", name)
        outcome = ExecOutcome(status="error", workflow=name, error=f"{type(e).__name__}: {e}")
    _finish(outcome, json_path)


def _finish(outcome: ExecOutcome, json_path: Path | None) -> None:
    if json_path is not None:
        try:
            # 一時ファイルに書いてから置き換える。MCP の status は実行中にこのファイルを
            # 読むので、書きかけの JSON を見せない
            tmp = json_path.with_name(json_path.name + ".tmp")
            tmp.write_text(outcome.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, json_path)
        except OSError as e:
            print(f"結果の JSON を書き出せません: {e}", file=sys.stderr)
            _print(outcome)
            raise typer.Exit(code=EXIT_ERROR)
    _print(outcome)
    code = {"done": EXIT_DONE, "blocked": EXIT_BLOCKED}.get(outcome.status, EXIT_ERROR)
    if code != EXIT_DONE:
        raise typer.Exit(code=code)


def _print(outcome: ExecOutcome) -> None:
    """人間向けの要約。機械的に読むときは --json を使う。"""
    if outcome.status == "error":
        print(f"エラー: {outcome.error}", file=sys.stderr)
        return
    print(f"状態: {outcome.status}")
    print(f"作業ツリー: {outcome.workdir}")
    if outcome.blocked:
        print(f"blocked: {outcome.blocked}")
    if outcome.summary:
        print(f"\n{outcome.summary}")
    if outcome.changed_files:
        print(f"\n変更されたファイル ({len(outcome.changed_files)} 件):")
        for line in outcome.changed_files:
            print(f"  {line}")
    if outcome.proposals:
        print(f"\n申告された追加タスク ({len(outcome.proposals)} 件):")
        for p in outcome.proposals:
            print(f"- [{p.kind}] {p.title}")
