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
