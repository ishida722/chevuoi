from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Sequence
from pathlib import Path

from injector import inject

from vuoi_sdk import Runner, RunResult

from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger("vuoi.workflows.runner")


class ClaudeCliRunner(Runner):
    """claude -p を --output-format json で 1 回実行する。

    NodeRunner（カード処理用・Worktree 前提）とは契約が異なる：
    セッション継続と構造化結果（session_id / コスト）を提供する。
    失敗は例外ではなく RunResult(ok=False) で返す（LoadFailure と同じ流儀）。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._timeout = config.node_timeout_sec

    def build_command(
        self,
        prompt: str,
        session_id: str | None,
        allowed_tools: Sequence[str] | None = None,
    ) -> list[str]:
        cmd = ["claude", "-p", prompt, "--output-format", "json"]
        if session_id is not None:
            cmd += ["--resume", session_id]
        if allowed_tools is not None:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
        return cmd

    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        session_id: str | None = None,
        allowed_tools: Sequence[str] | None = None,
    ) -> RunResult:
        try:
            proc = subprocess.run(
                self.build_command(prompt, session_id, allowed_tools),
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult(ok=False, output=f"タイムアウト（{self._timeout}秒）")
        except OSError as e:
            return RunResult(ok=False, output=f"claude の起動に失敗: {e}")

        result = self._parse(proc)
        logger.info(
            "claude 実行: ok=%s session=%s cost=%s",
            result.ok,
            result.session_id,
            result.cost_usd,
        )
        return result

    def _parse(self, proc: subprocess.CompletedProcess[str]) -> RunResult:
        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            # JSON が出ない異常系（起動前エラー等）は生の出力をそのまま返す
            ok = proc.returncode == 0
            output = proc.stdout if ok else (proc.stdout + proc.stderr)
            return RunResult(ok=ok, output=output.strip())
        ok = proc.returncode == 0 and not data.get("is_error", False)
        return RunResult(
            ok=ok,
            output=str(data.get("result", "")),
            session_id=data.get("session_id"),
            cost_usd=data.get("total_cost_usd"),
        )
