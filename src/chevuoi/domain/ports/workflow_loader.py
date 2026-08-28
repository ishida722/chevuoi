from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict

from chevuoi.domain.entities.workflow_meta import WorkflowMeta
from chevuoi.domain.value_objects.workflow_name import WorkflowName


class LoadedWorkflow(BaseModel):
    """コンパイル済みグラフの不透明ハンドル。

    実体（CompiledStateGraph）の型はインフラ層だけが知る。
    ドメイン・アプリケーションは graph を素通しするだけ。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: WorkflowName
    graph: Any


class LoadFailure(BaseModel):
    name: WorkflowName
    traceback: str  # 生の traceback（整形しない。仕様 §10）


class WorkflowLoader(ABC):
    """1 つのワークフローを import → build → compile する。

    例外を投げず、失敗は LoadFailure で返す。KeyboardInterrupt のみ再送出。
    """

    @abstractmethod
    def load(self, meta: WorkflowMeta) -> LoadedWorkflow | LoadFailure: ...
