"""vuoi exec（今いる作業ツリーでワークフローを 1 回実行する）の仕様。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from vuoi_sdk import END, START, BaseState, StateGraph, WorkflowContext

from chevuoi.application.usecases.exec_usecase import ExecOutcome, ExecUsecase
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.exceptions import WorkflowNotFound, WorkspaceError
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow
from chevuoi.domain.ports.workspace_inspector import Workspace, WorkspaceInspector
from chevuoi.infrastructure.config.settings import ProjectConfig
from chevuoi.infrastructure.workflows.langgraph_executor import LangGraphExecutor
from chevuoi.interfaces.cli import main as cli_main
from tests.integration.test_workflow_infra import FakeRunner
from tests.unit.fakes import make_config
from tests.unit.test_run_workflow_usecase import FakeLoader, FakeScanner, make_meta

ROOT = Path("/work/repo")


class State(BaseState, total=False):
    result: str
    blocked: str


class FakeInspector(WorkspaceInspector):
    def __init__(
        self,
        workspace: Workspace | None = None,
        changed: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        self._workspace = workspace or Workspace(root=ROOT, main_root=ROOT)
        self.changed = list(changed or [])
        self._error = error
        self.located: list[Path] = []

    def locate(self, path: Path) -> Workspace:
        self.located.append(path)
        if self._error:
            raise WorkspaceError(self._error)
        return self._workspace

    def changed_files(self, root: Path) -> list[str]:
        return list(self.changed)


def recording_graph(seen: dict, *, blocked: str = "", touch: FakeInspector | None = None):
    """ctx から見える値を記録し、最後に結果を返すグラフ。"""
    ctx = WorkflowContext(llm=None, settings={}, logger=None, runner=FakeRunner())

    def node(state):
        seen["brief"] = state["messages"][-1].content if state["messages"] else ""
        seen["workdir"] = ctx.workdir
        seen["project"] = ctx.project
        seen["has_changes_before"] = ctx.has_changes()
        if touch is not None:
            touch.changed.append("?? new.py")
        seen["has_changes_after"] = ctx.has_changes()
        ctx.propose("別件のバグ", body="根拠", kind="bug")
        return {"messages": [AIMessage("完了しました")], "result": "要約", "blocked": blocked}

    g = StateGraph(State)
    g.add_node("work", node)
    g.add_edge(START, "work")
    g.add_edge("work", END)
    return LoadedWorkflow(name="rec", graph=g.compile(name="rec"))


def make_usecase(graph, inspector: FakeInspector, projects=None) -> ExecUsecase:
    registry = WorkflowRegistry(FakeScanner({"rec": make_meta("rec")}), FakeLoader(graph))
    config = make_config(projects=projects or {})
    return ExecUsecase(registry, LangGraphExecutor(), inspector, config)


class TestExecUsecase:
    def test_runs_in_workspace_root_with_brief(self):
        """ブリーフを入力に、作業ツリーのルートを workdir として実行すること。"""
        seen: dict = {}
        inspector = FakeInspector()
        outcome = make_usecase(recording_graph(seen), inspector).execute(
            "rec", "方針です", cwd=ROOT / "sub"
        )
        assert inspector.located == [ROOT / "sub"]
        assert seen["brief"] == "方針です"
        assert seen["workdir"] == ROOT
        assert outcome.status == "done"
        assert outcome.summary == "要約"
        assert outcome.output == "完了しました"
        assert outcome.workdir == str(ROOT)

    def test_blocked_result(self):
        seen: dict = {}
        outcome = make_usecase(recording_graph(seen, blocked="テスト失敗"), FakeInspector()).execute(
            "rec", "x", cwd=ROOT
        )
        assert outcome.status == "blocked"
        assert outcome.blocked == "テスト失敗"

    def test_project_from_config_by_root(self):
        """作業ツリーのルートが設定のパスと一致すれば、そのテストコマンドを渡すこと。"""
        seen: dict = {}
        projects = {"app": ProjectConfig(path=ROOT, test_commands=["pytest -q"])}
        outcome = make_usecase(recording_graph(seen), FakeInspector(), projects).execute(
            "rec", "x", cwd=ROOT
        )
        assert seen["project"].name == "app"
        assert seen["project"].path == ROOT
        assert seen["project"].test_commands == ("pytest -q",)
        assert outcome.project == "app"
        assert outcome.test_commands == ["pytest -q"]

    def test_project_from_config_by_main_root_in_worktree(self):
        """git worktree の中では、本体のパスで設定と照合し、workdir は worktree のままにすること。"""
        seen: dict = {}
        wt = Path("/work/wt/feature")
        inspector = FakeInspector(Workspace(root=wt, main_root=ROOT))
        projects = {"app": ProjectConfig(path=ROOT, test_commands=["pytest -q"])}
        make_usecase(recording_graph(seen), inspector, projects).execute("rec", "x", cwd=wt)
        assert seen["project"].name == "app"
        assert seen["project"].path == wt
        assert seen["workdir"] == wt

    def test_cli_test_commands_override_config(self):
        seen: dict = {}
        projects = {"app": ProjectConfig(path=ROOT, test_commands=["pytest -q"])}
        make_usecase(recording_graph(seen), FakeInspector(), projects).execute(
            "rec", "x", cwd=ROOT, test_commands=["make test"]
        )
        assert seen["project"].test_commands == ("make test",)

    def test_unknown_project_with_test_commands_uses_repo_name(self):
        seen: dict = {}
        outcome = make_usecase(recording_graph(seen), FakeInspector()).execute(
            "rec", "x", cwd=ROOT, test_commands=["make test"]
        )
        assert seen["project"].name == "repo"
        assert seen["project"].test_commands == ("make test",)
        assert outcome.project is None

    def test_unknown_project_without_test_commands_passes_no_project(self):
        seen: dict = {}
        make_usecase(recording_graph(seen), FakeInspector()).execute("rec", "x", cwd=ROOT)
        assert seen["project"] is None

    def test_has_changes_reflects_working_tree(self):
        """has_changes は作業ツリーの未コミットの変更の有無を返すこと。"""
        seen: dict = {}
        inspector = FakeInspector()
        outcome = make_usecase(recording_graph(seen, touch=inspector), inspector).execute(
            "rec", "x", cwd=ROOT
        )
        assert seen["has_changes_before"] is False
        assert seen["has_changes_after"] is True
        assert outcome.changed_files == ["?? new.py"]

    def test_proposals_are_returned_not_issued(self):
        seen: dict = {}
        outcome = make_usecase(recording_graph(seen), FakeInspector()).execute("rec", "x", cwd=ROOT)
        assert [(p.title, p.kind) for p in outcome.proposals] == [("別件のバグ", "bug")]

    def test_unknown_workflow_raises_before_touching_workspace(self):
        inspector = FakeInspector()
        with pytest.raises(WorkflowNotFound):
            make_usecase(recording_graph({}), inspector).execute("nope", "x", cwd=ROOT)
        assert inspector.located == []

    def test_not_a_repository_raises(self):
        seen: dict = {}
        with pytest.raises(WorkspaceError):
            make_usecase(recording_graph(seen), FakeInspector(error="not a git repo")).execute(
                "rec", "x", cwd=ROOT
            )
        assert seen == {}


class FakeExecUsecase:
    def __init__(self, outcome: ExecOutcome | None = None, error: Exception | None = None):
        self.calls: list[dict] = []
        self._outcome = outcome
        self._error = error

    def execute(self, name, brief, *, cwd, test_commands=()):
        self.calls.append(
            {"name": name, "brief": brief, "cwd": cwd, "test_commands": list(test_commands)}
        )
        if self._error is not None:
            raise self._error
        return self._outcome


class StubInjector:
    def __init__(self, usecase: FakeExecUsecase) -> None:
        self._usecase = usecase

    def get(self, cls):
        assert cls is ExecUsecase
        return self._usecase


def run_cli(argv: list[str], usecase: FakeExecUsecase) -> int:
    with patch.object(cli_main, "build_injector", return_value=StubInjector(usecase)):
        return cli_main.main(argv)


@pytest.fixture
def brief(tmp_path: Path) -> Path:
    p = tmp_path / "brief.md"
    p.write_text("# 目的\nテスト", encoding="utf-8")
    return p


class TestExecCommand:
    def test_done_writes_json_and_exits_zero(self, tmp_path, brief):
        usecase = FakeExecUsecase(
            ExecOutcome(status="done", workflow="dev", workdir="/w", summary="できた")
        )
        out = tmp_path / "result.json"
        code = run_cli(
            ["exec", "dev", "--brief", str(brief), "--json", str(out),
             "--test-command", "a", "--test-command", "b"],
            usecase,
        )
        assert code == 0
        assert usecase.calls[0]["brief"] == "# 目的\nテスト"
        assert usecase.calls[0]["test_commands"] == ["a", "b"]
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "done" and data["summary"] == "できた"

    def test_blocked_exits_two(self, brief):
        usecase = FakeExecUsecase(ExecOutcome(status="blocked", workflow="dev", blocked="x"))
        assert run_cli(["exec", "dev", "--brief", str(brief)], usecase) == 2

    def test_domain_error_is_reported_as_error(self, tmp_path, brief, capsys):
        usecase = FakeExecUsecase(error=WorkflowNotFound("dev が見つかりません"))
        out = tmp_path / "result.json"
        code = run_cli(["exec", "dev", "--brief", str(brief), "--json", str(out)], usecase)
        assert code == 1
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["status"] == "error" and "dev が見つかりません" in data["error"]
        assert "dev が見つかりません" in capsys.readouterr().err

    def test_unexpected_error_is_reported_as_error(self, tmp_path, brief):
        usecase = FakeExecUsecase(error=RuntimeError("boom"))
        out = tmp_path / "result.json"
        assert run_cli(["exec", "dev", "--brief", str(brief), "--json", str(out)], usecase) == 1
        assert "boom" in json.loads(out.read_text(encoding="utf-8"))["error"]

    def test_missing_brief_file_is_error_without_running(self, tmp_path):
        usecase = FakeExecUsecase()
        out = tmp_path / "result.json"
        code = run_cli(
            ["exec", "dev", "--brief", str(tmp_path / "none.md"), "--json", str(out)], usecase
        )
        assert code == 1
        assert usecase.calls == []
        assert json.loads(out.read_text(encoding="utf-8"))["status"] == "error"
