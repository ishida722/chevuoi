from __future__ import annotations

import logging

from injector import inject

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.node_result import NodeStatus
from chevuoi.domain.entities.project import NullProject, Project
from chevuoi.domain.ports.node_runner import NodeRunner
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

# Trello のコメント上限（16384 文字）に収める。PR URL は末尾に出るので末尾を優先して残す
MAX_COMMENT_LEN = 16000

# ノードに渡す作業指示。ランナーは汎用の claude 実行機であり、
# 「何をさせるか」はこのユースケースが決める（MVP では作業ルートはこれ1本）。
# 内容は cycle スキルのコア部分（カードの取得・移動などの Trello 操作を除く、
# 作業〜自己レビュー〜テスト〜PR 作成のループ）に合わせている。
PROMPT_TEMPLATE = """\
次のチケットに対応してください。

タイトル: {name}
URL: {url}

本文:
{desc}

進め方:
1. チケットの内容から作業モードを判断する（実装 / PoC / 調査・報告書作成）。迷ったら実装。
2. このリポジトリの規約（CLAUDE.md・テスト・lint）に従って作業する。
3. チケットのスコープを超える変更や、依頼にない大規模リファクタリングはしない。
4. コミット前に差分を自己レビューし、見つけた問題は自分で修正する。
5. テストを実行し、すべて通った状態でのみ commit / push する。
6. ブランチを push して PR を作成する。PR の作成までで必ず停止し、マージはしない。
   既定ブランチ（main）への直接 commit / push、履歴改変（force push・reset --hard）は禁止。

同じ原因で2回連続失敗して解決の目処が立たない場合は、無理に続けず原因と状況を報告して終了してください。
最後に PR の URL（PR を作らない作業の場合は成果の要約）を出力してください。
"""


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
        if project.is_null:
            logger.warning("project not found, skip: %s", card.name)
            return

        logger.info("project 解決: %s -> %s", project.tag.value, project.repo_path)
        try:
            worktree = self.worktrees.create(project, card)
            logger.info("worktree 作成: %s", worktree.path)
            logger.info("ノード実行開始: %s", card.name)
            result = self.runner.run(worktree, self.build_prompt(card))
            logger.info("ノード実行終了: %s (status=%s)", card.name, result.status.name)
        except Exception as e:
            # エラーでも動作が終わったらレビューを要求する。
            # In Progress に残すとエラーなのか作業中なのか分からないため
            logger.exception("node execution failed: %s", card.id)
            card.add_comment(_truncate_comment(f"エラー: {e}"))
            card.move_to_review()
            return

        if result.status is NodeStatus.DONE:
            card.add_comment(_truncate_comment(result.output))
        else:
            card.add_comment(_truncate_comment(f"エラー: {result.output}"))
        card.move_to_review()
        logger.info("In review へ移動: %s", card.name)

    def build_prompt(self, card: Card) -> str:
        return PROMPT_TEMPLATE.format(name=card.name, url=card.url, desc=card.desc)

    def resolve_project(self, card: Card) -> Project:
        """タイトルのタグを対応表で引く。決定的で、LLM には推測させない。

        解決できない場合は NullProject を返す。
        """
        tag = card.project_tag
        if tag is None:
            return NullProject()
        repo_path = self.config.projects.get(tag.value)
        if repo_path is None:
            return NullProject()
        return Project(tag=tag, repo_path=repo_path)
