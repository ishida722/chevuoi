from __future__ import annotations

from pydantic import BaseModel

from chevuoi.domain.value_objects.card_id import CardId


class BranchName(BaseModel):
    """カード ID から決定的に導出される作業ブランチ名。"""

    model_config = {"frozen": True}

    value: str

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_card_id(cls, card_id: CardId) -> BranchName:
        """chevuoi/<source>-<external_id> を導出する（例: chevuoi/trello-oFm0QQAr）。"""
        return cls(value=f"chevuoi/{card_id.source}-{card_id.external_id}")
