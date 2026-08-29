from __future__ import annotations

import logging

from injector import inject

from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.issue_report import IssueReport
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.task_proposal import TaskProposal
from chevuoi.domain.services.proposal_policy import select_proposals
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

SUMMARY_TITLE = "<summary>"  # 要約カードの冪等キー用。親ごとに固定する
MAX_SUMMARY_ITEMS = 50  # 要約カード本文に列挙する上限（Trello の本文上限に収める）


class IssueProposalsUsecase:
    """ラン終了時の起票。終端状態に関わらず呼ばれる（blocked でも起票する。仕様）。

    歯止め（深度・重複・上限）は select_proposals に、Inbox 起票と冪等性は CardIssuer に
    委ねる。失敗は IssueReport.skipped に落とし、例外を外へ出さない。
    """

    @inject
    def __init__(self, issue_card: IssueCardUsecase, config: AppConfig) -> None:
        self._issue_card = issue_card
        self._config = config

    def execute(
        self, proposals: list[TaskProposal], project: Project, parent: Card
    ) -> IssueReport:
        report = IssueReport()
        if not proposals:
            return report
        policy = select_proposals(
            proposals,
            parent_generation=parent.generation,
            max_per_run=self._config.proposals.max_per_run,
            max_generation=self._config.proposals.max_generation,
        )
        for proposal, reason in policy.rejected:
            report.skipped.append(f"{reason}: {proposal.title}")

        for proposal in policy.accepted:
            try:
                report.issued.append(self._issue_card.execute(proposal, project, parent=parent))
            except Exception as e:  # noqa: BLE001 - 起票の失敗は本流を止めない
                logger.warning("起票に失敗: %s (%s)", proposal.title, e)
                report.skipped.append(f"起票失敗（{e}）: {proposal.title}")

        if policy.overflow > 0:
            overflowed = policy.overflowed
            body = "上限超過のため起票を見送った候補:\n" + "\n".join(
                f"- [{p.kind}] {p.title}" for p in overflowed[:MAX_SUMMARY_ITEMS]
            )
            if len(overflowed) > MAX_SUMMARY_ITEMS:
                body += f"\n- 他 {len(overflowed) - MAX_SUMMARY_ITEMS} 件"
            summary = TaskProposal(title=f"{policy.overflow} 件の問題を検出", body=body)
            try:
                # 要約カードも冪等にする。件数でタイトルが変わるのでキーは親ごとに固定する
                key = TaskProposal(title=SUMMARY_TITLE).key(parent.id)
                report.summary_card = self._issue_card.execute(
                    summary, project, parent=parent, key=key
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("要約カードの起票に失敗: %s", e)
                report.skipped.append(f"起票失敗（{e}）: {summary.title}")
        return report
