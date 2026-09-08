from __future__ import annotations

import logging

import httpx
from injector import inject

from chevuoi.domain.entities.issue_report import IssuedCard
from chevuoi.domain.exceptions import CardIssueError
from chevuoi.domain.ports.card_issuer import CardIssueRequest, CardIssuer, SearchScope
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


def _card_name(request: CardIssueRequest) -> str:
    """タイトル先頭にプロジェクトタグを前置する。タグが無ければタイトルだけにする
    （プロジェクトに紐づかないカードの先頭に空白を残さない）。
    """
    tag = str(request.project_tag).strip()
    return f"{tag} {request.title}" if tag else request.title


class TrelloCardIssuer(CardIssuer):
    """Inbox リストへ POST /cards でカードを作る。冪等キーは既存カード一覧の
    フッター照合で探す（範囲は request.search_scope が決める）。

    Inbox 範囲の照合は 1 ラン最大 4 枚（上限 3 + 要約 1）なので毎回取り直す。
    ボード範囲は 1 回の実行で PR の数だけ引くことになるため、一覧をプロセス内に
    保持して 1 ランにつき 1 回だけ取得する（Trello のレート上限に当てない）。
    Trello の検索 API は索引更新が遅延するため使わない。
    """

    @inject
    def __init__(self, client: TrelloClient, config: AppConfig) -> None:
        self._client = client
        self._config = config.trello
        self._board_id: str | None = None
        self._board_cards: list[dict] | None = None  # ボード範囲の照合用。1 ランで 1 回だけ取る

    def _inbox(self) -> str:
        if not self._config.inbox_list_id:
            raise CardIssueError("trello.inbox_list_id が未設定のため起票できません")
        return self._config.inbox_list_id

    def _board(self) -> str:
        """Inbox リストが乗っているボード。設定には持たず 1 回だけ引いて覚える。"""
        if self._board_id is None:
            try:
                board = self._client.get(f"/lists/{self._inbox()}", {"fields": "idBoard"})
                self._board_id = board["idBoard"]
            except (TrelloApiError, httpx.HTTPError, KeyError, TypeError) as e:
                raise CardIssueError(f"Inbox のボード特定に失敗: {e}") from e
        return self._board_id

    def find_by_key(self, key: str, *, scope: SearchScope = "inbox") -> IssuedCard | None:
        for card in self._cards(scope):
            if parse_footer(card.get("desc", "")).get("key") == key:
                return IssuedCard(
                    id=CardId(source="trello", external_id=card["shortLink"]),
                    url=card["url"],
                    created=False,
                )
        return None

    def _cards(self, scope: SearchScope) -> list[dict]:
        """照合対象のカード一覧。ボード範囲は取得済みならそれを使い回す。"""
        if scope != "board":
            return self._fetch_cards(f"/lists/{self._inbox()}/cards", "Inbox")
        if self._board_cards is None:
            # Inbox から動かされたカードも既存として拾う（定期起票の二重発行を防ぐ）。
            # アーカイブ済みのカードは含まれない（一覧が際限なく育つのを避けるため）
            self._board_cards = self._fetch_cards(f"/boards/{self._board()}/cards", "ボード")
        return self._board_cards

    def _fetch_cards(self, path: str, where: str) -> list[dict]:
        try:
            return self._client.get(path, {"fields": "desc,shortLink,url"})
        except (TrelloApiError, httpx.HTTPError) as e:
            raise CardIssueError(f"{where}の一覧取得に失敗: {e}") from e

    def issue(self, request: CardIssueRequest) -> IssuedCard:
        inbox = self._inbox()
        existing = self.find_by_key(request.idempotency_key, scope=request.search_scope)
        if existing is not None:
            logger.info("同じ冪等キーのカードがあるため再利用: %s", existing.url)
            return existing
        desc = request.body.rstrip()
        desc = (desc + "\n\n" if desc else "") + build_footer(request)
        try:
            created = self._client.post(
                "/cards",
                {"idList": inbox, "name": _card_name(request), "desc": desc},
            )
        except (TrelloApiError, httpx.HTTPError) as e:
            raise CardIssueError(f"カードの作成に失敗: {e}") from e
        if self._board_cards is not None:
            # 取得済みの一覧にも足す。同じランで続けて発行するカードから見えるようにする
            self._board_cards.append(
                {"desc": desc, "shortLink": created["shortLink"], "url": created["url"]}
            )
        return IssuedCard(
            id=CardId(source="trello", external_id=created["shortLink"]),
            url=created["url"],
            created=True,
        )
