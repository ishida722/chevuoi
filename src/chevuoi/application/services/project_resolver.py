from __future__ import annotations

from injector import inject

from chevuoi.domain.entities.project import NullProject, Project
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig, ProjectConfig


class ProjectResolver:
    """タグを設定の対応表で引いてプロジェクトを決める。決定的で、LLM には推測させない。

    カード処理（ProcessCardUsecase）と CLI（vuoi card issue）が共用する。
    引けないときにカレントディレクトリで代用しないのが要点で、代用すると
    worktree や起票の記録が、対象プロジェクトではなく実行場所のリポジトリを指す。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def resolve(self, tag: ProjectTag | None) -> Project:
        """引けなければ NullProject を返す。引けなかったタグ自体は保持する。"""
        if tag is None:
            return NullProject()
        entry = self._lookup(tag.value)
        if entry is None:
            return NullProject(tag=tag)
        return Project(tag=tag, repo_path=entry.path, test_commands=list(entry.test_commands))

    def _lookup(self, value: str) -> ProjectConfig | None:
        entry = self._config.projects.get(value)
        if entry is not None:
            return entry
        # タグの大文字小文字は無視する（例: "Wf" と "wf" を同一視）
        wanted = value.casefold()
        return next(
            (cfg for key, cfg in self._config.projects.items() if key.casefold() == wanted),
            None,
        )
