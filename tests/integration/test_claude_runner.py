from pathlib import Path

from chevuoi.infrastructure.claude.claude_node_runner import ClaudeNodeRunner
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from tests.unit.fakes import FakeCard


def make_runner() -> ClaudeNodeRunner:
    config = AppConfig(
        trello=TrelloConfig(api_key="k", api_token="t", ready_list_id="r",
                            in_progress_list_id="d", in_review_list_id="v"),
        projects={},
        worktree_root=Path("/tmp/worktrees"),
        node_timeout_sec=10,
    )
    return ClaudeNodeRunner(config)


class TestClaudeNodeRunner:
    def test_build_command_embeds_card_fields(self):
        command = make_runner().build_command(FakeCard("MIRAI: 修正"))
        assert command[:2] == ["claude", "-p"]
        prompt = command[2]
        assert "MIRAI: 修正" in prompt
        assert "https://example.com/card" in prompt
        assert "desc" in prompt
