from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.value_objects.card_id import CardId


class TriageCardRepository(ABC):
    """トリアージ対象カードの取得と状態の書き戻し。

    Card ADT ではなくリポジトリにする理由: トリアージは集合を読み、ID で他カードを
    操作する（重複カードを代表カードへ集約する）。1 枚を処理する Card の抽象では
    表現できない。リストの概念（Inbox など）は実装側に閉じる。
    """

    @abstractmethod
    def fetch_open(self) -> list[TriageCard]:
        """トリアージ対象リストの未アーカイブカードを、作成順（昇順）で返す。"""

    @abstractmethod
    def add_comment(self, card_id: CardId, text: str) -> None: ...

    @abstractmethod
    def has_comment(self, card_id: CardId, digest: str) -> bool:
        """同じ digest のトリアージコメントが既にあるか（コメントの冪等性）。"""

    @abstractmethod
    def add_label(self, card_id: CardId, label: str) -> None:
        """ラベルを付ける。既に付いていれば何もしない（冪等）。"""

    @abstractmethod
    def archive(self, card_id: CardId) -> None:
        """アーカイブする。可逆であること（削除しない）。既にアーカイブ済みなら何もしない。"""
