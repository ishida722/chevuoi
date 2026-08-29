"""仕様 proposals の歯止め（深度・重複・上限）を適用する純粋関数。外部依存なし。"""

from __future__ import annotations

from pydantic import BaseModel

from chevuoi.domain.entities.task_proposal import TaskProposal, normalize_title


class PolicyResult(BaseModel):
    accepted: list[TaskProposal] = []
    rejected: list[tuple[TaskProposal, str]] = []
    overflow: int = 0  # 上限で切り捨てた件数（要約カードの根拠）

    @property
    def overflowed(self) -> list[TaskProposal]:
        return [p for p, reason in self.rejected if reason == OVERFLOW]


DEPTH_LIMIT = "世代深度の上限"
DUPLICATE = "重複"
OVERFLOW = "上限超過"


def select_proposals(
    proposals: list[TaskProposal],
    *,
    parent_generation: int,
    max_per_run: int,
    max_generation: int,
) -> PolicyResult:
    """起票候補を選別する。

    - parent_generation >= max_generation なら全件 rejected（深度制限）
    - 同一タイトル（casefold・空白正規化）は先勝ちで 1 件にまとめる
    - max_per_run を超えた分は overflow に数える
    """
    if parent_generation >= max_generation:
        return PolicyResult(rejected=[(p, DEPTH_LIMIT) for p in proposals])

    result = PolicyResult()
    seen: set[str] = set()
    for proposal in proposals:
        norm = normalize_title(proposal.title)
        if norm in seen:
            result.rejected.append((proposal, DUPLICATE))
            continue
        seen.add(norm)
        if len(result.accepted) >= max_per_run:
            result.rejected.append((proposal, OVERFLOW))
            result.overflow += 1
            continue
        result.accepted.append(proposal)
    return result
