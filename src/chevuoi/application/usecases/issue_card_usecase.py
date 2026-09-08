from __future__ import annotations

from injector import inject

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.issue_report import IssuedCard
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.task_proposal import TaskProposal
from chevuoi.domain.ports.card_issuer import CardIssueRequest, CardIssuer, SearchScope


class IssueCardUsecase:
    """カード 1 枚の発行。CLI（vuoi card issue）と IssueProposalsUsecase が共用する。

    冪等キーは親カード ID + タイトルから決定的に作り、CardIssuer が本文に埋め込む。
    """

    @inject
    def __init__(self, issuer: CardIssuer) -> None:
        self._issuer = issuer

    def execute(
        self,
        proposal: TaskProposal,
        project: Project,
        *,
        parent: Card | None = None,
        key: str | None = None,
        search_scope: SearchScope = "inbox",
    ) -> IssuedCard:
        """key を渡すと冪等キーを固定できる（要約カードのように件数でタイトルが変わる場合）。

        search_scope="board" は、発行済みカードが Inbox から動かされたあとも
        既存として拾いたい場合（定期的に同じ対象を起票する場合）に使う。
        """
        body = proposal.body
        if proposal.evidence:
            body = (body + "\n\n" if body else "") + "根拠:\n" + "\n".join(
                f"- {e}" for e in proposal.evidence
            )
        request = CardIssueRequest(
            title=proposal.title,
            body=body,
            project_tag=project.tag,
            idempotency_key=key or proposal.key(parent.id if parent is not None else None),
            kind=proposal.kind,
            generation=parent.generation + 1 if parent is not None else 0,
            parent=parent.id if parent is not None else None,
            parent_url=parent.url if parent is not None else "",
            search_scope=search_scope,
        )
        return self._issuer.issue(request)
