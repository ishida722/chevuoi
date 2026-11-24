from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class RunFiles(BaseModel):
    """1 回の実行に付随するファイル。どれも作業ツリーの外に置く。"""

    model_config = ConfigDict(frozen=True)

    brief: Path  # ワークフローに渡すブリーフ
    result: Path  # vuoi exec が書き出す結果の JSON
    log: Path  # vuoi exec の標準出力・標準エラー


class ExecRun(BaseModel):
    """バックグラウンドで起動した vuoi exec の記録（MCP の start / status 用）。

    状態（実行中・終了）は持たない。状態はプロセスの生存と結果ファイルから
    その都度求める（記録を書き換えるのは pid の記入と取り消しのときだけ）。
    """

    run_id: str
    workflow: str
    workdir: Path  # 作業ツリーのルート
    started_at: datetime
    test_commands: list[str] = []
    changed_before: list[str] = []  # 開始時点の git status --porcelain
    pid: int | None = None
    cancelled_at: datetime | None = None
