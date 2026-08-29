from __future__ import annotations

import logging

import httpx
from injector import inject

from chevuoi.domain.entities.issue_report import IssuedCard
from chevuoi.domain.exceptions import CardIssueError
from chevuoi.domain.ports.card_issuer import CardIssueRequest, CardIssuer
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import AppConfig
from chevuoi.infrastructure.trello.client import TrelloApiError, TrelloClient
from chevuoi.infrastructure.trello.trello_card import parse_footer

logger = logging.getLogger(__name__)


def build_footer(request: CardIssueRequest) -> str:
    """機械可読・人間可読を兼ねる本文フッター。TrelloCard.parse_footer と往復できる。"""
    attrs = [f"key={request.idempotency_key}"]
    if request.parent is not None:
        attrs.append(f"parent={request.parent}")
    attrs.append(f"generation={request.generation}")
    attrs.append(f"kind={request.kind}")
    lines = ["---", "vuoi: " + " ".join(attrs)]
    if request.parent_url:
        lines.append(f"親カード: {request.parent_url}")
    return "\n".join(lines)


class TrelloCardIssuer(CardIssuer):
    """Inbox リストへ POST /cards でカードを作る。冪等キーは Inbox 一覧のフッター照合で探す。

    1 ランで発行するのは最大 4 枚（上限 3 + 要約 1）なので、毎回一覧を取り直す素朴な
    実装で足りる。Trello の検索 API は索引更新が遅延するため使わない。
    """

    @inject
    def __init__(self, client: TrelloClient, config: AppConfig) -> None:
        self._client = client
        self._config = config.trello

    def _inbox(self) -> str:
        if not self._config.inbox_list_id:
            raise CardIssueError("trello.inbox_list_id が未設定のため起票できません")
        return self._config.inbox_list_id

    def find_by_key(self, key: str) -> IssuedCard | None:
        try:
            cards = self._client.get(
                f"/lists/{self._inbox()}/cards", {"fields": "desc,shortLink,url"}
            )
        except (TrelloApiError, httpx.HTTPError) as e:
            raise CardIssueError(f"Inbox の一覧取得に失敗: {e}") from e
        for card in cards:
            if parse_footer(card.get("desc", "")).get("key") == key:
                return IssuedCard(
                    id=CardId(source="trello", external_id=card["shortLink"]),
                    url=card["url"],
                    created=False,
                )
        return None

    def issue(self, request: CardIssueRequest) -> IssuedCard:
        inbox = self._inbox()
        existing = self.find_by_key(request.idempotency_key)
        if existing is not None:
            logger.info("同じ冪等キーのカードがあるため再利用: %s", existing.url)
            return existing
        desc = request.body.rstrip()
        desc = (desc + "\n\n" if desc else "") + build_footer(request)
        try:
            created = self._client.post(
                "/cards",
                {"idList": inbox, "name": f"{request.project_tag} {request.title}", "desc": desc},
            )
        except (TrelloApiError, httpx.HTTPError) as e:
            raise CardIssueError(f"カードの作成に失敗: {e}") from e
        return IssuedCard(
            id=CardId(source="trello", external_id=created["shortLink"]),
            url=created["url"],
            created=True,
        )
