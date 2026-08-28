from __future__ import annotations

from chevuoi.domain.entities.card import Card
from chevuoi.domain.value_objects.card_id import CardId


class AdhocCard(Card):
    """CLI から手入力で与えるカード。外部サービスに紐付かず、操作は no-op。"""

    def __init__(self, name: str, desc: str = "") -> None:
        self._name = name
        self._desc = desc

    @property
    def id(self) -> CardId:
        return CardId(source="adhoc", external_id="adhoc")

    @property
    def name(self) -> str:
        return self._name

    @property
    def desc(self) -> str:
        return self._desc

    @property
    def url(self) -> str:
        return ""

    def claim(self) -> bool:
        return True

    def add_comment(self, text: str) -> None:
        pass

    def move_to_review(self) -> None:
        pass
