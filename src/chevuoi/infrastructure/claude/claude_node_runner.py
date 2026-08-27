from __future__ import annotations

import subprocess

from injector import inject

from chevuoi.domain.entities.node_result import NodeResult, NodeStatus
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.ports.node_runner import NodeRunner
from chevuoi.infrastructure.config.settings import AppConfig


class ClaudeNodeRunner(NodeRunner):
    """worktree を作業ディレクトリとして claude -p を1回実行する。

    汎用の claude 実行ランナー。プロンプトは外部（ユースケース）から
    注入されたものをそのまま渡し、内容には関与しない。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._timeout = config.node_timeout_sec

    def build_command(self, prompt: str) -> list[str]:
        return ["claude", "-p", prompt]

    def run(self, worktree: Worktree, prompt: str) -> NodeResult:
        try:
            result = subprocess.run(
                self.build_command(prompt),
                cwd=worktree.path,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return NodeResult(
                status=NodeStatus.FAILED,
                output=f"タイムアウト（{self._timeout}秒）",
            )
        except OSError as e:
            # claude が PATH に無い場合など。失敗としてカードに返せるようにする
            return NodeResult(status=NodeStatus.FAILED, output=f"ノード起動失敗: {e}")
        status = NodeStatus.DONE if result.returncode == 0 else NodeStatus.FAILED
        output = result.stdout if status is NodeStatus.DONE else (
            result.stdout + result.stderr
        )
        return NodeResult(status=status, output=output.strip())
