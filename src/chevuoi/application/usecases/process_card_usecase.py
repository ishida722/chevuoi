from __future__ import annotations

import logging

from injector import inject

from chevuoi.application.usecases.select_workflow_usecase import SelectWorkflowUsecase
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.project import NullProject, Project
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor
from chevuoi.domain.ports.pull_request_publisher import PullRequestPublisher
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
    """カード 1 枚の処理。

    claim → project 解決 → ワークフロー選択 → worktree 上で実行 → 終端処理 → In review。
    終端処理は outcome × 差分の有無 × blocked の有無で決定的に決める（LLM の自己申告は使わない）。
    ワークフローが選べない（棄権）場合は needs_human として人間に返し、別経路にはフォールバックしない。
    """

    @inject
    def __init__(
        self,
        worktrees: WorktreeManager,
        selector: SelectWorkflowUsecase,
        registry: WorkflowRegistry,
        executor: GraphExecutor,
        publisher: PullRequestPublisher,
        config: AppConfig,
    ) -> None:
        self.worktrees = worktrees
        self.selector = selector
        self.registry = registry
        self.executor = executor
        self.publisher = publisher
        self.config = config

    def execute(self, card: Card) -> None:
        if not card.claim():
            logger.info("claim failed, skip: %s", card.id)
            return

        project = self.resolve_project(card)
        if project.is_null:
            logger.warning("project not found, skip: %s", card.name)
            return
        logger.info("project 解決: %s -> %s", project.tag.value, project.repo_path)

        try:
            meta, decision = self.selector.execute(card, cwd=project.repo_path)
            if meta is None:
                card.add_comment(
                    _truncate_comment(
                        "🤖 needs_human: 適用するワークフローを決められませんでした。"
                        f"カードの本文に作業の種類（実装 / 調査 / 運用作業）を書き足してください。\n"
                        f"理由: {decision.reason}"
                    )
                )
                card.move_to_review()
                return

            worktree = self.worktrees.create(project, card)
            logger.info("worktree 作成: %s", worktree.path)
            workflow = self.registry.get(meta.name)
            logger.info("ワークフロー実行開始: %s (%s)", card.name, meta.name)
            result = self.executor.execute(
                workflow, self.build_message(card), workdir=worktree.path, project=project
            )
            logger.info("ワークフロー実行終了: %s (blocked=%s)", card.name, bool(result.blocked))
            comment = self.finalize(card, meta.outcome, worktree, result)
        except Exception as e:
            # エラーでも動作が終わったらレビューを要求する。
            # In Progress に残すとエラーなのか作業中なのか分からないため
            logger.exception("card processing failed: %s", card.id)
            comment = f"🤖 エラー: {e}"

        card.add_comment(_truncate_comment(comment))
        card.move_to_review()
        logger.info("In review へ移動: %s", card.name)

    def finalize(
        self, card: Card, outcome: str, worktree: Worktree, result: ExecutionResult
    ) -> str:
        """終端処理。戻り値はカードに残すコメント。"""
        if result.blocked:
            return f"🤖 blocked: {result.blocked}\n\nworktree: {worktree.path}"
        if outcome == "comment":
            return f"🤖 完了:\n{result.summary}"
        if not self.worktrees.has_changes(worktree):
            return f"🤖 変更なし:\n{result.summary}"
        url = self.publisher.publish(
            worktree,
            title=card.name,
            body=f"{result.summary}\n\nTrello: {card.url}",
        )
        return f"{result.summary}\n\n🤖 PR: {url}"

    @staticmethod
    def build_message(card: Card) -> str:
        return f"タイトル: {card.name}\nURL: {card.url}\n\n本文:\n{card.desc}"

    def resolve_project(self, card: Card) -> Project:
        """タイトルのタグを対応表で引く。決定的で、LLM には推測させない。

        解決できない場合は NullProject を返す。
        """
        tag = card.project_tag
        if tag is None:
            return NullProject()
        entry = self.config.projects.get(tag.value)
        if entry is None:
            # タグの大文字小文字は無視する（例: "Wf" と "wf" を同一視）
            wanted = tag.value.casefold()
            entry = next(
                (cfg for key, cfg in self.config.projects.items() if key.casefold() == wanted),
                None,
            )
        if entry is None:
            return NullProject()
        return Project(tag=tag, repo_path=entry.path, test_commands=list(entry.test_commands))
