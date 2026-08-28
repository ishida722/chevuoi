from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.routing_decision import RoutingDecision
from chevuoi.domain.entities.workflow_meta import WorkflowMeta


class WorkflowRouter(ABC):
    """カードの内容から候補ワークフローを 1 つ選ぶ（曖昧マッチ）。

    triage 仕様の第 2 層に相当し、LLM を使ってよい唯一の判断。
    必ず棄権（workflow=None）を返せること。例外は投げず棄権で表現する。
    """

    @abstractmethod
    def route(
        self, card: Card, candidates: list[WorkflowMeta], *, cwd: Path | None = None
    ) -> RoutingDecision: ...
