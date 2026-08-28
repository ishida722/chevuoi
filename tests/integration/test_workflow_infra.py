"""FsWorkflowScanner / PythonWorkflowLoader の結合テスト（tmp_path に実ファイル）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from vuoi_sdk import Runner, RunResult

from chevuoi.domain.ports.llm_factory import LlmFactory
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow, LoadFailure
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.workflows.fs_workflow_scanner import FsWorkflowScanner
from chevuoi.infrastructure.workflows.python_workflow_loader import (
    NAMESPACE,
    PythonWorkflowLoader,
)

VALID_TOML = 'api_version = 1\nsummary = "テスト用"\n'

VALID_PY = """\
from vuoi_sdk import BaseState, StateGraph, START, END


def build(ctx):
    g = StateGraph(BaseState)
    g.add_node("noop", lambda state: {})
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    return g
"""


class FakeLlmFactory(LlmFactory):
    def create(self):
        return object()


class FakeRunner(Runner):
    def run(self, prompt, *, cwd=None, session_id=None):
        return RunResult(ok=True, output=f"fake: {prompt}")


@pytest.fixture
def workflows_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workflows"
    d.mkdir()
    return d


@pytest.fixture
def config(workflows_dir: Path) -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k",
            api_token="t",
            ready_list_id="r",
            in_progress_list_id="p",
            in_review_list_id="v",
        ),
        projects={},
        worktree_root=workflows_dir.parent,
        workflows_dir=workflows_dir,
        workflow_defaults={"shared": 1},
    )


@pytest.fixture(autouse=True)
def clean_global_state():
    """ローダはグローバル状態に触れるため、前後の差分ゼロを表明する。"""
    saved_path = list(sys.path)
    saved_modules = set(sys.modules)
    yield
    for key in set(sys.modules) - saved_modules:
        if key == NAMESPACE or key.startswith(NAMESPACE + "."):
            del sys.modules[key]
    assert sys.path == saved_path


def write_workflow(root: Path, name: str, toml: str = VALID_TOML, py: str = VALID_PY):
    d = root / name
    d.mkdir()
    (d / "workflow.toml").write_text(toml, encoding="utf-8")
    (d / "workflow.py").write_text(py, encoding="utf-8")
    return d


class TestScanner:
    def test_valid_workflow(self, workflows_dir, config):
        write_workflow(workflows_dir, "hello")
        result = FsWorkflowScanner(config).scan()
        assert set(result.metas) == {"hello"}
        assert result.invalid == {}
        meta = result.metas["hello"]
        assert meta.entry_path == workflows_dir / "hello" / "workflow.py"

    def test_underscore_and_files_skipped(self, workflows_dir, config):
        write_workflow(workflows_dir, "_draft")
        (workflows_dir / "stray.py").write_text("", encoding="utf-8")
        result = FsWorkflowScanner(config).scan()
        assert result.metas == {} and result.invalid == {}

    def test_broken_toml(self, workflows_dir, config):
        write_workflow(workflows_dir, "broken", toml="api_version = [")
        result = FsWorkflowScanner(config).scan()
        assert "解析に失敗" in result.invalid["broken"]

    def test_unknown_field(self, workflows_dir, config):
        write_workflow(
            workflows_dir, "typo", toml=VALID_TOML + 'when_to_used = "x"\n'
        )
        result = FsWorkflowScanner(config).scan()
        assert "when_to_used" in result.invalid["typo"]

    def test_name_in_toml_rejected(self, workflows_dir, config):
        write_workflow(workflows_dir, "dup", toml=VALID_TOML + 'name = "other"\n')
        result = FsWorkflowScanner(config).scan()
        assert "name" in result.invalid["dup"]

    def test_missing_toml_and_entry(self, workflows_dir, config):
        (workflows_dir / "no_toml").mkdir()
        d = workflows_dir / "no_entry"
        d.mkdir()
        (d / "workflow.toml").write_text(VALID_TOML, encoding="utf-8")
        result = FsWorkflowScanner(config).scan()
        assert "workflow.toml" in result.invalid["no_toml"]
        assert "workflow.py" in result.invalid["no_entry"]

    def test_missing_dir_returns_empty(self, config):
        config = config.model_copy(update={"workflows_dir": Path("/nonexistent/x")})
        result = FsWorkflowScanner(config).scan()
        assert result.metas == {} and result.invalid == {}


class TestLoader:
    def load(self, config, workflows_dir, name):
        meta = FsWorkflowScanner(config).scan().metas[name]
        return PythonWorkflowLoader(config, FakeLlmFactory(), FakeRunner()).load(meta)

    def test_success_with_relative_import(self, workflows_dir, config):
        d = write_workflow(
            workflows_dir,
            "hello",
            py="from . import prompts\n" + VALID_PY,
        )
        (d / "prompts.py").write_text('PROMPT = "hi"\n', encoding="utf-8")
        loaded = self.load(config, workflows_dir, "hello")
        assert isinstance(loaded, LoadedWorkflow)
        assert loaded.graph.name == "hello"

    def test_settings_merged_into_ctx(self, workflows_dir, config):
        write_workflow(
            workflows_dir,
            "conf",
            toml=VALID_TOML + "[settings]\nlocal = 2\n",
            py="""\
from vuoi_sdk import BaseState, StateGraph, START, END

SEEN = {}


def build(ctx):
    SEEN.update(ctx.settings)
    g = StateGraph(BaseState)
    g.add_node("noop", lambda state: {})
    g.add_edge(START, "noop")
    g.add_edge("noop", END)
    return g
""",
        )
        loaded = self.load(config, workflows_dir, "conf")
        assert isinstance(loaded, LoadedWorkflow)
        module = sys.modules[f"{NAMESPACE}.conf"]
        assert module.SEEN == {"shared": 1, "local": 2}

    def test_missing_build(self, workflows_dir, config):
        write_workflow(workflows_dir, "nobuild", py="x = 1\n")
        failure = self.load(config, workflows_dir, "nobuild")
        assert isinstance(failure, LoadFailure)
        assert "build" in failure.traceback

    def test_returning_compiled_graph_is_error(self, workflows_dir, config):
        write_workflow(
            workflows_dir,
            "compiled",
            py=VALID_PY.replace("return g", "return g.compile()"),
        )
        failure = self.load(config, workflows_dir, "compiled")
        assert isinstance(failure, LoadFailure)
        assert "StateGraph" in failure.traceback

    def test_sys_exit_is_captured(self, workflows_dir, config):
        write_workflow(workflows_dir, "exiting", py="import sys\nsys.exit(1)\n")
        failure = self.load(config, workflows_dir, "exiting")
        assert isinstance(failure, LoadFailure)
        assert "SystemExit" in failure.traceback

    def test_failure_purges_modules_and_retry_succeeds(self, workflows_dir, config):
        d = write_workflow(workflows_dir, "flaky", py="raise RuntimeError('boom')\n")
        failure = self.load(config, workflows_dir, "flaky")
        assert isinstance(failure, LoadFailure)
        assert f"{NAMESPACE}.flaky" not in sys.modules
        (d / "workflow.py").write_text(VALID_PY, encoding="utf-8")
        loaded = self.load(config, workflows_dir, "flaky")
        assert isinstance(loaded, LoadedWorkflow)
