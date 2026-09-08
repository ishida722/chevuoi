"""タグ→プロジェクトの解決（ProjectResolver）の仕様。

対応表を引けないときにカレントディレクトリで代用しないことが要点。
"""

from __future__ import annotations

from pathlib import Path

from chevuoi.application.services.project_resolver import ProjectResolver
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import ProjectConfig
from tests.unit.fakes import make_config


def resolver(projects) -> ProjectResolver:
    return ProjectResolver(make_config(projects=projects))


class TestProjectResolver:
    def test_unmapped_tag_is_unresolved(self):
        """設定に無いタグのとき、解決できなかったこと（is_null）を返すこと。"""
        project = resolver({"MIRAI": Path("/repo/mirai")}).resolve(ProjectTag(value="OTHER"))
        assert project.is_null

    def test_unmapped_tag_carries_no_project_settings(self):
        """設定が空のとき、解決できなかったことを is_null で示し、
        設定由来の値（テストコマンド）を持たせないこと。

        repo_path については、pathlib が Path("") を Path(".") に正規化するため
        「カレントディレクトリでないこと」は値として表現できない。未解決の
        プロジェクトを触ってよいかの判定は is_null が唯一の手段になる。
        """
        project = resolver({}).resolve(ProjectTag(value="MIRAI"))
        assert project.is_null
        assert project.test_commands == []

    def test_unmapped_tag_keeps_the_tag(self):
        """解決できなかった場合でも、引けなかったタグを保持すること
        （起票はタグをタイトルに前置するため）。"""
        project = resolver({}).resolve(ProjectTag(value="OTHER"))
        assert project.tag.value == "OTHER"

    def test_no_tag_is_unresolved(self):
        """タグが無い（None）とき、解決できなかったことを返すこと。"""
        assert resolver({"MIRAI": Path("/repo/mirai")}).resolve(None).is_null

    def test_mapped_tag_uses_configured_repository_and_commands(self):
        """設定にあるタグのとき、その設定のパスとテストコマンドを持つプロジェクトを返すこと。"""
        entry = ProjectConfig(path=Path("/repo/mirai"), test_commands=["uv run pytest -q"])
        project = resolver({"MIRAI": entry}).resolve(ProjectTag(value="MIRAI"))
        assert not project.is_null
        assert project.repo_path == Path("/repo/mirai")
        assert project.test_commands == ["uv run pytest -q"]
        assert project.tag.value == "MIRAI"

    def test_tag_lookup_ignores_case(self):
        """タグの大文字小文字が設定と違っても、同じプロジェクトとして引けること。"""
        project = resolver({"wf": Path("/repo/wf")}).resolve(ProjectTag(value="Wf"))
        assert project.repo_path == Path("/repo/wf")
