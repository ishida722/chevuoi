from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from injector import inject

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.exceptions import TriageError
from chevuoi.domain.ports.triage_card_repository import TriageCardRepository
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import AppConfig
from chevuoi.infrastructure.trello.client import TrelloApiError, TrelloClient
from chevuoi.infrastructure.trello.trello_card import parse_footer

logger = logging.getLogger(__name__)

# IssueCardUsecase が本文に書く根拠節の見出し。ここから evidence を逆解析する
EVIDENCE_HEADING = "根拠:"
# 冪等性の照合に読むコメント数。トリアージのコメントは適用直後に付くので直近だけ見る
COMMENT_WINDOW = 50


def parse_evidence(desc: str) -> tuple[str, ...]:
    """本文の「根拠:」節から evidence を復元する（IssueCardUsecase の書式の逆）。"""
    lines = desc.splitlines()
    found: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped == EVIDENCE_HEADING:
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("- "):
            found.append(stripped[2:].strip())
            continue
        if stripped:  # 箇条書き以外の行が来たら節の終わり
            in_section = False
    return tuple(found)


def created_at_from_id(card_id: str) -> datetime:
    """Trello の内部カード ID（ObjectId）先頭 8 桁は生成時刻の Unix 秒。

    追加の API 呼び出しなしに作成時刻を決定的に導出できる。読めない ID は
    「たった今作られた」として扱う。settle の目的は起票直後のカードを触らないこと
    なので、時刻が分からない場合は触らない側（=判定対象から外れる側）に倒す。
    """
    try:
        return datetime.fromtimestamp(int(card_id[:8], 16), tz=UTC)
    except (ValueError, TypeError, OSError, OverflowError):
        logger.warning("カード ID から作成時刻を導出できません: %r", card_id)
        return datetime.now(UTC)


class TrelloTriageRepository(TriageCardRepository):
    """Inbox リストのカードを集合として読み、ID で書き戻す Trello 実装。

    ボード ID とラベル ID はランの中でキャッシュする（Trello のレート上限を叩かない）。
    """

    @inject
    def __init__(self, client: TrelloClient, config: AppConfig) -> None:
        self._client = client
        self._config = config.trello
        self._board_id: str | None = None
        self._label_ids: dict[str, str] | None = None

    def fetch_open(self) -> list[TriageCard]:
        cards = self._get(
            f"/lists/{self._inbox()}/cards",
            {"fields": "id,shortLink,name,desc,url,labels"},
            "Inbox の一覧取得",
        )
        parsed = [self._to_card(card) for card in cards]
        # 作成順（昇順）。同時刻は ID 順で全順序にする
        return sorted(parsed, key=lambda c: (c.created_at, str(c.id)))

    def add_comment(self, card_id: CardId, text: str) -> None:
        self._post(
            f"/cards/{card_id.external_id}/actions/comments", {"text": text}, "コメントの追加"
        )

    def has_comment(self, card_id: CardId, digest: str) -> bool:
        actions = self._get(
            f"/cards/{card_id.external_id}/actions",
            {"filter": "commentCard", "limit": COMMENT_WINDOW},
            "コメントの取得",
        )
        return any(digest in a.get("data", {}).get("text", "") for a in actions)

    def add_label(self, card_id: CardId, label: str) -> None:
        label_id = self._label_id(label)
        current = self._get(
            f"/cards/{card_id.external_id}", {"fields": "idLabels"}, "ラベルの確認"
        )
        if label_id in current.get("idLabels", []):
            return
        self._post(f"/cards/{card_id.external_id}/idLabels", {"value": label_id}, "ラベルの付与")

    def archive(self, card_id: CardId) -> None:
        # closed=true は可逆（削除ではない）。既にアーカイブ済みでも同じ結果になる
        self._put(f"/cards/{card_id.external_id}", {"closed": "true"}, "アーカイブ")

    def _to_card(self, card: dict) -> TriageCard:
        desc = card.get("desc", "")
        footer = parse_footer(desc)
        try:
            generation = int(footer.get("generation", 0))
        except ValueError:
            generation = 0
        source, sep, external = footer.get("parent", "").partition(":")
        parent = CardId(source=source, external_id=external) if sep and source and external else None
        return TriageCard(
            id=CardId(source="trello", external_id=card["shortLink"]),
            title=card.get("name", ""),
            body=desc,
            url=card.get("url", ""),
            created_at=created_at_from_id(card.get("id", "")),
            labels=tuple(label.get("name", "") for label in card.get("labels", [])),
            key=footer.get("key", ""),
            kind=footer.get("kind", "chore"),
            generation=generation,
            parent_id=parent,
            base_commit=footer.get("base", ""),
            evidence=parse_evidence(desc),
        )

    def _inbox(self) -> str:
        if not self._config.inbox_list_id:
            raise TriageError("trello.inbox_list_id が未設定のためトリアージできません")
        return self._config.inbox_list_id

    def _board(self) -> str:
        """Inbox リストが乗っているボード。設定には持たず 1 回だけ引いて覚える。"""
        if self._board_id is None:
            board = self._get(f"/lists/{self._inbox()}", {"fields": "idBoard"}, "ボードの特定")
            try:
                self._board_id = board["idBoard"]
            except (KeyError, TypeError) as e:
                raise TriageError(f"ボードの特定に失敗: {e}") from e
        return self._board_id

    def _label_id(self, name: str) -> str:
        """ラベル名からラベル ID を引く。無ければ作る（色は付けない）。"""
        if self._label_ids is None:
            labels = self._get(
                f"/boards/{self._board()}/labels", {"fields": "name", "limit": 1000},
                "ラベルの一覧取得",
            )
            self._label_ids = {
                label.get("name", ""): label["id"] for label in labels if label.get("name")
            }
        if name not in self._label_ids:
            created = self._post(
                "/labels", {"idBoard": self._board(), "name": name, "color": "null"},
                "ラベルの作成",
            )
            self._label_ids[name] = created["id"]
        return self._label_ids[name]

    def _get(self, path: str, params: dict, what: str):
        return self._call(lambda: self._client.get(path, params), what)

    def _post(self, path: str, params: dict, what: str):
        return self._call(lambda: self._client.post(path, params), what)

    def _put(self, path: str, params: dict, what: str):
        return self._call(lambda: self._client.put(path, params), what)

    @staticmethod
    def _call(fn, what: str):
        try:
            return fn()
        except (TrelloApiError, httpx.HTTPError) as e:
            raise TriageError(f"{what}に失敗: {e}") from e
