from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field

from chevuoi.domain.value_objects.card_id import CardId

ProposalKind = Literal["bug", "chore", "spike", "debt"]

_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """重複判定・冪等キー用のタイトル正規化（casefold・空白の畳み込み）。"""
    return _WS.sub(" ", title).strip().casefold()


class TaskProposal(BaseModel):
    """ワークフローから受け取った起票候補（ホスト側表現）。SDK の Proposal は知らない。"""

    model_config = {"frozen": True}

    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    kind: ProposalKind = "chore"
    evidence: tuple[str, ...] = ()

    def key(self, parent: CardId | None) -> str:
        """冪等キー。親カード ID + 正規化タイトルの sha1 先頭 12 桁。決定的。"""
        seed = f"{parent or ''}\n{normalize_title(self.title)}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
