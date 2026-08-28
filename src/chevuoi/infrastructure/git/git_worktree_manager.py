from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from injector import inject

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.exceptions import WorktreeError
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.infrastructure.config.settings import AppConfig


logger = logging.getLogger(__name__)


class GitWorktreeManager(WorktreeManager):
    """git worktree add / list / remove を subprocess で実行する。"""

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._root = config.worktree_root

    def create(self, project: Project, card: Card) -> Worktree:
        branch = BranchName.from_card_id(card.id)
        path = self._root / branch.value.replace("/", "-")
        worktree = Worktree(path=path, branch=branch, repo_path=project.repo_path)

        if path.exists():
            return worktree

        self._root.mkdir(parents=True, exist_ok=True)
        branch_exists = (
            self._git(project.repo_path, "branch", "--list", branch.value, check=False)
            .stdout.strip()
            != ""
        )
        args = ["worktree", "add"]
        if not branch_exists:
            args += ["-b", branch.value, str(path)]
        else:
            args += [str(path), branch.value]
        result = self._git(project.repo_path, *args, check=False)
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip())
        return worktree

    def list_stale(self, older_than_days: int) -> list[Worktree]:
        """作成から指定日数を経過した chevuoi 管理下の worktree を列挙する。

        「終端済み」の状態判定は行わない（MVP は実行状態を永続化しないため、
        経過日数だけを基準にする）。git 管理下と確認できないディレクトリは
        警告ログを残してスキップする。
        """
        if not self._root.exists():
            return []
        threshold = time.time() - older_than_days * 86400
        found: list[Worktree] = []
        for path in sorted(self._root.iterdir()):
            if not path.is_dir() or not path.name.startswith("chevuoi-"):
                continue
            if path.stat().st_mtime > threshold:
                continue
            repo_path = self._resolve_repo(path)
            if repo_path is None:
                logger.warning("not a git worktree, skip: %s", path)
                continue
            branch = BranchName(value=path.name.replace("chevuoi-", "chevuoi/", 1))
            found.append(Worktree(path=path, branch=branch, repo_path=repo_path))
        return found

    def remove(self, worktree: Worktree) -> None:
        result = self._git(
            worktree.repo_path, "worktree", "remove", "--force", str(worktree.path),
            check=False,
        )
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip())

    def has_changes(self, worktree: Worktree) -> bool:
        result = self._git(worktree.path, "status", "--porcelain", check=False)
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip())
        return result.stdout.strip() != ""

    def _resolve_repo(self, worktree_path: Path) -> Path | None:
        result = self._git(worktree_path, "rev-parse", "--path-format=absolute",
                           "--git-common-dir", check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return None
        common_dir = Path(result.stdout.strip())
        return common_dir.parent if common_dir.name == ".git" else common_dir

    @staticmethod
    def _git(cwd: Path, *args: str, check: bool) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, check=check,
        )
