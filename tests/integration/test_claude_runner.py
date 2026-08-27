from pathlib import Path

from chevuoi.infrastructure.claude.claude_node_runner import ClaudeNodeRunner
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig


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
    def test_build_command_passes_prompt_verbatim(self):
        command = make_runner().build_command("チケットに対応してください")
        assert command == ["claude", "-p", "チケットに対応してください"]
