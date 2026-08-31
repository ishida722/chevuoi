from __future__ import annotations

import re

from chevuoi.domain.entities.card import Card
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import TrelloConfig
from chevuoi.infrastructure.trello.client import TrelloClient


# 自動起票カードの本文末尾に TrelloCardIssuer が書く機械可読フッター
# 例: "vuoi: key=3f9a1c2b7d4e parent=trello:VsK3d4Jp generation=1 kind=bug"
# 行内空白（[ \t]）だけを区切りにし、キーに = を含めないことで行跨ぎと指数的バックトラックを防ぐ
FOOTER_LINE = re.compile(r"^vuoi:((?:[ \t]+[^\s=]+=\S*)+)[ \t]*$", re.MULTILINE)


def parse_footer(desc: str) -> dict[str, str]:
    """本文から vuoi: フッターの属性を読む。無ければ空 dict。複数あれば最後を採る。"""
    matches = FOOTER_LINE.findall(desc)
    if not matches:
        return {}
    attrs: dict[str, str] = {}
    for token in matches[-1].split():
        key, _, value = token.partition("=")
        attrs[key] = value
    return attrs


class TrelloCard(Card):
    """Card の Trello 実装。自分自身への操作を TrelloClient 経由の REST で行う。"""

    def __init__(
        self,
        client: TrelloClient,
        config: TrelloConfig,
        *,
        card_id: str,
        short_link: str,
        name: str,
        desc: str,
        url: str,
        list_id: str,
    ) -> None:
        self._client = client
        self._config = config
        self._card_id = card_id
        self._short_link = short_link
        self._name = name
        self._desc = desc
        self._url = url
        self._list_id = list_id

    @property
    def id(self) -> CardId:
        return CardId(source="trello", external_id=self._short_link)

    @property
    def name(self) -> str:
        return self._name

    @property
    def desc(self) -> str:
        return self._desc

    @property
    def url(self) -> str:
        return self._url

    @property
    def generation(self) -> int:
        try:
            return int(parse_footer(self._desc).get("generation", 0))
        except ValueError:
            return 0

    @property
    def parent_id(self) -> CardId | None:
        source, sep, external_id = parse_footer(self._desc).get("parent", "").partition(":")
        if not sep or not source or not external_id:
            return None
        return CardId(source=source, external_id=external_id)

    def claim(self) -> bool:
        """In Progress へ移動してクレームする。

        冪等: サーバ上の現在リストを確認し、既に In Progress なら成功、
        Ready でも In Progress でもなければ失敗を返す。
        """
        current = self._client.get(f"/cards/{self._card_id}", {"fields": "idList"})
        list_id = current["idList"]
        if list_id == self._config.in_progress_list_id:
            self._list_id = list_id
            return True
        if list_id != self._config.ready_list_id:
            return False
        self._client.put(
            f"/cards/{self._card_id}", {"idList": self._config.in_progress_list_id}
        )
        self._list_id = self._config.in_progress_list_id
        return True

    def add_comment(self, text: str) -> None:
        self._client.post(f"/cards/{self._card_id}/actions/comments", {"text": text})

    def fetch_comments(self) -> list[str]:
        # Trello の actions は新しい順で返る。既定の 50 件窓で古いコメントを
        # 見落とさないよう、API 上限の 1000 件まで取る
        actions = self._client.get(
            f"/cards/{self._card_id}/actions", {"filter": "commentCard", "limit": 1000}
        )
        return [a["data"]["text"] for a in actions]

    def move_to_review(self) -> None:
        self._client.put(
            f"/cards/{self._card_id}", {"idList": self._config.in_review_list_id}
        )
        self._list_id = self._config.in_review_list_id
