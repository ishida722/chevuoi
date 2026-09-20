"""タグ→プロジェクトの解決（ProjectResolver）の仕様。

対応表を引けないときにカレントディレクトリで代用しないことが要点。
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


class TestSymbolsInTag:
    """タグは人間が手で書くため、"[テレ東] 見積を出す" のように括弧つきになることがある。

    括弧などの記号は無視して対応表を引く。ただし「記号を落とす」だけにすると、記号しか
    無いタグが空文字に潰れて無関係なプロジェクトに一致するため、その境界も固定する。
    """

    def test_bracketed_tag_matches_plain_configured_tag(self):
        """タグが括弧で囲まれているとき、括弧を除いたキーの設定を引けること。"""
        project = resolver({"テレ東": Path("/repo/tvtokyo")}).resolve(ProjectTag(value="[テレ東]"))
        assert project.repo_path == Path("/repo/tvtokyo")

    @pytest.mark.parametrize("tag", ["【テレ東】", "「テレ東」", "（テレ東）", "#テレ東", "テレ・東"])
    def test_symbols_other_than_brackets_are_ignored(self, tag: str):
        """括弧以外の記号（全角括弧・記号・中黒）が混じっていても、同じ設定を引けること。"""
        project = resolver({"テレ東": Path("/repo/tvtokyo")}).resolve(ProjectTag(value=tag))
        assert project.repo_path == Path("/repo/tvtokyo")

    def test_plain_tag_matches_bracketed_configured_key(self):
        """設定のキーが括弧つきで書かれているとき、括弧の無いタグでも引けること
        （記号を無視する向きは対称にする）。"""
        project = resolver({"[テレ東]": Path("/repo/tvtokyo")}).resolve(ProjectTag(value="テレ東"))
        assert project.repo_path == Path("/repo/tvtokyo")

    def test_different_names_do_not_match_even_without_symbols(self):
        """記号を除いても別の名前になるタグのとき、解決できないこと
        （記号を無視することで過剰に一致させない）。"""
        assert resolver({"テレ東": Path("/repo/tvtokyo")}).resolve(ProjectTag(value="[テレ朝]")).is_null

    def test_symbol_only_tag_matches_nothing(self):
        """記号だけのタグのとき、どのプロジェクトにも一致しないこと。

        記号を落として空文字にすると、同じく記号だけの別のキーと一致してしまう。
        """
        projects = {"テレ東": Path("/repo/tvtokyo"), "★": Path("/repo/star")}
        assert resolver(projects).resolve(ProjectTag(value="[]")).is_null

    def test_tag_matching_the_symbols_too_wins(self):
        """記号まで一致する設定と、記号だけが違う設定の両方があるとき、
        記号まで一致する設定を使うこと（別のリポジトリを引き当てない）。"""
        projects = {"テレ東": Path("/repo/plain"), "[テレ東]": Path("/repo/bracket")}
        assert resolver(projects).resolve(ProjectTag(value="[テレ東]")).repo_path == Path(
            "/repo/bracket"
        )
        assert resolver(projects).resolve(ProjectTag(value="テレ東")).repo_path == Path(
            "/repo/plain"
        )
