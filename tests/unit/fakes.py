from __future__ import annotations

from pathlib import Path

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.issue_report import IssuedCard
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.exceptions import CardIssueError
from chevuoi.domain.ports.card_issuer import CardIssueRequest, CardIssuer
from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor
from chevuoi.domain.ports.pull_request_publisher import PullRequestPublisher
from chevuoi.domain.ports.workflow_loader import LoadedWorkflow
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.domain.value_objects.card_id import CardId


class FakeCard(Card):
    """操作を記録するだけのインメモリ Card 実装。"""

    def __init__(
        self,
        name: str,
        *,
        claimable: bool = True,
        external_id: str = "x1",
        generation: int = 0,
    ) -> None:
        self._name = name
        self._claimable = claimable
        self._external_id = external_id
        self._generation = generation
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

    @property
    def generation(self) -> int:
        return self._generation


class FakeCardIssuer(CardIssuer):
    """発行要求を記録し、冪等キーで既存を返すインメモリ CardIssuer。"""

    def __init__(self, *, error: str | None = None) -> None:
        self.error = error
        self.requests: list[CardIssueRequest] = []
        self.by_key: dict[str, IssuedCard] = {}

    def find_by_key(self, key: str) -> IssuedCard | None:
        return self.by_key.get(key)

    def issue(self, request: CardIssueRequest) -> IssuedCard:
        if self.error:
            raise CardIssueError(self.error)
        existing = self.by_key.get(request.idempotency_key)
        if existing is not None:
            return existing
        self.requests.append(request)
        n = len(self.requests)
        issued = IssuedCard(
            id=CardId(source="fake", external_id=f"new{n}"),
            url=f"https://example.com/new{n}",
            created=True,
        )
        self.by_key[request.idempotency_key] = issued.model_copy(update={"created": False})
        return issued


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
