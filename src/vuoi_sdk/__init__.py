"""vuoi ワークフローのユーザー契約（SDK）。

ユーザーのワークフローが import する唯一の公開インターフェース。
ホスト本体（chevuoi）には依存しない。
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Sequence, TypedDict

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
        model: str | None = None,
        permission_mode: str | None = None,
    ) -> RunResult:
        """prompt を実行して結果を返す。

        cwd: 作業ディレクトリ（None ならホストのカレント）。
        session_id: 前回の RunResult.session_id を渡すと文脈を継続する。
        allowed_tools: 許可するツール名（None なら Claude Code の既定）。ツールの事前承認で
                       あって制限ではないので、これだけでは書き込みを防げない。
        model: 使用するモデル。Claude Code のエイリアス（"haiku" / "sonnet" 等）または
               完全名。None または空文字列なら Claude Code の既定に従う。
        permission_mode: Claude Code の権限モード（例: "auto"）。None または空文字列なら
                         既定のままで、非対話（-p）では書き込みを伴うツールが拒否される。
                         その呼び出しが変更を加えることを意図しているかで決める:
                         ファイル編集やコマンド実行をさせるノードは "auto" を渡し、
                         分類・レビューなど読むだけの呼び出しには渡さない。session_id で
                         継続した先も同じ判断をする（継続元が読み取りでも、書き込ませる
                         呼び出しでは渡す必要がある）。
        """


@dataclass(frozen=True)
class ProjectInfo:
    """実行対象プロジェクトの情報（ホストの設定から供給される）。

    test_commands: テストゲートの中身。ゲートを置くか・何回試すかはワークフローが決め、
                   何を実行するかはプロジェクトが決める。
    """

    name: str
    path: Path
    test_commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class Proposal:
    """ワークフローが申告する追加タスク。起票するかどうかはホストが決める。

    作業中に見つけた範囲外の問題（無関係なバグ・技術的負債・先に必要な準備）を
    その場で直さずに申告するための値。ホストが重複排除・上限・冪等性をかけて起票する。
    """

    title: str
    body: str = ""
    kind: Literal["bug", "chore", "spike", "debt"] = "chore"
    evidence: tuple[str, ...] = ()  # 例: ("src/foo.py:142",)


PROPOSAL_PROMPT = """\
作業中に範囲外の問題（無関係なバグ・技術的負債・先に必要な準備）を見つけたら、
その場で直さずに次の形式で報告し、本来の作業を続けてください:

```vuoi-proposal
{"title": "...", "kind": "bug|chore|spike|debt", "evidence": ["path:line"], "body": "..."}
```
"""

# 閉じフェンスが同じ行にあっても拾えるよう、改行は要求しない
_PROPOSAL_BLOCK = re.compile(r"```vuoi-proposal\s*\n(.*?)```", re.DOTALL)
_PLACEHOLDER_TITLES = frozenset({"...", "…"})  # PROPOSAL_PROMPT の雛形を復唱した出力
_PROPOSAL_KINDS = ("bug", "chore", "spike", "debt")

_workdir: ContextVar[Path | None] = ContextVar("vuoi_workdir", default=None)
_project: ContextVar[ProjectInfo | None] = ContextVar("vuoi_project", default=None)
_proposals: ContextVar[list[Proposal] | None] = ContextVar("vuoi_proposals", default=None)
_has_changes: ContextVar[Callable[[], bool] | None] = ContextVar(
    "vuoi_has_changes", default=None
)


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


@contextmanager
def bind_project(project: ProjectInfo) -> Iterator[None]:
    """ホストが 1 回の実行に対象プロジェクトを束縛する。ワークフローは ctx.project で読む。"""
    token = _project.set(project)
    try:
        yield
    finally:
        _project.reset(token)


@contextmanager
def bind_has_changes(probe: Callable[[], bool]) -> Iterator[None]:
    """ホストが 1 回の実行に「成果となる変更があるか」の判定を束縛する。

    ワークフローは ctx.has_changes() で読む。ホストが終端処理で「変更なし」と
    するかどうかに使うのと同じ判定を渡すことで、ワークフロー側の分岐（レビューを
    飛ばす等）とホストの終端判定が同じ事実を見る。
    """
    token = _has_changes.set(probe)
    try:
        yield
    finally:
        _has_changes.reset(token)


@contextmanager
def bind_proposals(sink: list[Proposal]) -> Iterator[None]:
    """ホストが 1 回の実行に申告の収集先を束縛する。ワークフローは ctx.propose で積む。

    LangGraph の並列ノードは contextvars をコピーして走るので、同じ実行の申告は同じ
    sink に集まる。ユーザーが自前のスレッドプールをノード内で使うと、そのスレッドには
    収集先が伝わらず申告は捨てられる（警告ログは出る）。
    """
    token = _proposals.set(sink)
    try:
        yield
    finally:
        _proposals.reset(token)


@dataclass(frozen=True)
class WorkflowContext:
    """依存性注入。ユーザーは自前で LLM や接続を作らない。

    runner: ノードの主作業（ツールを使うエージェント実行）に使う。
    llm: 軽い 1 発呼び出し（分類・要約など）向け。設定に [llm] が無ければ None。
    workdir: この実行の作業ディレクトリ（カードの worktree など）。
             runner.run(cwd=ctx.workdir) や subprocess の cwd に渡す。
    project: 対象プロジェクトの情報。カード起点でない実行（vuoi workflow run 等）では None。
    has_changes(): 作業ツリーに成果となる変更があるか。ホストの終端判定と同じ事実。
    """

    llm: BaseChatModel | None
    settings: Mapping[str, Any]
    logger: Any
    runner: Runner

    @property
    def workdir(self) -> Path:
        bound = _workdir.get()
        return bound if bound is not None else Path.cwd()

    @property
    def project(self) -> ProjectInfo | None:
        return _project.get()

    def has_changes(self) -> bool:
        """作業ツリーに成果となる変更があるか。呼ぶたびにホストへ問い合わせる。

        ホストが終端処理で「変更なし」と判定するのと同じ事実を返す。未コミットの変更
        （追跡外ファイル含む）に加え、ベースブランチとの差分＝コミット済みの成果も含む。
        差分ゼロの回にレビューや要約を走らせない、といった分岐に使う。git を自前で
        叩くとホストの判定条件とずれるので、この値に一本化する。

        判定は毎回評価する（キャッシュしない）。ノードが変更を加えた前後で答えは変わる。
        判定できない場合（ベースブランチを解決できない等）はホスト側の例外がそのまま
        伝播する。「判定できない」を黙って「変更なし」に倒すと成果を捨てるため。

        ホストの束縛外（vuoi workflow run など、対象の worktree が無い実行）では
        警告を出して True を返す。ここで False に倒すと、ワークフローが成果のある回を
        「変更なし」と誤って扱いうるため。
        """
        probe = _has_changes.get()
        if probe is None:
            self._log().warning(
                "has_changes の判定が束縛されていないため「変更あり」として扱います"
            )
            return True
        return probe()

    def propose(
        self,
        title: str,
        *,
        body: str = "",
        kind: str = "chore",
        evidence: Sequence[str] = (),
    ) -> None:
        """追加タスクを申告する。収集先が無い実行（束縛外）ではログに残して捨てる。

        起票するかどうか・どこへ・何件までは、ホストが決める。
        """
        if kind not in _PROPOSAL_KINDS:
            self._log().warning("proposal の kind が不正なので chore にします: %r", kind)
            kind = "chore"
        if isinstance(evidence, str):
            evidence = (evidence,)  # str も Sequence[str] なので 1 文字ずつ分解されないよう包む
        proposal = Proposal(
            title=title.strip(),
            body=body,
            kind=kind,  # type: ignore[arg-type]
            evidence=tuple(evidence),
        )
        sink = _proposals.get()
        if sink is None:
            self._log().warning("proposal は収集先がないため捨てます: %s", proposal.title)
            return
        sink.append(proposal)

    def propose_from_output(self, text: str) -> int:
        """runner の出力から ```vuoi-proposal``` ブロックを抜き出して申告する。件数を返す。

        JSON として壊れたブロック、title が無い・型が違うブロックは警告して読み飛ばす。
        """
        count = 0
        for block in _PROPOSAL_BLOCK.findall(text):
            try:
                data = json.loads(block)
            except json.JSONDecodeError as e:
                self._log().warning("vuoi-proposal ブロックの JSON が壊れています: %s", e)
                continue
            if not isinstance(data, dict) or not isinstance(data.get("title"), str):
                self._log().warning("vuoi-proposal ブロックに title がありません: %r", block)
                continue
            title = data["title"].strip()
            if not title or title in _PLACEHOLDER_TITLES:
                self._log().warning("vuoi-proposal ブロックの title が空か雛形のままです: %r", title)
                continue
            kind = data.get("kind", "chore")
            evidence = data.get("evidence", [])
            if not isinstance(evidence, list):
                evidence = []
            body = data.get("body", "")
            self.propose(
                title,
                body=body if isinstance(body, str) else str(body),
                kind=kind,
                evidence=[str(e) for e in evidence],
            )
            count += 1
        return count

    def _log(self) -> Any:
        # ctx.logger は Any（テストでは None を渡している）ので、無ければ SDK のロガーに落とす
        return self.logger or logging.getLogger("vuoi_sdk")


__all__ = [
    "API_VERSION",
    "PROPOSAL_PROMPT",
    "BaseState",
    "ProjectInfo",
    "Proposal",
    "RunResult",
    "Runner",
    "WorkflowContext",
    "bind_has_changes",
    "bind_project",
    "bind_proposals",
    "bind_workdir",
    "StateGraph",
    "START",
    "END",
]
