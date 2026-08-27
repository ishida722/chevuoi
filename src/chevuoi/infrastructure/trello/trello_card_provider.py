from __future__ import annotations

from injector import inject

from chevuoi.domain.entities.card import Card
from chevuoi.domain.ports.card_provider import CardProvider
from chevuoi.infrastructure.config.settings import AppConfig
from chevuoi.infrastructure.trello.client import TrelloClient
from chevuoi.infrastructure.trello.trello_card import TrelloCard


class TrelloCardProvider(CardProvider):
    """Ready 相当リストのカード一覧を取得し、TrelloCard を構築して返す。"""

    @inject
    def __init__(self, client: TrelloClient, config: AppConfig) -> None:
        self._client = client
        self._config = config.trello

    def fetch_ready_cards(self) -> list[Card]:
        cards = self._client.get(
            f"/lists/{self._config.ready_list_id}/cards",
            {"fields": "name,desc,url,shortLink,idList"},
        )
        return [
            TrelloCard(
                self._client,
                self._config,
                card_id=c["id"],
                short_link=c["shortLink"],
                name=c["name"],
                desc=c["desc"],
                url=c["url"],
                list_id=c["idList"],
            )
            for c in cards
        ]
