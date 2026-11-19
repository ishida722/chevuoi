from __future__ import annotations

import subprocess
from pathlib import Path

from chevuoi.domain.exceptions import WorkspaceError
from chevuoi.domain.ports.workspace_inspector import Workspace, WorkspaceInspector

TIMEOUT_SEC = 30


class GitWorkspaceInspector(WorkspaceInspector):
    """git で今いる作業ツリーを読む。"""

    def locate(self, path: Path) -> Workspace:
        root = Path(self._git(path, "rev-parse", "--show-toplevel").strip())
        # 本体の .git（worktree なら本体側、そうでなければ root/.git）。相対で返る
        # ことがあるので root 基準で絶対パスにする
        common = Path(self._git(root, "rev-parse", "--git-common-dir").strip())
        if not common.is_absolute():
            common = root / common
        common = common.resolve()
        main_root = common.parent if common.name == ".git" else root
        return Workspace(root=root.resolve(), main_root=main_root)

    def changed_files(self, root: Path) -> list[str]:
        # --untracked-files=all: 新規ディレクトリを 1 行に畳まず、作られたファイルを並べる
        out = self._git(root, "status", "--porcelain", "--untracked-files=all")
        return [line for line in out.splitlines() if line.strip()]

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(cwd), *args],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SEC,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise WorkspaceError(f"git を実行できませんでした: {e}") from e
        if result.returncode != 0:
            raise WorkspaceError(
                f"git リポジトリとして扱えません（{cwd}）: {result.stderr.strip()}"
            )
        return result.stdout
