import logging
from pathlib import Path

import pytest

from vuoi_sdk import (
    END,
    PROPOSAL_PROMPT,
    START,
    BaseState,
    ProjectInfo,
    Proposal,
    RunResult,
    Runner,
    StateGraph,
    WorkflowContext,
    bind_has_changes,
    bind_project,
    bind_proposals,
    bind_workdir,
)

from chevuoi.domain.entities.project import Project
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.workflows.langgraph_executor import LangGraphExecutor


class NoopRunner(Runner):
    def run(
        self, prompt, *, cwd=None, session_id=None, allowed_tools=None, model=None,
        permission_mode=None,
    ):
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


class TestHasChanges:
    """ctx.has_changes() は「ホストが終端処理で『変更なし』とするか」と同じ事実を返す。"""

    def test_outside_binding_is_treated_as_changed_with_warning(self, caplog):
        """判定が束縛されていない実行では、例外にせず警告を出して「変更あり」になること。

        False に倒すと、成果がある回をワークフローが「変更なし」と誤って扱いうる。
        """
        ctx = make_ctx()
        with caplog.at_level(logging.WARNING, logger="vuoi_sdk"):
            assert ctx.has_changes() is True
        # 文言ではなく「警告として記録されたこと」を見る（言い換えで落とさない）
        assert [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_binding_does_not_leak_after_exit(self):
        """束縛を抜けたあとは、束縛外の振る舞い（変更あり扱い）に戻ること。"""
        ctx = make_ctx()
        with bind_has_changes(lambda: False):
            assert ctx.has_changes() is False
        assert ctx.has_changes() is True

    def test_is_evaluated_on_every_call(self):
        """判定は呼ぶたびに評価されること（キャッシュしない）。

        ノードが変更を加える前後で答えが変わるので、初回の結果を固定してはならない。
        """
        ctx = make_ctx()
        answers = iter([False, True])
        with bind_has_changes(lambda: next(answers)):
            assert ctx.has_changes() is False
            assert ctx.has_changes() is True

    def test_probe_failure_propagates(self):
        """判定そのものが失敗したときは、握りつぶさず例外を伝播すること。

        「判定できない」を黙って「変更なし」に倒すと成果を捨てる。
        """
        def broken() -> bool:
            raise RuntimeError("ベースブランチを解決できませんでした")

        ctx = make_ctx()
        with bind_has_changes(broken), pytest.raises(RuntimeError):
            ctx.has_changes()


def _run_probing_workflow(ctx: WorkflowContext, seen: dict, **execute_kwargs) -> None:
    """ノードから ctx.has_changes() を 1 回読むだけのグラフを executor 経由で回す。"""

    class State(BaseState):
        pass

    def probe(state: State):
        seen["has_changes"] = ctx.has_changes()
        return {}

    g = StateGraph(State)
    g.add_node("probe", probe)
    g.add_edge(START, "probe")
    g.add_edge("probe", END)
    workflow = LoadedWorkflow(name="probe", graph=g.compile())
    LangGraphExecutor().execute(workflow, "hi", **execute_kwargs)


def test_executor_binds_has_changes_for_workflow():
    """executor に渡した判定が、グラフのノードから ctx.has_changes() として見え、
    実行が終われば束縛が解除されること。"""
    ctx = make_ctx()
    seen: dict = {}
    _run_probing_workflow(ctx, seen, has_changes=lambda: False)
    assert seen["has_changes"] is False
    assert ctx.has_changes() is True  # 束縛は実行外へ漏れない


def test_workflow_sees_changes_when_executor_gets_no_probe():
    """判定を渡さない実行（vuoi workflow run など、対象の worktree が無い実行）では、
    ノードが「変更あり」を見ること。

    ここで「変更なし」を見せると、ワークフローが成果のある回をレビューや要約ごと
    飛ばしうる。
    """
    ctx = make_ctx()
    seen: dict = {}
    _run_probing_workflow(ctx, seen)
    assert seen["has_changes"] is True


class TestPropose:
    def test_outside_binding_is_dropped_with_warning(self, caplog):
        ctx = make_ctx()
        with caplog.at_level(logging.WARNING, logger="vuoi_sdk"):
            ctx.propose("捨てられる")
        assert "捨てます" in caplog.text

    def test_collected_into_sink(self):
        ctx = make_ctx()
        sink: list[Proposal] = []
        with bind_proposals(sink):
            ctx.propose(" flaky ", body="b", kind="bug", evidence=["tests/x.py:10"])
        assert sink == [Proposal(title="flaky", body="b", kind="bug", evidence=("tests/x.py:10",))]

    def test_parallel_nodes_share_the_same_sink(self):
        ctx = make_ctx()

        class State(BaseState):
            pass

        g = StateGraph(State)
        for name in ("a", "b", "c"):
            g.add_node(name, lambda state, name=name: ctx.propose(name) or {})
            g.add_edge(START, name)
            g.add_edge(name, END)
        workflow = LoadedWorkflow(name="fan", graph=g.compile())
        result = LangGraphExecutor().execute(workflow, "hi")
        assert sorted(p.title for p in result.proposals) == ["a", "b", "c"]

    def test_executor_drops_invalid_proposals(self):
        ctx = make_ctx()
        g = StateGraph(BaseState)
        g.add_node("n", lambda s: (ctx.propose("   "), ctx.propose("ok"))[0] or {})
        g.add_edge(START, "n")
        g.add_edge("n", END)
        result = LangGraphExecutor().execute(LoadedWorkflow(name="n", graph=g.compile()), "")
        assert [p.title for p in result.proposals] == ["ok"]

    def test_executions_are_isolated(self):
        ctx = make_ctx()
        g = StateGraph(BaseState)
        g.add_node("n", lambda s: ctx.propose("one") or {})
        g.add_edge(START, "n")
        g.add_edge("n", END)
        wf = LoadedWorkflow(name="n", graph=g.compile())
        first = LangGraphExecutor().execute(wf, "")
        second = LangGraphExecutor().execute(wf, "")
        assert len(first.proposals) == 1 and len(second.proposals) == 1


class TestProposeFromOutput:
    def _run(self, text: str) -> tuple[int, list[Proposal]]:
        sink: list[Proposal] = []
        with bind_proposals(sink):
            n = make_ctx().propose_from_output(text)
        return n, sink

    def test_extracts_blocks(self):
        text = (
            "作業しました。\n```vuoi-proposal\n"
            '{"title": "flaky", "kind": "bug", "evidence": ["t.py:1"], "body": "b"}\n'
            "```\nつづき\n```vuoi-proposal\n{\"title\": \"debt\"}\n```\n"
        )
        n, sink = self._run(text)
        assert n == 2
        assert sink[0] == Proposal(title="flaky", body="b", kind="bug", evidence=("t.py:1",))
        assert sink[1] == Proposal(title="debt")

    def test_broken_json_is_skipped(self, caplog):
        with caplog.at_level(logging.WARNING, logger="vuoi_sdk"):
            n, sink = self._run("```vuoi-proposal\n{not json}\n```")
        assert n == 0 and sink == [] and "壊れています" in caplog.text

    def test_missing_title_and_bad_kind(self):
        n, sink = self._run(
            '```vuoi-proposal\n{"body": "x"}\n```\n'
            '```vuoi-proposal\n{"title": "t", "kind": "weird"}\n```'
        )
        assert n == 1 and sink[0].kind == "chore"

    def test_no_blocks(self):
        assert self._run("なにもない") == (0, [])

    def test_prompt_mentions_block_name(self):
        assert "```vuoi-proposal" in PROPOSAL_PROMPT

    def test_placeholder_title_and_same_line_fence(self):
        n, sink = self._run(
            '```vuoi-proposal\n{"title": "..."}\n```\n'
            '```vuoi-proposal\n{"title": "same line"}```'
        )
        assert n == 1 and sink[0].title == "same line"

    def test_string_evidence_is_not_split(self):
        sink: list[Proposal] = []
        with bind_proposals(sink):
            make_ctx().propose("t", evidence="src/a.py:1", kind="weird")
        assert sink[0].evidence == ("src/a.py:1",) and sink[0].kind == "chore"
