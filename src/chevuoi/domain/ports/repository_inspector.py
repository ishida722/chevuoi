from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from chevuoi.domain.entities.project import Project


class RepositoryInspector(ABC):
    """プロジェクトのリポジトリを読み取るだけのポート。作業ツリーを汚さない。

    トリアージ以外（起票時の base_commit 記録）でも使う。
    """

    @abstractmethod
    def refresh(self, project: Project) -> bool:
        """ベース参照を最新化する（リモート追跡参照の取得）。

        トリアージランの開始時にプロジェクトごとに 1 回だけ呼ぶ。失敗しても例外は
        投げず False を返す（呼び側はそのプロジェクトの解決済み判定を見送る）。
        起票時には呼ばない（記録するのは「そのとき見ていたベース」で足りる）。
        """

    @abstractmethod
    def base_commit(self, project: Project) -> str:
        """ベース参照（Project.base_ref。既定 origin/HEAD）の現在のコミット SHA。
        解決できなければ空文字を返す（例外は投げない）。"""

    @abstractmethod
    def path_exists(self, project: Project, path: str) -> bool:
        """ベース参照にそのパスが存在するか（作業ツリーではなく ref を見る）。"""

    @abstractmethod
    def changed_since(self, project: Project, path: str, since: str) -> bool:
        """since 以降、ベース参照でそのパスに変更があったか。since が無効なら True。"""

    @abstractmethod
    def base_checkout(self, project: Project) -> Path:
        """ベース参照を detached HEAD で置いた読み取り専用チェックアウトを用意して返す。
        プロジェクトごとに使い回し、ブランチを作らないのでカード用 worktree と衝突しない。
        用意できなければ WorktreeError を投げる。"""
