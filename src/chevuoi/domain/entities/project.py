from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from chevuoi.domain.value_objects.project_tag import ProjectTag


class Project(BaseModel):
    """タグに紐付くプロジェクトフォルダ。"""

    tag: ProjectTag
    repo_path: Path
