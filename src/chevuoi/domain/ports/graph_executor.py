from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from chevuoi.domain.entities.project import Project
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow


class ExecutionResult(BaseModel):
    """グラフ実行の結果。最終 state はワークフロー固有なので素通しする。

    blocked / summary はワークフロー契約の推奨キー（state["blocked"] / state["result"]）。
    ホストの終端処理はこの 2 つと差分の有無だけを見る。
    """

    output: str  # グラフが追加した最後のメッセージ本文（無ければ空文字）
    state: dict[str, Any]
    blocked: str = ""
    summary: str = ""


class GraphExecutor(ABC):
    """コンパイル済みグラフの実行。実体（CompiledStateGraph）の操作は
    インフラ層に閉じ込める（設計ドキュメント: LoadedWorkflow.graph は不透明）。
    """

    @abstractmethod
    def execute(
        self,
        workflow: LoadedWorkflow,
        message: str,
        *,
        workdir: Path | None = None,
        project: Project | None = None,
    ) -> ExecutionResult: ...
