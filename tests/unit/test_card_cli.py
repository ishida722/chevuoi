"""vuoi card issue が「どのプロジェクトを対象に起票するか」の仕様。

対象プロジェクトはタグを設定の対応表で引いて決める。実行場所（カレント
ディレクトリ）のリポジトリを対象にしてはならない。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from injector import Injector

from chevuoi.application.services.project_resolver import ProjectResolver
from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.domain.entities.project import Project
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig, ProjectConfig
from chevuoi.interface.di_modules import AppModule
from chevuoi.interfaces.cli import main as cli_main
from tests.unit.fakes import FakeCardIssuer, FakeInspector, make_config


class RecordingIssueCardUsecase(IssueCardUsecase):
    """CLI から渡された Project を記録する。発行そのものは本物の実装が行う。"""

    def __init__(self, issuer: FakeCardIssuer) -> None:
        super().__init__(issuer, FakeInspector())
        self.projects: list[Project] = []

    def execute(self, proposal, project, **kwargs):
        self.projects.append(project)
        return super().execute(proposal, project, **kwargs)


class StubInjector:
    def __init__(self, config: AppConfig, usecase: RecordingIssueCardUsecase) -> None:
        self._by_type = {
            AppConfig: config,
            ProjectResolver: ProjectResolver(config),
            IssueCardUsecase: usecase,
        }

    def get(self, cls):
        return self._by_type[cls]


def run_cli(
    argv: list[str], projects, *, issue_error: str | None = None
) -> tuple[int, RecordingIssueCardUsecase, FakeCardIssuer]:
    issuer = FakeCardIssuer(error=issue_error)
    usecase = RecordingIssueCardUsecase(issuer)
    config = make_config(projects=projects)
    with patch.object(cli_main, "build_injector", return_value=StubInjector(config, usecase)):
        code = cli_main.main(argv)
    return code, usecase, issuer


class TestCardIssueCommand:
    def test_tag_with_space_is_rejected_before_issuing(self):
        """タグが 1 語でないとき、起票せずに終了コード 1 で終わること。"""
        code, _, issuer = run_cli(["card", "issue", "MIRAI X", "ログイン修正"], {})
        assert code == 1
        assert issuer.requests == []

    def test_tag_with_ideographic_space_is_rejected_before_issuing(self):
        """区切りが全角スペースでもタグが 1 語でないと判定すること。

        ProjectTag.from_title は Unicode の空白全般で区切るため、ここで通すと
        設定にあるタグ（MIRAI）なのに引けないタグとして扱われてしまう。
        """
        code, _, issuer = run_cli(
            ["card", "issue", "MIRAI　X", "ログイン修正"], {"MIRAI": Path("/repo/mirai")}
        )
        assert code == 1
        assert issuer.requests == []

    def test_blank_tag_is_rejected_before_issuing(self):
        """タグが空白のみのとき、起票せずに終了コード 1 で終わること。"""
        code, _, issuer = run_cli(["card", "issue", "   ", "ログイン修正"], {})
        assert code == 1
        assert issuer.requests == []

    def test_issue_failure_exits_with_error(self, capsys):
        """発行に失敗したとき、理由を標準エラーに出して終了コード 1 で終わること。"""
        code, _, _ = run_cli(
            ["card", "issue", "MIRAI", "ログイン修正"],
            {"MIRAI": Path("/repo/mirai")},
            issue_error="Inbox 未設定",
        )
        assert code == 1
        assert "Inbox 未設定" in capsys.readouterr().err

    def test_unmapped_tag_issues_without_a_repository(self, capsys):
        """設定に無いタグのとき、実行場所のリポジトリを対象にせず
        （対象未特定のまま）起票し、警告を出すこと。"""
        code, usecase, issuer = run_cli(
            ["card", "issue", "OTHER", "ログイン修正"], {"MIRAI": Path("/repo/mirai")}
        )
        assert code == 0
        assert usecase.projects[0].is_null
        # 対象は未特定でもタグはカードに残る（タイトル前置に使われる）
        assert issuer.requests[0].project_tag.value == "OTHER"
        assert "OTHER" in capsys.readouterr().err

    def test_mapped_tag_targets_the_configured_repository(self):
        """設定にあるタグのとき、その設定のリポジトリを対象として起票すること
        （カレントディレクトリではないこと）。

        暫定的に「CLI がユースケースへ渡した Project」を観測点にしている。
        本ブランチでは対象リポジトリが発行要求に載らないため、これ以外に
        観測できる場所が無い。親ブランチのトリアージ実装が入って
        CardIssueRequest.base_commit（フッターの base=）が載ったら、
        issuer.requests[0].base_commit を観測点にして置き換える。
        """
        entry = ProjectConfig(path=Path("/repo/mirai"), test_commands=["uv run pytest -q"])
        code, usecase, issuer = run_cli(
            ["card", "issue", "MIRAI", "ログイン修正"], {"MIRAI": entry}
        )
        assert code == 0
        project = usecase.projects[0]
        assert project.repo_path == Path("/repo/mirai")
        assert not project.is_null
        assert issuer.requests[0].title == "ログイン修正"


class TestDiWiring:
    def test_project_resolver_is_resolvable_from_app_module(self):
        """CLI が使う ProjectResolver が、実際の DI 構成から取り出せること。

        card.py は injector.get(ProjectResolver) で解決するため、
        AppModule 側の結線が壊れるとコマンドが実行時に落ちる。
        """
        config = make_config(projects={"MIRAI": Path("/repo/mirai")})
        resolver = Injector([AppModule(config)]).get(ProjectResolver)
        assert resolver.resolve(ProjectTag(value="MIRAI")).repo_path == Path("/repo/mirai")
