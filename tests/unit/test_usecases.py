from pathlib import Path

from chevuoi.application.usecases.gc_usecase import GcUsecase
from chevuoi.application.usecases.process_card_usecase import ProcessCardUsecase
from chevuoi.domain.entities.node_result import NodeResult, NodeStatus
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from tests.unit.fakes import FakeCard, FakeNodeRunner, FakeWorktreeManager


def make_config(projects: dict[str, Path] | None = None) -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k", api_token="t",
            ready_list_id="ready", in_progress_list_id="doing", in_review_list_id="review",
        ),
        projects=projects if projects is not None else {"MIRAI": Path("/repo/mirai")},
        worktree_root=Path("/tmp/worktrees"),
    )


def make_usecase(runner_result: NodeResult, projects=None):
    worktrees = FakeWorktreeManager()
    runner = FakeNodeRunner(runner_result)
    usecase = ProcessCardUsecase(worktrees, runner, make_config(projects))
    return usecase, worktrees, runner


class TestProcessCardUsecase:
    def test_done_comments_output_and_moves_to_review(self):
        usecase, worktrees, runner = make_usecase(
            NodeResult(status=NodeStatus.DONE, output="PR: https://x/pr/1")
        )
        card = FakeCard("MIRAI: ログイン修正")
        usecase.execute(card)
        assert card.comments == ["PR: https://x/pr/1"]
        assert card.moved_to_review
        assert len(worktrees.created) == 1
        assert len(runner.calls) == 1

    def test_failed_comments_error_and_stays(self):
        usecase, _, _ = make_usecase(NodeResult(status=NodeStatus.FAILED, output="boom"))
        card = FakeCard("MIRAI: ログイン修正")
        usecase.execute(card)
        assert card.comments == ["エラー: boom"]
        assert not card.moved_to_review

    def test_claim_failure_skips_everything(self):
        usecase, worktrees, runner = make_usecase(
            NodeResult(status=NodeStatus.DONE, output="x")
        )
        card = FakeCard("MIRAI: 修正", claimable=False)
        usecase.execute(card)
        assert not worktrees.created and not runner.calls and not card.comments

    def test_unknown_tag_skips_after_claim(self):
        usecase, worktrees, runner = make_usecase(
            NodeResult(status=NodeStatus.DONE, output="x"), projects={}
        )
        card = FakeCard("MIRAI: 修正")
        usecase.execute(card)
        assert not worktrees.created and not runner.calls
        assert not card.moved_to_review

    def test_no_tag_skips(self):
        usecase, worktrees, _ = make_usecase(NodeResult(status=NodeStatus.DONE, output="x"))
        card = FakeCard("タグなしカード")
        usecase.execute(card)
        assert not worktrees.created


class TestGcUsecase:
    def test_removes_all_finished(self):
        worktrees = FakeWorktreeManager()
        wt = Worktree(
            path=Path("/tmp/wt"),
            branch=BranchName(value="chevuoi/trello-a"),
            repo_path=Path("/repo"),
        )
        worktrees.finished = [wt]
        GcUsecase(worktrees).execute(older_than_days=7)
        assert worktrees.removed == [wt]
