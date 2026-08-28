from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from chevuoi.domain.ports.workflow_loader import LoadedWorkflow


class ExecutionResult(BaseModel):
    """グラフ実行の結果。最終 state はワークフロー固有なので素通しする。"""

    output: str  # 最後のメッセージ本文（無ければ空文字）
    state: dict[str, Any]


class GraphExecutor(ABC):
    """コンパイル済みグラフの実行。実体（CompiledStateGraph）の操作は
    インフラ層に閉じ込める（設計ドキュメント: LoadedWorkflow.graph は不透明）。
    """

    @abstractmethod
    def execute(self, workflow: LoadedWorkflow, message: str) -> ExecutionResult: ...
