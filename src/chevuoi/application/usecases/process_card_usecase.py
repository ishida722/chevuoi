from __future__ import annotations

import logging

from injector import inject

from chevuoi.application.usecases.issue_proposals_usecase import IssueProposalsUsecase
from chevuoi.application.usecases.select_workflow_usecase import SelectWorkflowUsecase
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.issue_report import IssueReport
from chevuoi.domain.entities.project import NullProject, Project
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor
from chevuoi.domain.ports.pull_request_publisher import PullRequestPublisher
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

# Trello のコメント上限（16384 文字）に収める
MAX_COMMENT_LEN = 16000

# 自動処理が残すコメントの印。これが付いたコメントは人間の指示ではない
BOT_MARK = "🤖"

# プロンプトに載せるレビューコメントの総量上限。超えたら新しいコメントを優先して残す
MAX_REVIEW_COMMENTS_LEN = 8000


def _is_bot_comment(text: str) -> bool:
    # 自動処理のコメントは 1 行目が 🤖 で始まる。人間が本文の途中で 🤖 行を引用しても
    # 誤爆しないよう 1 行目だけで判定する。旧形式の PR コメントだけは summary の後に
    # "🤖 PR:" 行が来るため、互換のためそれも自動処理とみなす
    return text.startswith(BOT_MARK) or any(
        line.startswith(f"{BOT_MARK} PR:") for line in text.splitlines()
    )


def _select_human_comments(comments: list[str]) -> list[str]:
    """直近の自動処理コメントより新しい人間コメントを、総量上限内で古い順に返す。

    それより古い人間コメントは前回実行が読んでいるため再送しない（対応済みの
    指示を「必ず対応すること」として繰り返さない）。上限超過時は新しい方を残す。
    """
    selected: list[str] = []
    total = 0
    for text in comments:  # 新しい順
        if _is_bot_comment(text):
            break
        remaining = MAX_REVIEW_COMMENTS_LEN - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            if selected:
                break
            text = text[:remaining] + "…（以降省略）"
        selected.append(text)
        total += len(text)
    selected.reverse()
    return selected


def _previous_outcome(comments: list[str]) -> str | None:
    """前回の自動処理の成果種別。"🤖 PR:" があれば "pr"、"🤖 完了:" だけなら "comment"。"""
    outcome = None
    for text in comments:
        for line in text.splitlines():
            if line.startswith(f"{BOT_MARK} PR:"):
                return "pr"
            if line.startswith(f"{BOT_MARK} 完了:"):
                outcome = "comment"
    return outcome


def _truncate_comment(text: str) -> str:
    if len(text) <= MAX_COMMENT_LEN:
        return text
    # 1 行目（🤖 印・PR URL）と末尾（起票報告）を優先して残す。
    # 1 行目を落とすと次回実行時に自動処理コメントが人間の指示と誤認される
    head, sep, rest = text.partition("\n")
    notice = "\n（中略）…\n"
    budget = MAX_COMMENT_LEN - len(head) - len(notice)
    if not sep or budget <= 0:
        return text[: MAX_COMMENT_LEN - 1] + "…"
    return head + notice + rest[-budget:]


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
        proposals: IssueProposalsUsecase,
    ) -> None:
        self.worktrees = worktrees
        self.selector = selector
        self.registry = registry
        self.executor = executor
        self.publisher = publisher
        self.config = config
        self.proposals = proposals

    def execute(self, card: Card) -> None:
        if not card.claim():
            logger.info("claim failed, skip: %s", card.id)
            return

        project = self.resolve_project(card)
        if project.is_null:
            # 放置すると In Progress に残って作業中と区別できないため、人間に返す
            logger.warning("project not found: %s", card.name)
            tag = card.project_tag
            tag_text = f"タグ「{tag.value}」に対応するプロジェクトが設定にありません。" if tag else "タイトルにプロジェクトタグがありません。"
            card.add_comment(
                _truncate_comment(
                    "🤖 needs_human: プロジェクトを特定できませんでした。"
                    f"{tag_text}\n"
                    "設定済みのプロジェクトタグをタイトル先頭に付けるか、設定にプロジェクトを追加してください。\n"
                    f"設定済みタグ: {', '.join(self.config.projects) or '(なし)'}"
                )
            )
            card.move_to_review()
            logger.info("In review へ移動: %s", card.name)
            return
        logger.info("project 解決: %s -> %s", project.tag.value, project.repo_path)

        # 起票結果は finalize（PR 作成を含む）が失敗しても失わないよう try の外で持つ
        report = IssueReport()
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
            # 終端状態に関わらず起票する（blocked でも踏んだバグは実在する）。例外は出さない
            report = self.proposals.execute(result.proposals, project, parent=card)
            comment = self.finalize(card, meta.outcome, worktree, result)
        except Exception as e:
            # エラーでも動作が終わったらレビューを要求する。
            # In Progress に残すとエラーなのか作業中なのか分からないため
            logger.exception("card processing failed: %s", card.id)
            comment = f"🤖 エラー: {e}"

        if not report.is_empty:
            # 末尾に置く（_truncate_comment は末尾を優先して残す）
            comment += "\n\n" + report.to_comment()
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
        # 1 行目に 🤖 印を置き、自動処理コメントであることを機械可読にする
        return f"🤖 PR: {url}\n\n{result.summary}"

    @staticmethod
    def build_message(card: Card) -> str:
        message = f"タイトル: {card.name}\nURL: {card.url}\n\n本文:\n{card.desc}"
        comments = card.fetch_comments()
        human = _select_human_comments(comments)
        if human:
            message += (
                "\n\nレビューコメント（人間からの追加指示。必ず対応すること）:\n"
                + "\n".join(f"- {c}" for c in human)
            )
        outcome = _previous_outcome(comments)
        if outcome == "pr":
            message += (
                "\n\nこのカードは一度自動処理され、PR 作成後に人間のレビューで差し戻されました。"
                "前回の成果は現在のブランチにコミット済みです。"
                "前回と同じ作業をやり直すのではなく、前回の成果を前提に"
                "レビューコメントの指示へ対応してください。"
                "変更はコミットせず作業ツリーに残してください（コミットと PR 更新は自動で行われます）。"
            )
        elif outcome == "comment":
            message += (
                "\n\nこのカードは一度自動処理され（前回はコメント報告のみで、コミットはありません）、"
                "人間のレビューで差し戻されました。"
                "前回の報告を踏まえて、レビューコメントの指示へ対応してください。"
            )
        return message

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
