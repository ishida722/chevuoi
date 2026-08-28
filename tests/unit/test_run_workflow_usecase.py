"""RunWorkflowUsecase / LangGraphExecutor のテスト。"""

from __future__ import annotations

import pytest

from vuoi_sdk import END, START, BaseState, StateGraph

from chevuoi.application.usecases.run_workflow_usecase import RunWorkflowUsecase
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.exceptions import WorkflowNotFound
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow, WorkflowLoader
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner
from chevuoi.infrastructure.workflows.langgraph_executor import LangGraphExecutor


def make_meta(name: str) -> WorkflowMeta:
    return WorkflowMeta(
        name=name,
        path=f"/x/{name}",
        entry_path=f"/x/{name}/workflow.py",
        api_version=1,
        summary="テスト",
    )


def echo_graph() -> LoadedWorkflow:
    """受け取ったメッセージ本文を加工して返すグラフ。"""
    from langchain_core.messages import AIMessage

    g = StateGraph(BaseState)
    g.add_node(
        "echo",
        lambda state: {
            "messages": [
                AIMessage(f"echo: {state['messages'][-1].content}")
                if state["messages"]
                else AIMessage("empty")
            ]
        },
    )
    g.add_edge(START, "echo")
    g.add_edge("echo", END)
    return LoadedWorkflow(name="echo", graph=g.compile(name="echo"))


class FakeScanner(WorkflowScanner):
    def __init__(self, metas: dict[str, WorkflowMeta]) -> None:
        self._metas = metas

    def scan(self) -> ScanResult:
        return ScanResult(metas=self._metas)


class FakeLoader(WorkflowLoader):
    def __init__(self, loaded: LoadedWorkflow) -> None:
        self._loaded = loaded

    def load(self, meta: WorkflowMeta) -> LoadedWorkflow:
        return self._loaded


def make_usecase() -> RunWorkflowUsecase:
    registry = WorkflowRegistry(
        FakeScanner({"echo": make_meta("echo")}), FakeLoader(echo_graph())
    )
    return RunWorkflowUsecase(registry, LangGraphExecutor())


class TestRunWorkflowUsecase:
    def test_runs_and_returns_last_message(self):
        result = make_usecase().execute("echo", "こんにちは")
        assert result.output == "echo: こんにちは"
        assert "messages" in result.state

    def test_empty_message_starts_with_no_messages(self):
        assert make_usecase().execute("echo", "").output == "empty"

    def test_unknown_name_raises(self):
        with pytest.raises(WorkflowNotFound):
            make_usecase().execute("nope", "x")
