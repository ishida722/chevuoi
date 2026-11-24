"""SubprocessRunLauncher を実際のプロセスで確かめる。"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from chevuoi.domain.entities.exec_run import RunFiles
from chevuoi.infrastructure.runs.subprocess_run_launcher import SubprocessRunLauncher


class ScriptLauncher(SubprocessRunLauncher):
    """vuoi exec の代わりに任意の Python スクリプトを起動する。"""

    def __init__(self, script: str) -> None:
        super().__init__(Path("/unused/config.toml"))
        self._script = script

    def command(self, workflow, files, test_commands=()):
        return [sys.executable, "-c", self._script]


def make_files(tmp_path: Path) -> RunFiles:
    return RunFiles(
        brief=tmp_path / "brief.md", result=tmp_path / "result.json", log=tmp_path / "exec.log"
    )


def wait_until(cond, timeout=10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_command_runs_vuoi_exec_with_same_config():
    launcher = SubprocessRunLauncher(Path("/abs/config.toml"))
    files = RunFiles(brief=Path("/r/brief.md"), result=Path("/r/result.json"), log=Path("/r/log"))
    cmd = launcher.command("dev", files, ["pytest -q", "ruff check"])
    assert cmd[:3] == [sys.executable, "-m", "chevuoi.interfaces.cli.main"]
    assert cmd[3:] == [
        "--config", "/abs/config.toml", "exec", "dev",
        "--brief", "/r/brief.md", "--json", "/r/result.json",
        "--test-command", "pytest -q", "--test-command", "ruff check",
    ]


def test_finished_child_is_reaped_and_output_goes_to_log(tmp_path):
    launcher = ScriptLauncher("import os; print('cwd=' + os.getcwd())")
    files = make_files(tmp_path)
    pid = launcher.launch("dev", files, cwd=tmp_path)

    # 回収しないとゾンビのまま「動いている」と判定される
    assert wait_until(lambda: not launcher.is_running(pid))
    assert f"cwd={tmp_path.resolve()}" in files.log.read_text()


def test_terminate_stops_running_child(tmp_path):
    launcher = ScriptLauncher("import time; time.sleep(60)")
    pid = launcher.launch("dev", make_files(tmp_path), cwd=tmp_path)
    assert launcher.is_running(pid)

    launcher.terminate(pid)

    assert not launcher.is_running(pid)


def test_process_from_another_launcher_is_seen_via_pid(tmp_path):
    """MCP サーバーを再起動したあと（Popen を持たない）でも生存を確かめられる。"""
    owner = ScriptLauncher("import time; time.sleep(60)")
    pid = owner.launch("dev", make_files(tmp_path), cwd=tmp_path)
    other = SubprocessRunLauncher(Path("/unused"))
    try:
        assert other.is_running(pid)
    finally:
        owner.terminate(pid)
    assert not other.is_running(pid)
