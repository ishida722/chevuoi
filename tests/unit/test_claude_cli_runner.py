"""ClaudeCliRunner の単体テスト（subprocess をモック）。"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.workflows.claude_cli_runner import ClaudeCliRunner
from chevuoi.infrastructure.workflows.langchain_llm_factory import LangchainLlmFactory


def make_config(**overrides) -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k",
            api_token="t",
            ready_list_id="r",
            in_progress_list_id="p",
            in_review_list_id="v",
        ),
        projects={},
        worktree_root="/tmp",
        **overrides,
    )


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def runner() -> ClaudeCliRunner:
    return ClaudeCliRunner(make_config(node_timeout_sec=42))


class TestBuildCommand:
    def test_basic(self, runner):
        assert runner.build_command("やる", None) == [
            "claude", "-p", "やる", "--output-format", "json",
        ]

    def test_resume(self, runner):
        cmd = runner.build_command("続き", "sess-1")
        assert cmd[-2:] == ["--resume", "sess-1"]


class TestRun:
    def test_json_success(self, runner):
        payload = json.dumps(
            {"result": "done", "session_id": "s1", "total_cost_usd": 0.12, "is_error": False}
        )
        with patch("subprocess.run", return_value=completed(payload)) as m:
            result = runner.run("やる", cwd=None)
        assert result.ok and result.output == "done"
        assert result.session_id == "s1" and result.cost_usd == 0.12
        assert m.call_args.kwargs["timeout"] == 42

    def test_is_error_flag(self, runner):
        payload = json.dumps({"result": "だめ", "session_id": "s1", "is_error": True})
        with patch("subprocess.run", return_value=completed(payload)):
            assert not runner.run("やる").ok

    def test_non_json_failure_includes_stderr(self, runner):
        with patch(
            "subprocess.run", return_value=completed("out", returncode=1, stderr="err")
        ):
            result = runner.run("やる")
        assert not result.ok and "err" in result.output

    def test_timeout(self, runner):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=42),
        ):
            result = runner.run("やる")
        assert not result.ok and "42" in result.output

    def test_command_not_found(self, runner):
        with patch("subprocess.run", side_effect=OSError("not found")):
            result = runner.run("やる")
        assert not result.ok and "起動に失敗" in result.output


class TestLlmOptional:
    def test_no_llm_config_returns_none(self):
        assert LangchainLlmFactory(make_config()).create() is None
