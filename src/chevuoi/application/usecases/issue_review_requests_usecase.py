from __future__ import annotations

import logging

from injector import inject

from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.domain.entities.issue_report import IssueReport
from chevuoi.domain.entities.project import NullProject, Project
from chevuoi.domain.ports.repository_locator import RepositoryLocator
from chevuoi.domain.ports.review_request_provider import ReviewRequestProvider
from chevuoi.domain.services.repository_name import normalize_repo
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig, ProjectConfig

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 20  # 1 回の取得で見る PR の上限


class IssueReviewRequestsUsecase:
    """自分にレビュー依頼が来ている PR を Inbox カードとして起票する（vuoi review-requests）。

    プロジェクトの特定は「PR のリポジトリ == 設定のプロジェクトのリポジトリ」の照合で
    決定的に行い、LLM には推測させない。マッチしなければタグ無しで起票する
    （人間がレビューするか、レビューのワークフローに流すかを判断する）。

    起票先は Inbox なので、自動で実行対象になることはない（仕様 proposals の歯止め 1）。
    冪等キーはリポジトリと PR 番号だけから決まり、既存カードはボード全体から探すため、
    定期実行しても同じ PR のカードは増えない。
    """

    @inject
    def __init__(
        self,
        provider: ReviewRequestProvider,
        locator: RepositoryLocator,
        issue_card: IssueCardUsecase,
        config: AppConfig,
    ) -> None:
        self._provider = provider
        self._locator = locator
        self._issue_card = issue_card
        self._config = config

    def execute(self, *, limit: int = DEFAULT_LIMIT) -> IssueReport:
        """取得の失敗は ReviewRequestError として外へ出す（1 件も処理できないため）。
        個々の起票の失敗は IssueReport.skipped に落とし、残りの PR の処理は続ける。
        """
        requests = self._provider.fetch(limit=limit)
        logger.info("レビュー依頼中の PR: %d 件", len(requests))
        if len(requests) >= limit:
            # 取得は更新の新しい順なので、上限に達していると古い依頼が落ちている。
            # 次回以降も同じ位置で切れて永久にカード化されないため、黙って捨てない
            logger.warning(
                "取得件数が上限 %d 件に達しました。これより古いレビュー依頼はカード化されません"
                "（--limit を上げてください）",
                limit,
            )
        projects = self._projects_by_repo()
        report = IssueReport()
        for request in requests:
            project = projects.get(request.repository.casefold())
            if project is None:
                logger.info("プロジェクト未特定のためタグ無しで起票: %s", request.slug)
                project = NullProject()
            try:
                report.issued.append(
                    self._issue_card.execute(
                        request.to_proposal(),
                        project,
                        key=request.card_key,
                        search_scope="board",
                    )
                )
            except Exception as e:  # noqa: BLE001 - 1 件の失敗で残りを止めない
                logger.warning("起票に失敗: %s (%s)", request.slug, e)
                report.skipped.append(f"起票失敗（{e}）: {request.slug}")
        return report

    def _projects_by_repo(self) -> dict[str, Project]:
        """設定のプロジェクトを "owner/name"（小文字化）で引ける形にする。

        リポジトリは設定の repo を優先し、無ければ origin のリモート URL から引く。
        """
        projects: dict[str, Project] = {}
        for tag, entry in self._config.projects.items():
            repo = self._repo_of(tag, entry)
            if not repo:
                logger.debug("リポジトリを特定できないプロジェクト: %s (%s)", tag, entry.path)
                continue
            key = repo.casefold()
            if key in projects:
                logger.warning("同じリポジトリのプロジェクトが複数あります: %s（%s を使う）",
                               repo, projects[key].tag)
                continue
            projects[key] = Project(
                tag=ProjectTag(value=tag),
                repo_path=entry.path,
                test_commands=list(entry.test_commands),
            )
        return projects

    def _repo_of(self, tag: str, entry: ProjectConfig) -> str | None:
        """プロジェクトのリポジトリ。設定の repo を優先し、無ければ origin から引く。

        設定は URL でも "owner/name" でも書けるよう正規化する。正規化できない値は
        黙って不一致にすると全 PR がタグ無しになるため、警告して気付けるようにする。
        """
        if not entry.repo:
            return self._locator.locate(entry.path)
        repo = normalize_repo(entry.repo)
        if repo is None:
            logger.warning(
                'projects.%s の repo "%s" を "owner/name" として解釈できません', tag, entry.repo
            )
        return repo
