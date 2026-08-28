from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.workflow_meta import ScanResult


class WorkflowScanner(ABC):
    """探索ディレクトリを走査し workflow.toml を解析する。コードは実行しない。"""

    @abstractmethod
    def scan(self) -> ScanResult: ...
