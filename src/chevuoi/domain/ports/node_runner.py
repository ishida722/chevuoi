from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.node_result import NodeResult
from chevuoi.domain.entities.worktree import Worktree


class NodeRunner(ABC):
    """処理ノードの実行。実装はインフラ層（claude -p の subprocess 呼び出し）。"""

    @abstractmethod
    def run(self, worktree: Worktree, card: Card) -> NodeResult: ...
