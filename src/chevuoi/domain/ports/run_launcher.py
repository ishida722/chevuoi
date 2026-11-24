from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from chevuoi.domain.entities.exec_run import RunFiles


class RunLauncher(ABC):
    """vuoi exec を別プロセスで起動・監視するポート。

    起動したプロセスは呼び出し元（MCP サーバー）が終了しても動き続ける。
    """

    @abstractmethod
    def launch(
        self,
        workflow: str,
        files: RunFiles,
        *,
        cwd: Path,
        test_commands: Sequence[str] = (),
    ) -> int:
        """起動して pid を返す。起動できなければ RunLaunchError。"""

    @abstractmethod
    def is_running(self, pid: int) -> bool:
        """プロセスがまだ動いているか。"""

    @abstractmethod
    def terminate(self, pid: int) -> None:
        """プロセス（とその子プロセス）を止める。既に終了していれば何もしない。"""
