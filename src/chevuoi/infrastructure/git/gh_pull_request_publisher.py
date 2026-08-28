from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.exceptions import WorktreeError
from chevuoi.domain.ports.pull_request_publisher import PullRequestPublisher

logger = logging.getLogger(__name__)


class GhPullRequestPublisher(PullRequestPublisher):
    """git add / commit / push と gh pr create を subprocess で実行する。"""

    def publish(self, worktree: Worktree, *, title: str, body: str) -> str:
        cwd = worktree.path
        self._run(cwd, "git", "add", "-A")
        if self._run(cwd, "git", "diff", "--cached", "--quiet", check=False).returncode != 0:
            self._run(cwd, "git", "commit", "-m", title, "-m", body)
        self._run(cwd, "git", "push", "-u", "origin", worktree.branch.value)

        existing = self._run(
            cwd, "gh", "pr", "view", worktree.branch.value, "--json", "url", "-q", ".url",
            check=False,
        )
        if existing.returncode == 0 and existing.stdout.strip():
            url = existing.stdout.strip()
            logger.info("既存 PR を再利用: %s", url)
            return url
        created = self._run(
            cwd, "gh", "pr", "create", "--head", worktree.branch.value,
            "--title", title, "--body", body,
        )
        return created.stdout.strip().splitlines()[-1]

    @staticmethod
    def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise WorktreeError(f"{' '.join(args[:3])} 失敗: {result.stderr.strip()}")
        return result
