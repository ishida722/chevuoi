import subprocess
from pathlib import Path

import pytest

from chevuoi.domain.entities.project import Project
from chevuoi.domain.exceptions import WorktreeError
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.git.git_worktree_manager import GitWorktreeManager
from tests.unit.fakes import FakeCard


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", "init", "-q"],
                   check=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})
    return repo


def make_manager(tmp_path: Path) -> GitWorktreeManager:
    config = AppConfig(
        trello=TrelloConfig(api_key="k", api_token="t", ready_list_id="r",
                            in_progress_list_id="d", in_review_list_id="v"),
        projects={},
        worktree_root=tmp_path / "worktrees",
    )
    return GitWorktreeManager(config)


def _commit(cwd: Path, message: str) -> None:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    subprocess.run(["git", "-C", str(cwd), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(cwd), "commit", "-m", message, "-q"], check=True, env=env)


@pytest.fixture
def repo_with_origin(tmp_path: Path, repo: Path) -> Path:
    """origin（bare）を持ち、origin/HEAD が設定されたリポジトリ。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "push", "-q", "-u", "origin", "HEAD"], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "set-head", "origin", "-a"], check=True)
    return repo


class TestGitWorktreeManager:
    def test_create_makes_worktree_with_derived_branch(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X test"))
        assert worktree.path.exists()
        assert worktree.branch.value == "chevuoi/fake-x1"

    def test_create_is_idempotent(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        card = FakeCard("X test")
        first = manager.create(project, card)
        second = manager.create(project, card)
        assert first == second

    def test_remove_deletes_worktree(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X test"))
        manager.remove(worktree)
        assert not worktree.path.exists()

    def test_recreate_after_remove_reuses_existing_branch(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        card = FakeCard("X test")
        first = manager.create(project, card)
        manager.remove(first)
        second = manager.create(project, card)
        assert second.path.exists()
        assert second.branch == first.branch

    def test_list_stale_skips_non_git_directory(self, tmp_path, repo):
        import os
        import time

        manager = make_manager(tmp_path)
        junk = tmp_path / "worktrees" / "chevuoi-trello-dead"
        junk.mkdir(parents=True)
        old = time.time() - 2 * 86400
        os.utime(junk, (old, old))
        assert manager.list_stale(older_than_days=1) == []

    def test_list_stale_by_age(self, tmp_path, repo):
        import os
        import time

        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X test"))
        assert manager.list_stale(older_than_days=1) == []
        old = time.time() - 2 * 86400
        os.utime(worktree.path, (old, old))
        found = manager.list_stale(older_than_days=1)
        assert [w.path for w in found] == [worktree.path]

    def test_has_changes_false_on_clean_worktree(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X test"))
        assert manager.has_changes(worktree) is False

    def test_has_changes_true_for_uncommitted_file(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X test"))
        (worktree.path / "a.txt").write_text("a")
        assert manager.has_changes(worktree) is True

    def test_has_changes_true_for_committed_work(self, tmp_path, repo):
        """ワークフローが自分でコミットしても成果は失われない。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X test"))
        (worktree.path / "a.txt").write_text("a")
        _commit(worktree.path, "work")
        assert manager.has_changes(worktree) is True

    def test_has_changes_false_when_base_branch_is_ahead_of_remote(
        self, tmp_path, repo_with_origin
    ):
        """ローカルのベースブランチが未 push で進んでいても、成果ゼロなら変更なし。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)
        worktree = manager.create(project, FakeCard("X test"))
        (repo_with_origin / "base.txt").write_text("base")
        _commit(repo_with_origin, "base work")
        assert manager.has_changes(worktree) is False

    def test_has_changes_true_for_committed_and_pushed_work(self, tmp_path, repo_with_origin):
        """ワークフローが自分で push しても成果は失われない。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)
        worktree = manager.create(project, FakeCard("X test"))
        (worktree.path / "a.txt").write_text("a")
        _commit(worktree.path, "work")
        subprocess.run(
            ["git", "-C", str(worktree.path), "push", "-q", "-u", "origin", "HEAD"], check=True
        )
        assert manager.has_changes(worktree) is True

    def test_has_changes_raises_when_base_ref_is_broken(self, tmp_path, repo_with_origin):
        """ベースブランチを解決できない場合は黙って「変更なし」にせずエラーにする。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)
        worktree = manager.create(project, FakeCard("X test"))
        (worktree.path / "a.txt").write_text("a")
        _commit(worktree.path, "work")
        subprocess.run(
            ["git", "-C", str(repo_with_origin), "symbolic-ref",
             "refs/remotes/origin/HEAD", "refs/remotes/origin/gone"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_with_origin), "checkout", "-q", "--detach"], check=True
        )
        with pytest.raises(WorktreeError):
            manager.has_changes(worktree)
