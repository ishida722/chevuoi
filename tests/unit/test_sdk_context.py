from pathlib import Path

from vuoi_sdk import (
    END,
    START,
    BaseState,
    ProjectInfo,
    RunResult,
    Runner,
    StateGraph,
    WorkflowContext,
    bind_project,
    bind_workdir,
)

from chevuoi.domain.entities.project import Project
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.workflows.langgraph_executor import LangGraphExecutor


class NoopRunner(Runner):
    def run(self, prompt, *, cwd=None, session_id=None, allowed_tools=None):
        return RunResult(ok=True, output=prompt)


def make_ctx() -> WorkflowContext:
    return WorkflowContext(llm=None, settings={}, logger=None, runner=NoopRunner())


def test_project_is_none_outside_binding():
    assert make_ctx().project is None


def test_bind_project_exposes_project_info():
    ctx = make_ctx()
    info = ProjectInfo(name="MIRAI", path=Path("/repo/mirai"), test_commands=("uv run pytest",))
    with bind_project(info), bind_workdir(Path("/tmp/wt")):
        assert ctx.project is info
        assert ctx.project.test_commands == ("uv run pytest",)
        assert ctx.workdir == Path("/tmp/wt")
    assert ctx.project is None


def test_executor_binds_project_for_workflow():
    ctx = make_ctx()
    seen: dict = {}

    class State(BaseState):
        pass

    def probe(state: State):
        seen["project"] = ctx.project
        return {}

    g = StateGraph(State)
    g.add_node("probe", probe)
    g.add_edge(START, "probe")
    g.add_edge("probe", END)
    workflow = LoadedWorkflow(name="probe", graph=g.compile())
    project = Project(
        tag=ProjectTag(value="MIRAI"), repo_path=Path("/repo/mirai"), test_commands=["make test"]
    )
    LangGraphExecutor().execute(workflow, "hi", workdir=Path("/tmp/wt"), project=project)
    assert seen["project"] == ProjectInfo(
        name="MIRAI", path=Path("/repo/mirai"), test_commands=("make test",)
    )
    assert ctx.project is None
