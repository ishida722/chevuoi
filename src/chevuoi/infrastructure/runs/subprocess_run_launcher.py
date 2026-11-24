from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from chevuoi.domain.entities.exec_run import RunFiles
from chevuoi.domain.exceptions import RunLaunchError
from chevuoi.domain.ports.run_launcher import RunLauncher

logger = logging.getLogger(__name__)


class SubprocessRunLauncher(RunLauncher):
    """vuoi exec を、今動いている Python と同じ環境の別プロセスで起動する。

    - 新しいセッションで起動し、MCP サーバーが終了しても実行が続くようにする
    - 標準入出力は MCP の通信路（stdio）なので、子には渡さずログファイルへ向ける
    - 自分が起動した子は Popen を保持して回収する（回収しないとゾンビになり、
      終了したのに生きているように見える）
    """

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._children: dict[int, subprocess.Popen[bytes]] = {}

    def command(
        self, workflow: str, files: RunFiles, test_commands: Sequence[str] = ()
    ) -> list[str]:
        cmd = [
            sys.executable,
            "-m",
            "chevuoi.interfaces.cli.main",
            "--config",
            str(self._config_path),
            "exec",
            workflow,
            "--brief",
            str(files.brief),
            "--json",
            str(files.result),
        ]
        for tc in test_commands:
            cmd += ["--test-command", tc]
        return cmd

    def launch(
        self,
        workflow: str,
        files: RunFiles,
        *,
        cwd: Path,
        test_commands: Sequence[str] = (),
    ) -> int:
        cmd = self.command(workflow, files, test_commands)
        try:
            with files.log.open("ab") as log:
                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
        except OSError as e:
            raise RunLaunchError(f"vuoi exec を起動できません: {e}") from e
        self._children[proc.pid] = proc
        return proc.pid

    def is_running(self, pid: int) -> bool:
        child = self._children.get(pid)
        if child is not None:
            return child.poll() is None
        # 以前の MCP サーバーが起動したプロセス。生存だけを確かめる
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # 存在はする（別ユーザーのプロセスに pid が再利用された可能性は残る）
        return True

    def terminate(self, pid: int) -> None:
        if not self.is_running(pid):
            return
        try:
            # 新しいセッションで起動したので pid がプロセスグループ ID。
            # vuoi exec が起動した claude -p などの子プロセスもまとめて止める
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except PermissionError as e:
            logger.warning("プロセスを止められません: pid=%s (%s)", pid, e)
            return
        child = self._children.get(pid)
        if child is not None:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(pid, signal.SIGKILL)
                child.wait(timeout=10)
