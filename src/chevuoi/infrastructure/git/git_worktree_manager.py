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
        if result.stdout.strip() != "":
            return True
        # ワークフローが自分でコミット（さらに push）してしまった場合、作業ツリーは
        # 綺麗でも成果は残っている。ベースブランチとの差分も成果として扱う。
        base = self._base_ref(worktree)
        if base is None:
            # 判定できないことを黙って「変更なし」にすると成果を捨てるため、エラーにする
            raise WorktreeError("ベースブランチを解決できませんでした")
        # 三点表記で分岐点からの差分だけを数える。ベースブランチ側が進んでいても
        # 「変更あり」と誤判定しないため。
        count = self._git(
            worktree.path, "rev-list", "--count", "--right-only", f"{base}...HEAD",
            check=False,
        )
        if count.returncode != 0:
            raise WorktreeError(count.stderr.strip())
        return count.stdout.strip() not in ("", "0")

    def _base_ref(self, worktree: Worktree) -> str | None:
        """差分を測る基準のベースブランチ。解決できた最初の候補を返す。

        upstream（origin/<自ブランチ>）は基準にしない。ワークフローが自分で push
        すると差分ゼロになり、成果を取りこぼすため。
        """
        candidates: list[str] = []
        origin_head = self._git(
            worktree.path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False
        )
        if origin_head.returncode == 0 and origin_head.stdout.strip():
            candidates.append(origin_head.stdout.strip())
        # リモートが無いリポジトリでは、本体側がチェックアウトしているブランチを基準にする
        head = self._git(worktree.repo_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
        if head.returncode == 0 and head.stdout.strip() not in ("", "HEAD"):
            candidates.append(head.stdout.strip())
        for ref in candidates:
            verified = self._git(
                worktree.path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
                check=False,
            )
            if verified.returncode == 0:
                return ref
        return None

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
