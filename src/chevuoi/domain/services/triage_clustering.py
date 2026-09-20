"""重複候補のクラスタリング（純粋関数）。外部依存なし・副作用なし。"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.ports.similarity_strategy import SimilarityStrategy
from chevuoi.domain.services.project_tag_matching import tag_key
from chevuoi.domain.services.similarity import normalize_for_similarity
from chevuoi.domain.value_objects.card_id import CardId


class DuplicatePair(BaseModel):
    model_config = {"frozen": True}

    card_id: CardId
    other_id: CardId
    score: float
    exact_title: bool = False  # 正規化タイトル完全一致（LLM 照会不要）


def _group_key(card: TriageCard) -> str:
    """比較するグループ（プロジェクト）のキー。記号違いで書かれた同じタグ
    （"[テレ東]" と "テレ東"）は同じプロジェクトとして扱う。"""
    tag = card.project_tag
    return tag_key(tag.value) if tag is not None else ""


def find_duplicate_pairs(
    cards: Sequence[TriageCard],
    similarity: SimilarityStrategy,
    *,
    threshold: float,
) -> list[DuplicatePair]:
    """同一プロジェクト内のペアだけを比較し、閾値以上を score 降順・ID 昇順で返す。

    正規化タイトルが完全一致するペアは閾値によらず必ず含める（第 1 層の決定的な重複）。
    ソートキーに ID を含めることで、同スコアでも順序が決定的になる。
    打ち切りはしない。1 ランで何組まで LLM に照会するかは呼び側の予算の話であり、
    ここで切ると「閾値を超えたペアが何組あったか」（較正の材料）が数えられなくなる。
    """
    pairs: list[DuplicatePair] = []
    for i, a in enumerate(cards):
        for b in cards[i + 1 :]:
            if _group_key(a) != _group_key(b):
                continue
            first, second = sorted((a, b), key=lambda c: str(c.id))
            exact = normalize_for_similarity(first) == normalize_for_similarity(second)
            score = similarity.score(first, second)
            if not exact and score < threshold:
                continue
            pairs.append(
                DuplicatePair(
                    card_id=first.id, other_id=second.id, score=score, exact_title=exact
                )
            )
    pairs.sort(key=lambda p: (-p.score, str(p.card_id), str(p.other_id)))
    return pairs


def choose_representative(cluster: Sequence[TriageCard]) -> TriageCard:
    """最古のカードを代表とする。同時刻は CardId の文字列順で決める（全順序）。

    人間起票カードが含まれる場合は、作成時刻に関わらず人間起票を優先する
    （人間が書いたカードは畳まないという安全側の非対称性）。
    """
    return min(cluster, key=lambda c: (c.is_auto_issued, c.created_at, str(c.id)))


def build_clusters(
    cards: Sequence[TriageCard], confirmed: Sequence[tuple[CardId, CardId]]
) -> dict[str, TriageCard]:
    """同一と確定したペアからクラスタを作り、各カードの代表を引ける表にして返す。

    キーはカード ID の文字列。単独のカード（どのペアにも入らない）は含めない。
    """
    by_id = {str(c.id): c for c in cards}
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for left, right in confirmed:
        a, b = str(left), str(right)
        if a not in by_id or b not in by_id:
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            # 併合の向きを ID 順で固定し、入力順に依存しないようにする
            parent[max(ra, rb)] = min(ra, rb)

    clusters: dict[str, list[TriageCard]] = {}
    for card_id in sorted(parent):
        clusters.setdefault(find(card_id), []).append(by_id[card_id])

    representatives: dict[str, TriageCard] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        representative = choose_representative(members)
        for member in members:
            representatives[str(member.id)] = representative
    return representatives
