from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag


class Card(ABC):
    """処理対象カードの抽象データ型。

    各具体カードは自分がどのサービスのどの ID かを知っており、
    自分自身に対する操作（クレーム・コメント・移動）を実装する。
    """

    @property
    @abstractmethod
    def id(self) -> CardId: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def desc(self) -> str: ...

    @property
    @abstractmethod
    def url(self) -> str: ...

    @abstractmethod
    def claim(self) -> bool:
        """着手宣言する（Trello なら In Progress への移動）。

        冪等: 既にクレーム済みなら成功として扱い、
        それ以外の状態なら失敗（False）を返す。
        """

    @abstractmethod
    def add_comment(self, text: str) -> None: ...

    @abstractmethod
    def move_to_review(self) -> None: ...

    @property
    def project_tag(self) -> ProjectTag | None:
        """タイトル先頭のタグ（例: "MIRAI ログイン修正" → MIRAI）。無ければ None。"""
        return ProjectTag.from_title(self.name)

    @property
    def generation(self) -> int:
        """世代深度。人間起票 = 0。自動起票されるたびに +1。"""
        return 0

    @property
    def parent_id(self) -> CardId | None:
        """自動起票の親カード。人間起票なら None。"""
        return None
