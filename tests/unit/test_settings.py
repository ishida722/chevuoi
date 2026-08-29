from chevuoi.infrastructure.config.settings import load_config


def test_load_config_reads_toml_and_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'worktree_root = "/tmp/wt"\n'
        "[trello]\n"
        'ready_list_id = "r"\n'
        'in_progress_list_id = "d"\n'
        'in_review_list_id = "v"\n'
        "[projects]\n"
        'MIRAI = "/repo/mirai"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_KEY", "env-key")
    monkeypatch.setenv("TRELLO_TOKEN", "env-token")
    config = load_config(config_path)
    assert config.trello.api_key == "env-key"
    assert config.trello.api_token == "env-token"
    assert str(config.projects["MIRAI"].path) == "/repo/mirai"
    assert config.projects["MIRAI"].test_commands == []
    assert config.node_timeout_sec == 3600
    assert config.log_file.name == "vuoi.log"


def test_load_config_accepts_log_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'worktree_root = "/tmp/wt"\n'
        'log_file = "/tmp/logs/vuoi.log"\n'
        "[trello]\n"
        'ready_list_id = "r"\n'
        'in_progress_list_id = "d"\n'
        'in_review_list_id = "v"\n'
        "[projects]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "t")
    config = load_config(config_path)
    assert str(config.log_file) == "/tmp/logs/vuoi.log"


def test_load_config_accepts_project_table_with_test_commands(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'worktree_root = "/tmp/wt"\n'
        "[trello]\n"
        'ready_list_id = "r"\n'
        'in_progress_list_id = "d"\n'
        'in_review_list_id = "v"\n'
        "[projects]\n"
        'short = "/repo/short"\n'
        "[projects.full]\n"
        'path = "/repo/full"\n'
        'test_commands = ["uv run pytest -q", "uv run ruff check ."]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "t")
    config = load_config(config_path)
    assert str(config.projects["short"].path) == "/repo/short"
    assert config.projects["short"].test_commands == []
    assert str(config.projects["full"].path) == "/repo/full"
    assert config.projects["full"].test_commands == ["uv run pytest -q", "uv run ruff check ."]


def test_inbox_list_id_and_proposals_default(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'worktree_root = "/tmp/wt"\n'
        "[trello]\n"
        'ready_list_id = "r"\n'
        'in_progress_list_id = "d"\n'
        'in_review_list_id = "v"\n'
        "[projects]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "t")
    config = load_config(config_path)
    assert config.trello.inbox_list_id is None
    assert config.proposals.max_per_run == 3
    assert config.proposals.max_generation == 2
    assert config.router.model is None


def test_inbox_list_id_and_proposals_from_toml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'worktree_root = "/tmp/wt"\n'
        "[trello]\n"
        'ready_list_id = "r"\n'
        'in_progress_list_id = "d"\n'
        'in_review_list_id = "v"\n'
        'inbox_list_id = "inbox"\n'
        "[proposals]\n"
        "max_per_run = 5\n"
        "[projects]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "t")
    config = load_config(config_path)
    assert config.trello.inbox_list_id == "inbox"
    assert config.proposals.max_per_run == 5


def test_router_model_from_toml(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'worktree_root = "/tmp/wt"\n'
        "[trello]\n"
        'ready_list_id = "r"\n'
        'in_progress_list_id = "d"\n'
        'in_review_list_id = "v"\n'
        "[router]\n"
        'model = "haiku"\n'
        "[projects]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "t")
    config = load_config(config_path)
    assert config.router.model == "haiku"
