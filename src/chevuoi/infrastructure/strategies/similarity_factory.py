from __future__ import annotations

from chevuoi.domain.ports.similarity_strategy import SimilarityStrategy
from chevuoi.infrastructure.strategies.trigram_similarity import TrigramSimilarity

# 設定 [triage] similarity の値 → 実装クラス。実装を足すときはこの表に 1 行足すだけで、
# DI モジュールは変更しない
SIMILARITIES: dict[str, type[SimilarityStrategy]] = {
    "trigram": TrigramSimilarity,
}

DEFAULT_SIMILARITY = "trigram"


def get_similarity_class(name: str) -> type[SimilarityStrategy]:
    """名前から実装クラスを解決する。未知の名前は既定（trigram）に落とす。"""
    return SIMILARITIES.get(name, SIMILARITIES[DEFAULT_SIMILARITY])
