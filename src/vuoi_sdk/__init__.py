"""vuoi ワークフローのユーザー契約（SDK）。

ユーザーのワークフローが import する唯一の公開インターフェース。
ホスト本体（chevuoi）には依存しない。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Mapping, Sequence, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

API_VERSION = 1


class BaseState(TypedDict):
    """ホストが読み書きを保証するキー。ユーザーはこれを継承して拡張する。"""

    messages: Annotated[list[BaseMessage], add_messages]


@dataclass(frozen=True)
class RunResult:
    """Runner による Claude Code 1 回の実行結果。失敗も例外ではなくこの型で返る。"""

    ok: bool
    output: str
    session_id: str | None = None
    cost_usd: float | None = None


class Runner(ABC):
    """Claude Code を非対話で 1 回実行するポート。実装とその横断的関心事
    （タイムアウト・ログ・コスト記録）はホストが担い、ワークフローは結果だけを見る。
    """

    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        session_id: str | None = None,
        allowed_tools: Sequence[str] | None = None,
    ) -> RunResult:
        """prompt を実行して結果を返す。

        cwd: 作業ディレクトリ（None ならホストのカレント）。
        session_id: 前回の RunResult.session_id を渡すと文脈を継続する。
        allowed_tools: 許可するツール名（None なら Claude Code の既定）。
        """


_workdir: ContextVar[Path | None] = ContextVar("vuoi_workdir", default=None)


@contextmanager
def bind_workdir(path: Path) -> Iterator[None]:
    """ホストが 1 回の実行に作業ディレクトリを束縛する。ワークフローは ctx.workdir で読む。

    ContextVar なので並列実行でも実行ごとに独立し、コンパイル済みグラフのキャッシュを保てる。
    """
    token = _workdir.set(path)
    try:
        yield
    finally:
        _workdir.reset(token)


@dataclass(frozen=True)
class WorkflowContext:
    """依存性注入。ユーザーは自前で LLM や接続を作らない。

    runner: ノードの主作業（ツールを使うエージェント実行）に使う。
    llm: 軽い 1 発呼び出し（分類・要約など）向け。設定に [llm] が無ければ None。
    workdir: この実行の作業ディレクトリ（カードの worktree など）。
             runner.run(cwd=ctx.workdir) や subprocess の cwd に渡す。
    """

    llm: BaseChatModel | None
    settings: Mapping[str, Any]
    logger: Any
    runner: Runner

    @property
    def workdir(self) -> Path:
        bound = _workdir.get()
        return bound if bound is not None else Path.cwd()


__all__ = [
    "API_VERSION",
    "BaseState",
    "RunResult",
    "Runner",
    "WorkflowContext",
    "bind_workdir",
    "StateGraph",
    "START",
    "END",
]
