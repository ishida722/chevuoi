from __future__ import annotations

import hashlib

from pydantic import BaseModel

from chevuoi.domain.entities.task_proposal import TaskProposal

TITLE_PREFIX = "PR レビュー: "
MAX_TITLE_LEN = 200  # TaskProposal.title の上限に合わせる


class ReviewRequest(BaseModel):
    """自分にレビューが依頼されている PR。取得元（gh）の詳細は知らない。"""

    model_config = {"frozen": True}

    repository: str  # "owner/name"
    number: int
    title: str = ""
    url: str = ""
    author: str = ""
    updated_at: str = ""

    @property
    def slug(self) -> str:
        return f"{self.repository}#{self.number}"

    @property
    def card_key(self) -> str:
        """冪等キー。リポジトリと PR 番号だけから決まるので、PR のタイトルが
        書き換わっても同じカードを指す（起票のたびに増えない）。
        """
        seed = f"pr-review:{self.repository.casefold()}#{self.number}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

    def to_proposal(self) -> TaskProposal:
        """起票用の候補。本文の先頭に PR の URL を置く（レビューのワークフローが
        本文から PR を特定するため）。
        """
        lines = [self.url, "", f"リポジトリ: {self.repository}", f"PR: #{self.number}"]
        if self.author:
            lines.append(f"作成者: {self.author}")
        if self.updated_at:
            lines.append(f"更新: {self.updated_at}")
        lines += [
            "",
            "自分にレビューが依頼されている PR です。"
            "人間がレビューするか、PR レビューのワークフローに流すかを判断してください。",
        ]
        return TaskProposal(title=self._card_title(), body="\n".join(lines), kind="chore")

    def _card_title(self) -> str:
        """"PR レビュー: <PR タイトル>（owner/name#12）"。上限を超える分は PR タイトルを詰める。"""
        suffix = f"（{self.slug}）"
        title = self.title.strip() or "(タイトルなし)"
        budget = MAX_TITLE_LEN - len(TITLE_PREFIX) - len(suffix)
        if budget < 1:
            # リポジトリ名だけで上限に迫る異常な場合。識別子（slug）の側を残す
            return f"{TITLE_PREFIX}{suffix}"[:MAX_TITLE_LEN]
        if len(title) > budget:
            title = title[: budget - 1] + "…"
        return f"{TITLE_PREFIX}{title}{suffix}"
