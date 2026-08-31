from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.worktree import Worktree


class WorktreeManager(ABC):
    """git worktree の構築・列挙・削除。実装はインフラ層（subprocess）。"""

    @abstractmethod
    def create(self, project: Project, card: Card) -> Worktree:
        """ブランチ名をカード ID から決定的に導出して worktree を作る。

        冪等: 同名ブランチの worktree が既にあればそれを返す（再実行対応）。
        """

    @abstractmethod
    def list_stale(self, older_than_days: int) -> list[Worktree]:
        """指定日数を経過した worktree を列挙する（経過日数ベース。終端判定はしない）。"""

    @abstractmethod
    def remove(self, worktree: Worktree) -> None: ...

    @abstractmethod
    def has_changes(self, worktree: Worktree) -> bool:
        """成果となる変更があるか。未コミットの変更（追跡外ファイル含む）に加え、
        ベース（upstream があれば upstream）との差分＝コミット済みの成果も含む。
        決定的な事実で、ワークフローの自己申告には依らない。"""
