import json
import logging
import subprocess
from pathlib import Path

import pytest

from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.application.usecases.issue_review_requests_usecase import IssueReviewRequestsUsecase
from chevuoi.domain.entities.review_request import ReviewRequest
from chevuoi.domain.exceptions import ReviewRequestError
from chevuoi.domain.ports.repository_locator import RepositoryLocator
from chevuoi.domain.ports.review_request_provider import ReviewRequestProvider
from chevuoi.infrastructure.config.settings import AppConfig, ProjectConfig, TrelloConfig
from chevuoi.domain.services.repository_name import normalize_repo
from chevuoi.infrastructure.git.gh_review_request_provider import GhReviewRequestProvider
from chevuoi.infrastructure.git.git_repository_locator import parse_repo_slug
from tests.unit.fakes import FakeCardIssuer


def make_config(projects: dict[str, ProjectConfig] | None = None) -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k", api_token="t",
            ready_list_id="ready", in_progress_list_id="doing", in_review_list_id="review",
            inbox_list_id="inbox",
        ),
        projects=projects or {},
        worktree_root=Path("/tmp/worktrees"),
    )


def request(repository: str = "ishida722/vuoi", number: int = 12, **kw) -> ReviewRequest:
    base = dict(
        title="レビュー対象の PR",
        url=f"https://github.com/{repository}/pull/{number}",
        author="someone",
        updated_at="2026-09-08T00:00:00Z",
    )
    return ReviewRequest(repository=repository, number=number, **{**base, **kw})


class FakeProvider(ReviewRequestProvider):
    def __init__(self, requests: list[ReviewRequest], *, exc: Exception | None = None) -> None:
        self.requests = requests
        self.exc = exc
        self.limits: list[int] = []

    def fetch(self, *, limit: int) -> list[ReviewRequest]:
        if self.exc is not None:
            raise self.exc
        self.limits.append(limit)
        return self.requests[:limit]


class FakeLocator(RepositoryLocator):
    def __init__(self, by_path: dict[str, str] | None = None) -> None:
        self.by_path = by_path or {}
        self.calls: list[Path] = []

    def locate(self, path: Path) -> str | None:
        self.calls.append(path)
        return self.by_path.get(str(path))


def make_usecase(requests, projects=None, locator=None, issuer=None):
    issuer = issuer or FakeCardIssuer()
    usecase = IssueReviewRequestsUsecase(
        FakeProvider(requests),
        locator or FakeLocator(),
        IssueCardUsecase(issuer),
        make_config(projects),
    )
    return usecase, issuer


class TestReviewRequest:
    def test_card_key_depends_only_on_repository_and_number(self):
        assert request(title="A").card_key == request(title="B").card_key
        assert request(number=12).card_key != request(number=13).card_key
        assert request(repository="ISHIDA722/VUOI").card_key == request().card_key

    def test_proposal_body_starts_with_pr_url(self):
        proposal = request().to_proposal()
        assert proposal.body.startswith("https://github.com/ishida722/vuoi/pull/12")
        assert "リポジトリ: ishida722/vuoi" in proposal.body
        assert proposal.kind == "chore"

    def test_proposal_title_carries_the_slug(self):
        assert request().to_proposal().title == "PR レビュー: レビュー対象の PR（ishida722/vuoi#12）"

    def test_long_pr_title_is_truncated_keeping_the_slug(self):
        proposal = request(title="あ" * 300).to_proposal()
        assert len(proposal.title) <= 200
        assert proposal.title.endswith("（ishida722/vuoi#12）")

    def test_empty_pr_title_still_makes_a_valid_card_title(self):
        assert request(title="").to_proposal().title == "PR レビュー: (タイトルなし)（ishida722/vuoi#12）"


class TestIssueReviewRequestsUsecase:
    def test_matched_project_tags_the_card(self):
        projects = {"VUOI": ProjectConfig(path=Path("/repo/vuoi"), repo="ishida722/vuoi")}
        usecase, issuer = make_usecase([request()], projects)
        report = usecase.execute()
        assert len(report.issued) == 1 and not report.skipped
        assert issuer.requests[0].project_tag.value == "VUOI"

    def test_repository_matching_ignores_case(self):
        projects = {"VUOI": ProjectConfig(path=Path("/repo/vuoi"), repo="Ishida722/Vuoi")}
        usecase, issuer = make_usecase([request(repository="ishida722/VUOI")], projects)
        usecase.execute()
        assert issuer.requests[0].project_tag.value == "VUOI"

    def test_unmatched_project_issues_without_tag(self):
        projects = {"VUOI": ProjectConfig(path=Path("/repo/vuoi"), repo="ishida722/vuoi")}
        usecase, issuer = make_usecase([request(repository="other/repo")], projects)
        usecase.execute()
        assert issuer.requests[0].project_tag.value == ""

    def test_repository_falls_back_to_the_local_remote(self):
        projects = {"VUOI": ProjectConfig(path=Path("/repo/vuoi"))}
        locator = FakeLocator({"/repo/vuoi": "ishida722/vuoi"})
        usecase, issuer = make_usecase([request()], projects, locator)
        usecase.execute()
        assert issuer.requests[0].project_tag.value == "VUOI"
        assert locator.calls == [Path("/repo/vuoi")]

    def test_config_repo_accepts_a_url_form(self):
        projects = {
            "VUOI": ProjectConfig(path=Path("/repo/vuoi"), repo="https://github.com/ishida722/vuoi")
        }
        usecase, issuer = make_usecase([request()], projects)
        usecase.execute()
        assert issuer.requests[0].project_tag.value == "VUOI"

    def test_unparsable_config_repo_is_warned_and_not_matched(self, caplog):
        projects = {"VUOI": ProjectConfig(path=Path("/repo/vuoi"), repo="vuoi")}
        usecase, issuer = make_usecase([request()], projects)
        with caplog.at_level(logging.WARNING):
            usecase.execute()
        assert issuer.requests[0].project_tag.value == ""
        assert "projects.VUOI" in caplog.text

    def test_hitting_the_limit_is_warned(self, caplog):
        provider = FakeProvider([request(number=n) for n in range(3)])
        usecase = IssueReviewRequestsUsecase(
            provider, FakeLocator(), IssueCardUsecase(FakeCardIssuer()), make_config()
        )
        with caplog.at_level(logging.WARNING):
            usecase.execute(limit=2)
        assert "上限 2 件" in caplog.text

    def test_below_the_limit_is_not_warned(self, caplog):
        usecase, _ = make_usecase([request()])
        with caplog.at_level(logging.WARNING):
            usecase.execute(limit=20)
        assert not caplog.text

    def test_config_repo_wins_over_the_local_remote(self):
        projects = {"VUOI": ProjectConfig(path=Path("/repo/vuoi"), repo="ishida722/vuoi")}
        locator = FakeLocator({"/repo/vuoi": "someone/else"})
        usecase, issuer = make_usecase([request()], projects, locator)
        usecase.execute()
        assert issuer.requests[0].project_tag.value == "VUOI"
        assert locator.calls == []

    def test_existing_card_is_reused_on_the_next_run(self):
        issuer = FakeCardIssuer()
        usecase, _ = make_usecase([request()], issuer=issuer)
        first = usecase.execute()
        second = usecase.execute()
        assert first.issued[0].created and not second.issued[0].created
        assert len(issuer.requests) == 1

    def test_existing_card_is_searched_across_the_board(self):
        # Inbox から動かされたカードも既存として拾う必要がある
        usecase, issuer = make_usecase([request()])
        usecase.execute()
        assert issuer.requests[0].search_scope == "board"

    def test_issue_failure_is_reported_and_does_not_stop_the_rest(self):
        usecase, _ = make_usecase(
            [request(number=1), request(number=2)], issuer=FakeCardIssuer(error="Inbox 未設定")
        )
        report = usecase.execute()
        assert not report.issued
        assert report.skipped == [
            "起票失敗（Inbox 未設定）: ishida722/vuoi#1",
            "起票失敗（Inbox 未設定）: ishida722/vuoi#2",
        ]

    def test_fetch_failure_propagates(self):
        usecase = IssueReviewRequestsUsecase(
            FakeProvider([], exc=ReviewRequestError("gh 失敗")),
            FakeLocator(),
            IssueCardUsecase(FakeCardIssuer()),
            make_config(),
        )
        with pytest.raises(ReviewRequestError):
            usecase.execute()

    def test_limit_is_passed_to_the_provider(self):
        provider = FakeProvider([request(number=n) for n in range(5)])
        usecase = IssueReviewRequestsUsecase(
            provider, FakeLocator(), IssueCardUsecase(FakeCardIssuer()), make_config()
        )
        assert len(usecase.execute(limit=2).issued) == 2
        assert provider.limits == [2]


class TestNormalizeRepo:
    @pytest.mark.parametrize(
        "value",
        [
            "ishida722/vuoi",
            "ishida722/vuoi.git",
            "  ishida722/vuoi/  ",
            "https://github.com/ishida722/vuoi",
            "git@github.com:ishida722/vuoi.git",
        ],
    )
    def test_accepted_forms(self, value):
        assert normalize_repo(value) == "ishida722/vuoi"

    @pytest.mark.parametrize("value", ["", "vuoi", "https://github.com/ishida722"])
    def test_rejected_forms(self, value):
        assert normalize_repo(value) is None


class TestParseRepoSlug:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:ishida722/vuoi.git",
            "https://github.com/ishida722/vuoi.git",
            "https://github.com/ishida722/vuoi",
            "ssh://git@github.com/ishida722/vuoi.git",
            "  https://github.com/ishida722/vuoi/  ",
        ],
    )
    def test_github_remotes(self, url):
        assert parse_repo_slug(url) == "ishida722/vuoi"

    @pytest.mark.parametrize("url", ["", "git@gitlab.com:ishida722/vuoi.git", "/repo/vuoi"])
    def test_non_github_remotes(self, url):
        assert parse_repo_slug(url) is None


class TestGhReviewRequestProvider:
    def _run_with(self, monkeypatch, *, stdout: str = "[]", returncode: int = 0):
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="失敗理由")

        monkeypatch.setattr(subprocess, "run", fake_run)
        return calls

    def test_search_is_limited_to_open_prs_requested_from_me(self, monkeypatch):
        calls = self._run_with(monkeypatch)
        GhReviewRequestProvider().fetch(limit=5)
        assert calls[0][:3] == ["gh", "search", "prs"]
        joined = " ".join(calls[0])
        assert "--review-requested @me" in joined
        assert "--state open" in joined
        assert "--limit 5" in joined

    def test_parses_search_results(self, monkeypatch):
        self._run_with(
            monkeypatch,
            stdout=json.dumps(
                [
                    {
                        "number": 12,
                        "title": "直す",
                        "url": "https://github.com/ishida722/vuoi/pull/12",
                        "repository": {"name": "vuoi", "nameWithOwner": "ishida722/vuoi"},
                        "author": {"login": "someone"},
                        "updatedAt": "2026-09-08T00:00:00Z",
                    }
                ]
            ),
        )
        [result] = GhReviewRequestProvider().fetch(limit=5)
        assert result == ReviewRequest(
            repository="ishida722/vuoi", number=12, title="直す",
            url="https://github.com/ishida722/vuoi/pull/12",
            author="someone", updated_at="2026-09-08T00:00:00Z",
        )

    def test_skips_entries_without_repository_or_number(self, monkeypatch):
        self._run_with(
            monkeypatch,
            stdout=json.dumps([{"number": 1}, {"repository": {"nameWithOwner": "a/b"}}, "壊れた"]),
        )
        assert GhReviewRequestProvider().fetch(limit=5) == []

    def test_gh_failure_raises(self, monkeypatch):
        self._run_with(monkeypatch, returncode=1)
        with pytest.raises(ReviewRequestError, match="失敗理由"):
            GhReviewRequestProvider().fetch(limit=5)

    def test_broken_json_raises(self, monkeypatch):
        self._run_with(monkeypatch, stdout="{")
        with pytest.raises(ReviewRequestError):
            GhReviewRequestProvider().fetch(limit=5)
