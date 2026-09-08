from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class RepositoryLocator(ABC):
    """ローカルのリポジトリパスから、そのリポジトリの GitHub 上の名前を引く出力ポート。"""

    @abstractmethod
    def locate(self, path: Path) -> str | None:
        """"owner/name" を返す。GitHub のリモートが無い・解釈できない場合は None。"""
