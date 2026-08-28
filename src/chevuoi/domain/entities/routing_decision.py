from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RoutingDecision(BaseModel):
    """ルーターの判断。workflow が None なら棄権（needs_human 相当）。

    非決定性は「どの名前を選ぶか」の一点に封じ込める（仕様 §7 / triage）。
    名前の実在・有効性の検証は呼び出し側が Registry で行う。
    """

    workflow: str | None
    confidence: Literal["high", "low"] = "low"
    reason: str = ""

    @property
    def abstained(self) -> bool:
        return self.workflow is None
