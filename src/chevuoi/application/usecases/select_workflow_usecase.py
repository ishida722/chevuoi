from __future__ import annotations

import logging
import re
from pathlib import Path

from injector import inject

from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.routing_decision import RoutingDecision
from chevuoi.domain.entities.workflow_meta import WorkflowMeta
from chevuoi.domain.ports.workflow_router import WorkflowRouter

logger = logging.getLogger(__name__)


class SelectWorkflowUsecase:
    """カードに適用するワークフローを 3 層で決める（triage 仕様）。

    1. 決定的: タイトルに [intent] マーカーがあれば by_intent（LLM 不使用）
    2. LLM 分類: 有効なワークフロー全件を候補にルーターへ
    3. 棄権: 名前が候補外・確信度 low なら None（needs_human 相当）
    """

    @inject
    def __init__(self, registry: WorkflowRegistry, router: WorkflowRouter) -> None:
        self._registry = registry
        self._router = router

    def execute(
        self, card: Card, *, cwd: Path | None = None
    ) -> tuple[WorkflowMeta | None, RoutingDecision]:
        self._registry.scan()
        candidates = self._registry.list()

        marked = self._by_marker(card, candidates)
        if marked is not None:
            decision = RoutingDecision(
                workflow=marked.name, confidence="high", reason="タイトルの intent マーカー"
            )
            logger.info("ルーティング(決定的): %s -> %s", card.name, marked.name)
            return marked, decision

        decision = self._router.route(card, candidates, cwd=cwd)
        logger.info(
            "ルーティング(LLM): %s -> %s (confidence=%s) %s",
            card.name,
            decision.workflow,
            decision.confidence,
            decision.reason,
        )
        if decision.abstained or decision.confidence != "high":
            return None, decision
        meta = next((m for m in candidates if m.name == decision.workflow), None)
        return meta, decision

    @staticmethod
    def _by_marker(card: Card, candidates: list[WorkflowMeta]) -> WorkflowMeta | None:
        markers = set(re.findall(r"\[([a-z0-9][a-z0-9_.-]*)\]", card.name))
        if not markers:
            return None
        for meta in candidates:
            if markers & set(meta.intents):
                return meta
        return None
