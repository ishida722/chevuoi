import subprocess
from pathlib import Path

import pytest

from chevuoi.domain.entities.project import Project
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


class TestGitWorktreeManager:
    def test_create_makes_worktree_with_derived_branch(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X: test"))
        assert worktree.path.exists()
        assert worktree.branch.value == "chevuoi/fake-x1"

    def test_create_is_idempotent(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        card = FakeCard("X: test")
        first = manager.create(project, card)
        second = manager.create(project, card)
        assert first == second

    def test_remove_deletes_worktree(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        worktree = manager.create(project, FakeCard("X: test"))
        manager.remove(worktree)
        assert not worktree.path.exists()

    def test_recreate_after_remove_reuses_existing_branch(self, tmp_path, repo):
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)
        card = FakeCard("X: test")
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
        worktree = manager.create(project, FakeCard("X: test"))
        assert manager.list_stale(older_than_days=1) == []
        old = time.time() - 2 * 86400
        os.utime(worktree.path, (old, old))
        found = manager.list_stale(older_than_days=1)
        assert [w.path for w in found] == [worktree.path]
