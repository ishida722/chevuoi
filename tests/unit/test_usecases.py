from pathlib import Path

from chevuoi.application.usecases.gc_usecase import GcUsecase
from chevuoi.application.usecases.process_card_usecase import ProcessCardUsecase
from chevuoi.application.usecases.select_workflow_usecase import SelectWorkflowUsecase
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.routing_decision import RoutingDecision
from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.ports.graph_executor import ExecutionResult
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow, WorkflowLoader
from chevuoi.domain.ports.workflow_router import WorkflowRouter
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner
from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from tests.unit.fakes import FakeCard, FakeExecutor, FakePublisher, FakeWorktreeManager


def make_config(projects: dict[str, Path] | None = None) -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k", api_token="t",
            ready_list_id="ready", in_progress_list_id="doing", in_review_list_id="review",
        ),
        projects=projects if projects is not None else {"MIRAI": Path("/repo/mirai")},
        worktree_root=Path("/tmp/worktrees"),
    )


def meta(name: str, outcome: str = "pr") -> WorkflowMeta:
    return WorkflowMeta(
        name=name, path=f"/x/{name}", entry_path=f"/x/{name}/workflow.py",
        api_version=1, summary="s", outcome=outcome,
    )


class FakeScanner(WorkflowScanner):
    def __init__(self, metas): self.metas = metas
    def scan(self): return ScanResult(metas={m.name: m for m in self.metas})


class FakeLoader(WorkflowLoader):
    def load(self, meta): return LoadedWorkflow(name=meta.name, graph=object())


class FixedRouter(WorkflowRouter):
    def __init__(self, name): self.name = name
    def route(self, card, candidates, *, cwd=None):
        return RoutingDecision(workflow=self.name, confidence="high" if self.name else "low", reason="r")


def make_usecase(
    result: ExecutionResult | None = None,
    *,
    workflow: str | None = "dev",
    outcome: str = "pr",
    projects=None,
    exc=None,
    changes=True,
):
    result = result or ExecutionResult(output="", state={}, summary="やった")
    worktrees = FakeWorktreeManager()
    worktrees.changes = changes
    registry = WorkflowRegistry(FakeScanner([meta("dev", outcome), meta("task", "comment")]), FakeLoader())
    selector = SelectWorkflowUsecase(registry, FixedRouter(workflow))
    executor = FakeExecutor(result, exc=exc)
    publisher = FakePublisher()
    usecase = ProcessCardUsecase(worktrees, selector, registry, executor, publisher, make_config(projects))
    return usecase, worktrees, executor, publisher


class TestProcessCardUsecase:
    def test_pr_outcome_with_changes_publishes_pr(self):
        usecase, worktrees, executor, publisher = make_usecase()
        card = FakeCard("MIRAI ログイン修正")
        usecase.execute(card)
        assert executor.calls[0]["workdir"] == Path("/tmp/wt")
        assert "MIRAI ログイン修正" in executor.calls[0]["message"]
        assert publisher.calls[0]["title"] == "MIRAI ログイン修正"
        assert "やった" in publisher.calls[0]["body"] and card.url in publisher.calls[0]["body"]
        assert card.comments == ["やった\n\n🤖 PR: https://x/pr/1"]
        assert card.moved_to_review

    def test_pr_outcome_without_changes_comments_no_change(self):
        usecase, _, _, publisher = make_usecase(changes=False)
        card = FakeCard("MIRAI 既に直ってる")
        usecase.execute(card)
        assert publisher.calls == []
        assert card.comments[0].startswith("🤖 変更なし:") and card.moved_to_review

    def test_comment_outcome_never_publishes(self):
        usecase, _, _, publisher = make_usecase(workflow="task")
        card = FakeCard("MIRAI PR をレビュー")
        usecase.execute(card)
        assert publisher.calls == []
        assert card.comments == ["🤖 完了:\nやった"] and card.moved_to_review

    def test_blocked_comments_and_keeps_worktree(self):
        usecase, worktrees, _, publisher = make_usecase(
            ExecutionResult(output="", state={}, blocked="テスト2回失敗", summary="x")
        )
        card = FakeCard("MIRAI 難しいやつ")
        usecase.execute(card)
        assert publisher.calls == [] and worktrees.removed == []
        assert card.comments[0].startswith("🤖 blocked: テスト2回失敗") and "/tmp/wt" in card.comments[0]
        assert card.moved_to_review

    def test_abstain_is_needs_human_without_worktree(self):
        usecase, worktrees, executor, _ = make_usecase(workflow=None)
        card = FakeCard("MIRAI なんかいい感じに")
        usecase.execute(card)
        assert worktrees.created == [] and executor.calls == []
        assert card.comments[0].startswith("🤖 needs_human") and card.moved_to_review

    def test_exception_comments_error_and_moves_to_review(self):
        usecase, _, _, _ = make_usecase(exc=RuntimeError("boom"))
        card = FakeCard("MIRAI 壊れる")
        usecase.execute(card)
        assert card.comments == ["🤖 エラー: boom"] and card.moved_to_review

    def test_claim_failure_skips_everything(self):
        usecase, worktrees, _, _ = make_usecase()
        card = FakeCard("MIRAI x", claimable=False)
        usecase.execute(card)
        assert worktrees.created == [] and card.comments == [] and not card.moved_to_review

    def test_unknown_tag_skips_after_claim(self):
        usecase, worktrees, _, _ = make_usecase(projects={})
        card = FakeCard("MIRAI x")
        usecase.execute(card)
        assert worktrees.created == [] and not card.moved_to_review

    def test_no_tag_skips(self):
        usecase, worktrees, _, _ = make_usecase()
        usecase.execute(FakeCard("タグなし"))
        assert worktrees.created == []

    def test_tag_lookup_ignores_case(self):
        usecase, _, _, _ = make_usecase(projects={"wf": Path("/repo/wf")})
        project = usecase.resolve_project(FakeCard("Wf [dev]ドキュメント作成"))
        assert project.repo_path == Path("/repo/wf")


class TestGcUsecase:
    def test_removes_all_finished(self):
        worktrees = FakeWorktreeManager()
        wt = Worktree(path=Path("/tmp/a"), branch=BranchName(value="chevuoi/x"), repo_path=Path("/r"))
        worktrees.finished = [wt]
        GcUsecase(worktrees).execute(older_than_days=7)
        assert worktrees.removed == [wt]
