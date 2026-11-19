"""GitWorkspaceInspector（vuoi exec が今いる作業ツリーを読む）の結合テスト。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chevuoi.domain.exceptions import WorkspaceError
from chevuoi.infrastructure.git.git_workspace_inspector import GitWorkspaceInspector
from tests.integration.test_git_worktree import repo  # noqa: F401 フィクスチャの再利用


def test_locate_from_subdirectory(repo: Path):  # noqa: F811
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    ws = GitWorkspaceInspector().locate(sub)
    assert ws.root == repo.resolve()
    assert ws.main_root == repo.resolve()


def test_locate_in_worktree_points_main_root_to_main_repository(repo: Path, tmp_path: Path):  # noqa: F811
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
    ws = GitWorkspaceInspector().locate(wt)
    assert ws.root == wt.resolve()
    assert ws.main_root == repo.resolve()


def test_locate_outside_repository_raises(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorkspaceError):
        GitWorkspaceInspector().locate(plain)


def test_changed_files_lists_untracked_files_individually(repo: Path):  # noqa: F811
    inspector = GitWorkspaceInspector()
    assert inspector.changed_files(repo) == []
    (repo / "pkg").mkdir()
    (repo / "pkg" / "x.py").write_text("x", encoding="utf-8")
    assert inspector.changed_files(repo) == ["?? pkg/x.py"]
