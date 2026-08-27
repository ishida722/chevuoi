from __future__ import annotations

import logging

from injector import inject

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.node_result import NodeStatus
from chevuoi.domain.entities.project import Project
from chevuoi.domain.ports.node_runner import NodeRunner
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

# Trello のコメント上限（16384 文字）に収める。PR URL は末尾に出るので末尾を優先して残す
MAX_COMMENT_LEN = 16000


def _truncate_comment(text: str) -> str:
    if len(text) <= MAX_COMMENT_LEN:
        return text
    return "（先頭を省略）…\n" + text[-MAX_COMMENT_LEN:]


class ProcessCardUsecase:
    """カード1枚の処理。フローは後ろ向きの遷移を持たない一直線。"""

    @inject
    def __init__(
        self,
        worktrees: WorktreeManager,
        runner: NodeRunner,
        config: AppConfig,
    ) -> None:
        self.worktrees = worktrees
        self.runner = runner
        self.config = config

    def execute(self, card: Card) -> None:
        if not card.claim():
            logger.info("claim failed, skip: %s", card.id)
            return

        project = self.resolve_project(card)
        if project is None:
            logger.warning("project not found, skip: %s", card.name)
            return

        try:
            worktree = self.worktrees.create(project, card)
            result = self.runner.run(worktree, card)
        except Exception as e:
            # 仕様どおりカードは In Progress に残し、原因をカード側にも返す
            logger.exception("node execution failed: %s", card.id)
            card.add_comment(f"エラー: {e}")
            return

        if result.status is NodeStatus.DONE:
            card.add_comment(_truncate_comment(result.output))
            card.move_to_review()
        else:
            card.add_comment(_truncate_comment(f"エラー: {result.output}"))

    def resolve_project(self, card: Card) -> Project | None:
        """タイトルのタグを対応表で引く。決定的で、LLM には推測させない。"""
        tag = card.project_tag
        if tag is None:
            return None
        repo_path = self.config.projects.get(tag.value)
        if repo_path is None:
            return None
        return Project(tag=tag, repo_path=repo_path)
