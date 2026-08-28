from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.worktree import Worktree


class PullRequestPublisher(ABC):
    """worktree の変更をコミット・push して PR を作る。ホストが決定的に行う
    （main への直接 push 禁止・PR 作成で停止、をプロンプト遵守に頼らない）。
    """

    @abstractmethod
    def publish(self, worktree: Worktree, *, title: str, body: str) -> str:
        """PR を作成して URL を返す。既に同ブランチの PR があればその URL を返す。"""
