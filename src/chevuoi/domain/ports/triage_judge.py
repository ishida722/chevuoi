from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.entities.triage_judgment import TriageJudgment


class TriageJudge(ABC):
    """カードの内容から判定を 1 つ返す。トリアージで LLM を使ってよい箇所はここだけ。

    必ず棄権（verdict="unknown"）を返せること。例外は投げず棄権で表現する
    （WorkflowRouter と同じ契約）。
    """

    @abstractmethod
    def judge_duplicate(self, card: TriageCard, other: TriageCard) -> TriageJudgment:
        """2 枚が同じ問題を指しているか。リポジトリは見ない（内容だけで答えられる問い）。"""

    @abstractmethod
    def judge_resolved(self, card: TriageCard, *, cwd: Path) -> TriageJudgment:
        """cwd（ベース参照のチェックアウト）で、その指摘が既に解消しているか。"""
