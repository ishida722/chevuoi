"""類似度計算のための正規化（純粋関数）。外部依存なし。"""

from __future__ import annotations

import re

from chevuoi.domain.entities.triage_card import TriageCard

_WS = re.compile(r"\s+")


def normalize_for_similarity(card: TriageCard) -> str:
    """比較用の正規化。プロジェクトタグを落とし、casefold し、空白を畳む。

    タグは同一プロジェクト内では共通で、類似度を一様に押し上げるため除く。
    """
    title = card.title
    if card.project_tag is not None:
        title = title.split(maxsplit=1)[1]
    return _WS.sub(" ", title).strip().casefold()


def trigrams(text: str) -> set[str]:
    """文字トライグラムの集合。3 文字未満の文字列はそれ自体を 1 要素として返す。"""
    if len(text) < 3:
        return {text} if text else set()
    return {text[i : i + 3] for i in range(len(text) - 2)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
