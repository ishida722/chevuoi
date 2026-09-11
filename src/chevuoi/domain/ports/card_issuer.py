from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel

from chevuoi.domain.entities.issue_report import IssuedCard
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag

# 冪等キーを探す範囲。inbox は Inbox リストのみ、board は Inbox を含むボード全体
# （人間が Inbox から動かしたあとの再起票を防ぐ）
SearchScope = Literal["inbox", "board"]


class CardIssueRequest(BaseModel):
    """新規カードの発行要求。タイトルへのタグ前置・本文フッターの付与は実装側が行う。"""

    model_config = {"frozen": True}

    title: str  # タグ付与前のタイトル
    body: str
    project_tag: ProjectTag
    idempotency_key: str  # 本文に埋め込む。同キーがあれば再利用
    kind: str = "chore"
    generation: int = 0
    parent: CardId | None = None
    parent_url: str = ""
    search_scope: SearchScope = "inbox"  # 同キーの既存カードを探す範囲
    # 起票時に見ていたベースのコミット SHA。後から「その後この箇所は変わったか」を
    # 機械的に判定するための指紋（解決できなければ空文字）
    base_commit: str = ""


class CardIssuer(ABC):
    """タスクソースに新規カードを作る出力ポート。取得と同じく、
    まだ存在しないカードの操作なので Card のメソッドにはできない。
    """

    @abstractmethod
    def find_by_key(self, key: str, *, scope: SearchScope = "inbox") -> IssuedCard | None:
        """冪等キーを本文に持つ既存カードを探す。scope で探索範囲を選ぶ。"""

    @abstractmethod
    def issue(self, request: CardIssueRequest) -> IssuedCard:
        """Inbox 相当のリストにカードを作る。既に同キーがあればそれを返す（冪等）。

        発行できない設定（Inbox 未設定など）や API の失敗は CardIssueError。
        """
