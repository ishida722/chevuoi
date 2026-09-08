from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.review_request import ReviewRequest


class ReviewRequestProvider(ABC):
    """自分にレビューが依頼されている PR を取得する入力ポート。

    カードと違い、取得した PR に対する操作は持たない（起票の材料にするだけ）。
    """

    @abstractmethod
    def fetch(self, *, limit: int) -> list[ReviewRequest]:
        """open な PR を更新の新しい順に最大 limit 件返す。失敗は ReviewRequestError。"""
