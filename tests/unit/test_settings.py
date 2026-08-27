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
    assert str(config.projects["MIRAI"]) == "/repo/mirai"
    assert config.node_timeout_sec == 3600
