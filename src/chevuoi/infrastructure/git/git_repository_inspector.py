from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from injector import inject

from chevuoi.domain.entities.project import Project
from chevuoi.domain.exceptions import WorktreeError
from chevuoi.domain.ports.repository_inspector import RepositoryInspector
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

# ベースチェックアウトの置き場所。GitWorktreeManager.list_stale は "chevuoi-" で始まる
# ディレクトリだけを掃除するため、この名前なら vuoi gc がカード用 worktree と
# 取り違えて消すことはない（裏を返すと gc されないので、毎回作り直さず使い回す）
BASE_CHECKOUT_DIR = "triage-base"


class GitRepositoryInspector(RepositoryInspector):
    """git をリポジトリの読み取りにだけ使う。作業ツリー（カード用 worktree）は触らない。

    ベース参照の解決順は GitWorktreeManager._base_ref と同じ:
    設定の base_ref → refs/remotes/origin/HEAD → 本体側の現在ブランチ。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._root = config.worktree_root
        self._base_refs: dict[Path, str | None] = {}

    def refresh(self, project: Project) -> bool:
        remotes = self._git(project.repo_path, "remote")
        if remotes.returncode != 0:
            logger.warning("リモートの一覧取得に失敗: %s", project.repo_path)
            return False
        names = remotes.stdout.split()
        if not names:
            return True  # リモートが無いリポジトリでは取りに行くものがない
        remote = "origin" if "origin" in names else names[0]
        result = self._git(project.repo_path, "fetch", "--quiet", remote)
        if result.returncode != 0:
            logger.warning("git fetch に失敗（%s）: %s", project.tag.value, result.stderr.strip())
            return False
        self._base_refs.pop(project.repo_path, None)  # 参照の解決結果を作り直す
        return True

    def base_commit(self, project: Project) -> str:
        ref = self._base_ref(project)
        if ref is None:
            return ""
        result = self._git(project.repo_path, "rev-parse", f"{ref}^{{commit}}")
        return result.stdout.strip() if result.returncode == 0 else ""

    def path_exists(self, project: Project, path: str) -> bool:
        ref = self._base_ref(project)
        if ref is None:
            return False
        result = self._git(project.repo_path, "cat-file", "-e", f"{ref}:{path}")
        return result.returncode == 0

    def changed_since(self, project: Project, path: str, since: str) -> bool:
        """since..base にそのパスの変更があるか。判定できないときは True（安全側）。

        「変更が無い」ことだけが LLM 照会を省く根拠なので、分からない場合は
        変更ありとして扱い、判定を先へ進める。
        """
        ref = self._base_ref(project)
        if ref is None or not since:
            return True
        verified = self._git(project.repo_path, "rev-parse", "--verify", "--quiet", f"{since}^{{commit}}")
        if verified.returncode != 0:
            return True
        result = self._git(
            project.repo_path, "log", "--oneline", "-1", f"{since}..{ref}", "--", path
        )
        if result.returncode != 0:
            return True
        return result.stdout.strip() != ""

    def base_checkout(self, project: Project) -> Path:
        ref = self._base_ref(project)
        if ref is None:
            raise WorktreeError(f"ベース参照を解決できません: {project.repo_path}")
        path = self._root / BASE_CHECKOUT_DIR / project.tag.value
        if path.exists():
            # 使い回す。ブランチは作らないので detached のまま基準を進める
            result = self._git(path, "checkout", "--detach", ref)
            if result.returncode != 0:
                raise WorktreeError(result.stderr.strip())
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        result = self._git(project.repo_path, "worktree", "add", "--detach", str(path), ref)
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip())
        return path

    def _base_ref(self, project: Project) -> str | None:
        if project.repo_path in self._base_refs:
            return self._base_refs[project.repo_path]
        resolved = self._resolve_base_ref(project)
        self._base_refs[project.repo_path] = resolved
        return resolved

    def _resolve_base_ref(self, project: Project) -> str | None:
        candidates: list[str] = []
        if project.base_ref:
            candidates.append(project.base_ref)
        origin_head = self._git(
            project.repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
        )
        if origin_head.returncode == 0 and origin_head.stdout.strip():
            candidates.append(origin_head.stdout.strip())
        # リモートが無いリポジトリでは、本体側がチェックアウトしているブランチを基準にする
        head = self._git(project.repo_path, "rev-parse", "--abbrev-ref", "HEAD")
        if head.returncode == 0 and head.stdout.strip() not in ("", "HEAD"):
            candidates.append(head.stdout.strip())
        for ref in candidates:
            verified = self._git(
                project.repo_path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
            )
            if verified.returncode == 0:
                return ref
        return None

    @staticmethod
    def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
            )
        except OSError as e:
            # git が無い・パスが無いなどは「解決できない」に落とす（例外は投げない）
            return subprocess.CompletedProcess(args=list(args), returncode=1, stdout="", stderr=str(e))
