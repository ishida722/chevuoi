import subprocess
from pathlib import Path

import pytest

from chevuoi.domain.entities.project import NullProject, Project
from chevuoi.domain.exceptions import ProjectNotResolvedError, WorktreeError
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.git.git_repository_inspector import GitRepositoryInspector
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


def _git_out(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


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


def _head_sha(cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _advance_origin(tmp_path: Path, name: str) -> str:
    """別クローン経由で origin の既定ブランチを 1 コミット進め、その SHA を返す。

    本体リポジトリを経由しないので、`repo` 側はこのコミットを知らない状態になる。
    """
    origin = tmp_path / "origin.git"
    clone = tmp_path / f"clone-{name}"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    (clone / f"{name}.txt").write_text(name)
    _commit(clone, name)
    subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "HEAD"], check=True)
    return _head_sha(clone)


class TestGitWorktreeManager:
    def test_unresolved_project_does_not_touch_the_repo_at_cwd(self, tmp_path, repo, monkeypatch):
        """リポジトリが未解決のプロジェクトで worktree を作ろうとすると例外になり、
        実行場所（カレントディレクトリ）のリポジトリにブランチも worktree も作らないこと。"""
        manager = make_manager(tmp_path)
        monkeypatch.chdir(repo)
        worktrees_before = _git_out(repo, "worktree", "list")

        raised: Exception | None = None
        try:
            manager.create(NullProject(), FakeCard("X test"))
        except Exception as e:  # noqa: BLE001 - 例外の種類は下でまとめて確かめる
            raised = e

        # どんな終わり方をしても、まず「実行場所のリポジトリが無傷か」を確かめる。
        # 壊れたときに「未解決なのに cwd の git を触った」と分かる落ち方にするため
        assert "chevuoi/" not in _git_out(repo, "branch", "--list")
        assert _git_out(repo, "worktree", "list") == worktrees_before
        assert isinstance(raised, ProjectNotResolvedError), f"想定外の終わり方: {raised!r}"

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

    def test_create_branches_from_latest_remote_default_branch(self, tmp_path, repo_with_origin):
        """本体側が知らない最新コミットがリモートの既定ブランチにあるとき、
        worktree はローカルの状態ではなくその最新コミットから分岐すること。"""
        manager = make_manager(tmp_path)
        # 本体側は別ブランチの未 push コミットを抱えたまま、既定ブランチも古い
        subprocess.run(["git", "-C", str(repo_with_origin), "checkout", "-q", "-b", "other"],
                       check=True)
        (repo_with_origin / "other.txt").write_text("other")
        _commit(repo_with_origin, "other work")
        remote_tip = _advance_origin(tmp_path, "remote-work")
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)

        worktree = manager.create(project, FakeCard("X test"))

        assert _head_sha(worktree.path) == remote_tip
        assert not (worktree.path / "other.txt").exists()

    def test_create_follows_renamed_remote_default_branch(self, tmp_path, repo_with_origin):
        """リモートの既定ブランチが改名されたとき、ローカルに残る古い origin/HEAD ではなく
        新しい既定ブランチの最新から分岐すること。"""
        manager = make_manager(tmp_path)
        origin = tmp_path / "origin.git"
        clone = tmp_path / "clone-rename"
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-b", "renamed"], check=True)
        (clone / "renamed.txt").write_text("renamed")
        _commit(clone, "renamed work")
        subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "renamed"], check=True)
        subprocess.run(
            ["git", "-C", str(origin), "symbolic-ref", "HEAD", "refs/heads/renamed"], check=True
        )
        # ローカルの origin/HEAD は改名前を指したまま（実体は残っているので解決はできる）
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)

        worktree = manager.create(project, FakeCard("X test"))

        assert _head_sha(worktree.path) == _head_sha(clone)

    @pytest.mark.parametrize("break_origin_head", ["delete", "dangling"])
    def test_create_ignores_unusable_local_origin_head(
        self, tmp_path, repo_with_origin, break_origin_head
    ):
        """ローカルの origin/HEAD が無い・壊れている場合でも、
        リモートの既定ブランチの最新から分岐すること。"""
        manager = make_manager(tmp_path)
        if break_origin_head == "delete":
            args = ["symbolic-ref", "-d", "refs/remotes/origin/HEAD"]
        else:
            args = ["symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone"]
        subprocess.run(["git", "-C", str(repo_with_origin), *args], check=True)
        remote_tip = _advance_origin(tmp_path, "remote-work")
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)

        worktree = manager.create(project, FakeCard("X test"))

        assert _head_sha(worktree.path) == remote_tip

    def test_create_fetches_default_branch_in_narrow_refspec_clone(
        self, tmp_path, repo_with_origin
    ):
        """既定の refspec が既定ブランチを含まないクローンでも、
        既定ブランチを取得して最新から分岐すること。"""
        manager = make_manager(tmp_path)
        default_branch = subprocess.run(
            ["git", "-C", str(repo_with_origin), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(repo_with_origin), "checkout", "-q", "-b", "side"],
                       check=True)
        subprocess.run(["git", "-C", str(repo_with_origin), "push", "-q", "origin", "side"],
                       check=True)
        narrow = tmp_path / "narrow"
        subprocess.run(
            ["git", "clone", "-q", "--single-branch", "--branch", "side",
             str(tmp_path / "origin.git"), str(narrow)],
            check=True,
        )
        assert default_branch not in subprocess.run(
            ["git", "-C", str(narrow), "config", "remote.origin.fetch"],
            capture_output=True, text=True, check=True,
        ).stdout
        remote_tip = _advance_origin(tmp_path, "remote-work")
        project = Project(tag=ProjectTag(value="X"), repo_path=narrow)

        worktree = manager.create(project, FakeCard("X test"))

        assert _head_sha(worktree.path) == remote_tip

    def test_create_does_not_set_upstream_on_new_branch(self, tmp_path, repo_with_origin):
        """新規ブランチには upstream を設定しないこと（worktree 内の素の git push / pull を
        ベースブランチに向けず、共有の .git/config も書かないため）。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)

        worktree = manager.create(project, FakeCard("X test"))

        upstream = subprocess.run(
            ["git", "-C", str(worktree.path), "rev-parse", "--abbrev-ref",
             "--symbolic-full-name", "@{upstream}"],
            capture_output=True, text=True,
        )
        assert upstream.returncode != 0, f"upstream が設定されている: {upstream.stdout.strip()}"

    @pytest.mark.parametrize("breakage", ["unreachable", "no_branches"])
    def test_create_fails_when_default_branch_cannot_be_resolved(
        self, tmp_path, repo_with_origin, breakage
    ):
        """リモートの既定ブランチを解決できないときは、古いローカルのベースで作らず
        「解決できない」と分かるエラーにすること。"""
        manager = make_manager(tmp_path)
        if breakage == "unreachable":
            url = tmp_path / "missing.git"
        else:
            url = tmp_path / "empty.git"
            subprocess.run(["git", "init", "--bare", "-q", str(url)], check=True)
        subprocess.run(
            ["git", "-C", str(repo_with_origin), "remote", "set-url", "origin", str(url)],
            check=True,
        )
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)

        with pytest.raises(WorktreeError, match="既定ブランチを解決できませんでした"):
            manager.create(project, FakeCard("X test"))
        assert not (tmp_path / "worktrees" / "chevuoi-fake-x1").exists()

    def test_create_fails_when_remote_cannot_be_fetched(self, tmp_path, repo_with_origin):
        """既定ブランチは解決できても取得に失敗したときは、古いローカルのベースで作らず
        「取得に失敗」と分かるエラーにすること。"""
        manager = make_manager(tmp_path)
        default_branch = subprocess.run(
            ["git", "-C", str(repo_with_origin), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # リモートの ref を実体の無いコミットに向ける（ls-remote は成功し fetch が失敗する）
        ref = tmp_path / "origin.git" / "refs" / "heads" / default_branch
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text("0" * 39 + "1\n")
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)

        with pytest.raises(WorktreeError, match="origin の取得に失敗しました"):
            manager.create(project, FakeCard("X test"))
        assert not (tmp_path / "worktrees" / "chevuoi-fake-x1").exists()

    def test_create_without_origin_uses_repository_head(self, tmp_path, repo):
        """origin を持たないリポジトリでは、リポジトリの HEAD から作れること。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo)

        worktree = manager.create(project, FakeCard("X test"))

        assert _head_sha(worktree.path) == _head_sha(repo)

    def test_recreate_after_remove_keeps_work_on_existing_branch(self, tmp_path, repo_with_origin):
        """同名ブランチが既にある場合は、最新 main で作り直さずそのブランチの成果を残すこと。"""
        manager = make_manager(tmp_path)
        project = Project(tag=ProjectTag(value="X"), repo_path=repo_with_origin)
        card = FakeCard("X test")
        first = manager.create(project, card)
        (first.path / "a.txt").write_text("a")
        _commit(first.path, "work")
        manager.remove(first)
        _advance_origin(tmp_path, "remote-work")

        second = manager.create(project, card)

        assert (second.path / "a.txt").read_text() == "a"


def make_inspector(tmp_path: Path) -> GitRepositoryInspector:
    config = AppConfig(
        trello=TrelloConfig(api_key="k", api_token="t", ready_list_id="r",
                            in_progress_list_id="d", in_review_list_id="v"),
        projects={},
        worktree_root=tmp_path / "worktrees",
    )
    return GitRepositoryInspector(config)


def project_for(repo: Path, base_ref: str = "") -> Project:
    return Project(tag=ProjectTag(value="X"), repo_path=repo, base_ref=base_ref)


@pytest.fixture
def repo_with_history(repo: Path) -> Path:
    """foo.txt / bar.txt を持つコミットが 1 つあるリポジトリ。"""
    (repo / "foo.txt").write_text("1")
    (repo / "bar.txt").write_text("1")
    _commit(repo, "add files")
    return repo


class TestGitRepositoryInspector:
    def test_base_commit_is_empty_when_the_repository_is_missing(self, tmp_path):
        """リポジトリを読めないとき、例外ではなく空文字を返すこと（起票を止めない）。"""
        inspector = make_inspector(tmp_path)
        assert inspector.base_commit(project_for(tmp_path / "nope")) == ""

    def test_configured_base_ref_is_used(self, tmp_path, repo_with_history):
        """base_ref が設定されているとき、その参照のコミットを返すこと。"""
        subprocess.run(["git", "-C", str(repo_with_history), "branch", "release"], check=True)
        inspector = make_inspector(tmp_path)
        head = subprocess.run(["git", "-C", str(repo_with_history), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        assert inspector.base_commit(project_for(repo_with_history, "release")) == head

    def test_unknown_base_ref_falls_back(self, tmp_path, repo_with_history):
        """設定された base_ref が実在しないとき、既定の解決（現在ブランチ）に落ちること。"""
        inspector = make_inspector(tmp_path)
        assert inspector.base_commit(project_for(repo_with_history, "no/such/ref")) != ""

    def test_path_exists_looks_at_the_ref_not_the_worktree(self, tmp_path, repo_with_history):
        """作業ツリーにしか無いファイルは、ベースに存在しないと答えること。"""
        (repo_with_history / "untracked.txt").write_text("x")
        inspector = make_inspector(tmp_path)
        project = project_for(repo_with_history)
        assert inspector.path_exists(project, "foo.txt") is True
        assert inspector.path_exists(project, "untracked.txt") is False

    def test_changed_since_distinguishes_touched_paths(self, tmp_path, repo_with_history):
        """起票時コミット以降に変更のあったパスだけを「変更あり」と答えること。"""
        inspector = make_inspector(tmp_path)
        project = project_for(repo_with_history)
        since = inspector.base_commit(project)
        (repo_with_history / "foo.txt").write_text("2")
        _commit(repo_with_history, "touch foo")
        assert inspector.changed_since(project, "foo.txt", since) is True
        assert inspector.changed_since(project, "bar.txt", since) is False

    def test_changed_since_is_true_for_an_unknown_commit(self, tmp_path, repo_with_history):
        """起票時コミットが解決できないとき、変更ありとして扱うこと（安全側）。"""
        inspector = make_inspector(tmp_path)
        project = project_for(repo_with_history)
        assert inspector.changed_since(project, "foo.txt", "0" * 40) is True
        assert inspector.changed_since(project, "foo.txt", "") is True

    def test_base_checkout_is_detached_and_reused(self, tmp_path, repo_with_history):
        """ベースのチェックアウトはブランチを作らず（detached）、2 回目も作り直さないこと。"""
        inspector = make_inspector(tmp_path)
        project = project_for(repo_with_history)
        path = inspector.base_checkout(project)
        branches = subprocess.run(["git", "-C", str(repo_with_history), "branch", "--list"],
                                  capture_output=True, text=True, check=True).stdout
        head = subprocess.run(["git", "-C", str(path), "symbolic-ref", "-q", "HEAD"],
                              capture_output=True, text=True)
        assert head.returncode != 0  # detached HEAD
        assert inspector.base_checkout(project) == path
        assert subprocess.run(["git", "-C", str(repo_with_history), "branch", "--list"],
                              capture_output=True, text=True, check=True).stdout == branches

    def test_base_checkout_is_not_collected_by_gc(self, tmp_path, repo_with_history):
        """ベースのチェックアウトは vuoi gc の掃除対象（chevuoi- 始まり）にならないこと。"""
        inspector = make_inspector(tmp_path)
        path = inspector.base_checkout(project_for(repo_with_history))
        assert not path.name.startswith("chevuoi-")
        assert make_manager(tmp_path).list_stale(older_than_days=0) == []

    def test_refresh_succeeds_without_a_remote(self, tmp_path, repo_with_history):
        """リモートの無いリポジトリでは、取りに行くものが無いので成功とすること。"""
        assert make_inspector(tmp_path).refresh(project_for(repo_with_history)) is True

    def test_refresh_fails_when_the_remote_is_unreachable(self, tmp_path, repo_with_origin):
        """ベース参照を更新できないとき、例外ではなく False を返すこと
        （呼び側はそのプロジェクトの鮮度判定だけを見送る）。"""
        subprocess.run(["git", "-C", str(repo_with_origin), "remote", "set-url", "origin",
                        str(tmp_path / "gone.git")], check=True)
        assert make_inspector(tmp_path).refresh(project_for(repo_with_origin)) is False
