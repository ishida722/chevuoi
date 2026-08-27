from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.card import Card


class CardProvider(ABC):
    """外部サービスから処理対象カードを取得する入力ポート。

    取得できた時点でカードの ID が確定し、以後の操作は
    返された Card 自身が行う。
    """

    @abstractmethod
    def fetch_ready_cards(self) -> list[Card]: ...
