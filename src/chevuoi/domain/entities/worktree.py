from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from chevuoi.domain.value_objects.branch_name import BranchName


class Worktree(BaseModel):
    """カード処理用に構築された作業環境。"""

    path: Path
    branch: BranchName
    repo_path: Path
