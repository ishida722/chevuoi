from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import Path

from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from vuoi_sdk import ProjectInfo, Proposal, bind_project, bind_proposals, bind_workdir

from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.task_proposal import TaskProposal
from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow

logger = logging.getLogger(__name__)


class LangGraphExecutor(GraphExecutor):
    """CompiledStateGraph を invoke する。初期 state は BaseState 契約に従い
    messages のみ渡す（拡張キーはワークフロー側が既定値を扱う）。
    workdir / project は SDK の ContextVar に束縛し、ワークフローは ctx.workdir / ctx.project で読む。
    ctx.propose の申告も同じ流儀で実行ごとに収集し、ExecutionResult.proposals へ写す。
    """

    def execute(
        self,
        workflow: LoadedWorkflow,
        message: str,
        *,
        workdir: Path | None = None,
        project: Project | None = None,
    ) -> ExecutionResult:
        initial = [HumanMessage(message)] if message else []
        sink: list[Proposal] = []
        with ExitStack() as stack:
            if workdir is not None:
                stack.enter_context(bind_workdir(workdir))
            if project is not None:
                stack.enter_context(bind_project(_to_info(project)))
            stack.enter_context(bind_proposals(sink))
            state = workflow.graph.invoke({"messages": initial})
        messages = state.get("messages", [])
        # グラフが追加したメッセージだけを出力とする（入力の echo を防ぐ）
        produced = messages[len(initial) :]
        output = str(produced[-1].content) if produced else ""
        return ExecutionResult(
            output=output,
            state=dict(state),
            blocked=str(state.get("blocked") or ""),
            summary=str(state.get("result") or output),
            proposals=_to_proposals(sink),
        )


def _to_info(project: Project) -> ProjectInfo:
    """ドメインの Project を SDK の契約型へ写す（SDK はホストに依存しない）。"""
    return ProjectInfo(
        name=project.tag.value,
        path=project.repo_path,
        test_commands=tuple(project.test_commands),
    )


def _to_proposals(sink: list[Proposal]) -> list[TaskProposal]:
    """SDK の Proposal をドメインの TaskProposal へ写す。検証に落ちた申告は警告して落とす。"""
    proposals: list[TaskProposal] = []
    for p in sink:
        try:
            proposals.append(
                TaskProposal(title=p.title, body=p.body, kind=p.kind, evidence=p.evidence)
            )
        except ValidationError as e:
            logger.warning("不正な proposal を捨てます: %r (%s)", p.title, e.errors()[0]["msg"])
    return proposals
