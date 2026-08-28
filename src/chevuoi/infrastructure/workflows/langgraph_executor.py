from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from langchain_core.messages import HumanMessage

from vuoi_sdk import bind_workdir

from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow


class LangGraphExecutor(GraphExecutor):
    """CompiledStateGraph を invoke する。初期 state は BaseState 契約に従い
    messages のみ渡す（拡張キーはワークフロー側が既定値を扱う）。
    workdir は SDK の ContextVar に束縛し、ワークフローは ctx.workdir で読む。
    """

    def execute(
        self, workflow: LoadedWorkflow, message: str, *, workdir: Path | None = None
    ) -> ExecutionResult:
        initial = [HumanMessage(message)] if message else []
        scope = bind_workdir(workdir) if workdir is not None else nullcontext()
        with scope:
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
        )
