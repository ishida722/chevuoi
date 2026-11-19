from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Workspace(BaseModel):
    """vuoi exec が実行する作業ツリー。

    root は作業ツリーのルート（ワークフローの workdir）。main_root は本体側の
    リポジトリのルートで、root が git worktree なら本体を、そうでなければ root と
    同じパスを指す。設定のプロジェクトとの照合に両方を使う。
    """

    model_config = ConfigDict(frozen=True)

    root: Path
    main_root: Path


class WorkspaceInspector(ABC):
    """今いる作業ツリーを読み取るポート（vuoi exec 用）。作業ツリーは変更しない。"""

    @abstractmethod
    def locate(self, path: Path) -> Workspace:
        """path を含む作業ツリーを返す。git リポジトリでなければ WorkspaceError。"""

    @abstractmethod
    def changed_files(self, root: Path) -> list[str]:
        """作業ツリーの未コミットの変更（git status --porcelain の各行）。
        未追跡のファイルも含む。読めなければ WorkspaceError。"""
