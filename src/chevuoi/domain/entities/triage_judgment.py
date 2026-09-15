from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from chevuoi.domain.value_objects.card_id import CardId

Verdict = Literal["duplicate", "distinct", "resolved", "unresolved", "needs_info", "unknown"]


class TriageJudgment(BaseModel):
    """判定器の出力。RoutingDecision と同じく、棄権を表現できることが要件。"""

    model_config = {"frozen": True}

    verdict: Verdict = "unknown"
    confidence: Literal["high", "low"] = "low"
    reason: str = ""
    # LLM が「同じ」と判断した相手の記録。行動の決定には使わない（代表は決定的に選ぶ）
    duplicate_of: CardId | None = None

    @property
    def abstained(self) -> bool:
        return self.verdict == "unknown" or self.confidence != "high"
