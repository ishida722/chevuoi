from __future__ import annotations

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.ports.similarity_strategy import SimilarityStrategy
from chevuoi.domain.services.similarity import jaccard, normalize_for_similarity, trigrams

TITLE_WEIGHT = 0.7
EVIDENCE_WEIGHT = 0.3


class TrigramSimilarity(SimilarityStrategy):
    """文字トライグラムの Jaccard 係数。日本語の分かち書きが要らず、外部依存もない。

    タイトルの類似度と evidence パスの一致を重み付きで合成する:
        score = 0.7 * jaccard(title_trigrams) + 0.3 * jaccard(evidence_paths)
    evidence が両方空なら title のみで判断する（合成すると一律に減点されるため）。
    """

    def score(self, a: TriageCard, b: TriageCard) -> float:
        title = jaccard(
            trigrams(normalize_for_similarity(a)), trigrams(normalize_for_similarity(b))
        )
        paths_a, paths_b = set(a.evidence_paths), set(b.evidence_paths)
        if not paths_a and not paths_b:
            return title
        return TITLE_WEIGHT * title + EVIDENCE_WEIGHT * jaccard(paths_a, paths_b)
