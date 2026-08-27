from __future__ import annotations

from chevuoi.domain.entities.card import Card
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import TrelloConfig
from chevuoi.infrastructure.trello.client import TrelloClient


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

    def move_to_review(self) -> None:
        self._client.put(
            f"/cards/{self._card_id}", {"idList": self._config.in_review_list_id}
        )
        self._list_id = self._config.in_review_list_id
