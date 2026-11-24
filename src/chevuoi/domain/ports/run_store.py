from __future__ import annotations

from abc import ABC, abstractmethod

from chevuoi.domain.entities.exec_run import ExecRun, RunFiles


class RunStore(ABC):
    """バックグラウンド実行の記録を永続化するポート。

    MCP サーバーが再起動しても、実行中・終了済みの実行を引き続き参照できるようにする。
    """

    @abstractmethod
    def create(self, run: ExecRun, brief: str) -> RunFiles:
        """記録とブリーフを書き、実行に使うファイルの場所を返す。"""

    @abstractmethod
    def save(self, run: ExecRun) -> None:
        """既存の記録を上書きする（pid の記入・取り消しの記録）。"""

    @abstractmethod
    def get(self, run_id: str) -> ExecRun:
        """記録を返す。無ければ RunNotFound。"""

    @abstractmethod
    def list(self) -> list[ExecRun]:
        """すべての記録。順序は問わない。読めない記録は飛ばす。"""

    @abstractmethod
    def files(self, run_id: str) -> RunFiles:
        """実行に使うファイルの場所。"""

    @abstractmethod
    def read_result(self, run_id: str) -> str | None:
        """結果の JSON の文字列。まだ無ければ None。"""

    @abstractmethod
    def log_tail(self, run_id: str, lines: int) -> str:
        """ログの末尾 lines 行。無ければ空文字列。"""
