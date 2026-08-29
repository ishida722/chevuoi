from __future__ import annotations

from pathlib import Path

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor
from chevuoi.domain.ports.pull_request_publisher import PullRequestPublisher
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.domain.value_objects.card_id import CardId


class FakeCard(Card):
    """操作を記録するだけのインメモリ Card 実装。"""

    def __init__(self, name: str, *, claimable: bool = True, external_id: str = "x1") -> None:
        self._name = name
        self._claimable = claimable
        self._external_id = external_id
        self.comments: list[str] = []
        self.moved_to_review = False

    @property
    def id(self) -> CardId:
        return CardId(source="fake", external_id=self._external_id)

    @property
    def name(self) -> str:
        return self._name

    @property
    def desc(self) -> str:
        return "desc"

    @property
    def url(self) -> str:
        return "https://example.com/card"

    def claim(self) -> bool:
        return self._claimable

    def add_comment(self, text: str) -> None:
        self.comments.append(text)

    def move_to_review(self) -> None:
        self.moved_to_review = True


class FakeWorktreeManager(WorktreeManager):
    def __init__(self) -> None:
        self.created: list[tuple[Project, Card]] = []
        self.removed: list[Worktree] = []
        self.finished: list[Worktree] = []
        self.changes = True

    def create(self, project: Project, card: Card) -> Worktree:
        self.created.append((project, card))
        return Worktree(
            path=Path("/tmp/wt"),
            branch=BranchName.from_card_id(card.id),
            repo_path=project.repo_path,
        )

    def list_stale(self, older_than_days: int) -> list[Worktree]:
        return self.finished

    def remove(self, worktree: Worktree) -> None:
        self.removed.append(worktree)

    def has_changes(self, worktree: Worktree) -> bool:
        return self.changes


class FakeExecutor(GraphExecutor):
    def __init__(self, result: ExecutionResult, *, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[dict] = []

    def execute(
        self, workflow: LoadedWorkflow, message: str, *, workdir=None, project=None
    ) -> ExecutionResult:
        if self.exc is not None:
            raise self.exc
        self.calls.append(
            {"workflow": workflow, "message": message, "workdir": workdir, "project": project}
        )
        return self.result


class FakePublisher(PullRequestPublisher):
    def __init__(self, url: str = "https://x/pr/1") -> None:
        self.url = url
        self.calls: list[dict] = []

    def publish(self, worktree: Worktree, *, title: str, body: str) -> str:
        self.calls.append({"worktree": worktree, "title": title, "body": body})
        return self.url
