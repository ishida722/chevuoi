from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from chevuoi.domain.value_objects.card_id import CardId

TriageAction = Literal["skip", "keep", "label", "merge", "archive"]

# 外部作用を伴う行動。台帳の planned がこの行動なら「実行が済んでいない」ことを意味する
EFFECTFUL_ACTIONS: frozenset[str] = frozenset({"label", "merge", "archive"})

# 付けるラベルの名前。読む順序の手がかりであり、行動そのものではない
LABEL_DUPLICATE = "triage/duplicate"
LABEL_STALE = "triage/stale"
LABEL_NEEDS_INFO = "triage/needs-info"
LABEL_REVIEW = "triage/review"


class TriagePlan(BaseModel):
    """カード 1 枚に対する行動。決定表の出力であり、適用前に一覧表示できる。"""

    model_config = {"frozen": True}

    card_id: CardId
    action: TriageAction
    labels: tuple[str, ...] = ()
    representative: CardId | None = None  # merge のときの集約先
    reason: str = ""  # カードに残すコメントの本文になる

    @property
    def has_effect(self) -> bool:
        return self.action in EFFECTFUL_ACTIONS


class TriageReport(BaseModel):
    """1 ランの結果。CLI の表示とログに使う。"""

    planned: list[TriagePlan] = []
    applied: list[CardId] = []
    failed: list[tuple[CardId, str]] = []
    judgments_used: int = 0
    budget_exceeded: bool = False
    pairs_found: int = 0  # 閾値を超えた候補ペアの数（閾値の較正に使う）

    def to_text(self, *, dry_run: bool) -> str:
        head = "dry-run（--apply で適用）" if dry_run else "適用"
        lines = [f"トリアージ結果（{head}）: 対象 {len(self.planned)} 件"]
        for plan in self.planned:
            if plan.action == "skip":
                continue
            detail = f"{plan.action}: {plan.card_id}"
            if plan.representative is not None:
                detail += f" -> {plan.representative}"
            if plan.labels:
                detail += f" [{', '.join(plan.labels)}]"
            if plan.reason:
                detail += f" ({plan.reason})"
            lines.append(detail)
        lines.append(
            f"LLM 判定 {self.judgments_used} 回 / 候補ペア {self.pairs_found} 組"
            + ("（予算超過あり）" if self.budget_exceeded else "")
        )
        if not dry_run:
            lines.append(f"適用 {len(self.applied)} 件 / 失敗 {len(self.failed)} 件")
        for card_id, reason in self.failed:
            lines.append(f"失敗: {card_id} ({reason})")
        return "\n".join(lines)
