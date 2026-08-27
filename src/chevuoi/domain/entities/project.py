from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from chevuoi.domain.value_objects.project_tag import ProjectTag


class Project(BaseModel):
    """タグに紐付くプロジェクトフォルダ。"""

    tag: ProjectTag
    repo_path: Path

    @property
    def is_null(self) -> bool:
        return False


class NullProject(Project):
    """解決できなかったことを表す Null Object。処理側は is_null で判定する。"""

    def __init__(self) -> None:
        super().__init__(tag=ProjectTag(value=""), repo_path=Path(""))

    @property
    def is_null(self) -> bool:
        return True
