from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from chevuoi.domain.entities.triage_plan import TriageAction
from chevuoi.domain.value_objects.card_id import CardId


class TriageLedgerEntry(BaseModel):
    model_config = {"frozen": True}

    card_id: CardId
    digest: str
    action: TriageAction
    # 再判定の要否を分けるための理由。"budget" のときだけ、内容が変わらなくても次回再判定する
    reason: Literal["", "budget", "abstained"] = ""
    state: Literal["planned", "applied"]
    updated_at: datetime


class TriageLedger(ABC):
    """ローカル台帳。真実源ではなく、再判定を省くためのキャッシュ + intent の記録。

    失っても結果は変わらない（真実源はタスクソース側の状態）。再判定のコストが増えるだけ。
    """

    @abstractmethod
    def load(self) -> dict[str, TriageLedgerEntry]:
        """カード ID の文字列表現をキーにした台帳の全体。"""

    @abstractmethod
    def record(self, entry: TriageLedgerEntry) -> None: ...
