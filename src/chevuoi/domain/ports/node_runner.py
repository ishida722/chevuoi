from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.node_result import NodeResult
from chevuoi.domain.entities.worktree import Worktree


class NodeRunner(ABC):
    """処理ノードの実行。実装はインフラ層（claude -p の subprocess 呼び出し）。

    プロンプトは呼び出し側（ユースケース・オーケストレーター）が決めて注入する。
    ランナーはそれを実行するだけで、内容には関与しない。
    """

    @abstractmethod
    def run(self, worktree: Worktree, prompt: str) -> NodeResult: ...
