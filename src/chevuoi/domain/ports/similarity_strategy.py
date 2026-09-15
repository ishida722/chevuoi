from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.triage_card import TriageCard


class SimilarityStrategy(ABC):
    """2 枚のカードの類似度を [0.0, 1.0] で返す。決定的であること（同じ入力に同じ値）。

    設計指針は Strategy を domain/services に置くが、本リポジトリは ABC を
    domain/ports に集めている（TriageJudge も同様）。慣行の側に統一した。
    """

    @abstractmethod
    def score(self, a: TriageCard, b: TriageCard) -> float: ...
