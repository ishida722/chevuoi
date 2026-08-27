from __future__ import annotations

import subprocess

from injector import inject

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.node_result import NodeResult, NodeStatus
from chevuoi.domain.entities.worktree import Worktree
from chevuoi.domain.ports.node_runner import NodeRunner
from chevuoi.infrastructure.config.settings import AppConfig

PROMPT_TEMPLATE = """\
次のチケットに対応してください。

タイトル: {name}
URL: {url}

本文:
{desc}

このリポジトリの規約（CLAUDE.md・テスト・lint）に従って実装し、
テストを通した上でブランチを push し、PR を作成してください。
PR の作成までで停止し、マージはしないでください。
最後に PR の URL を出力してください。
"""


class ClaudeNodeRunner(NodeRunner):
    """worktree を作業ディレクトリとして claude -p を1回実行する。"""

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._timeout = config.node_timeout_sec

    def build_command(self, card: Card) -> list[str]:
        prompt = PROMPT_TEMPLATE.format(name=card.name, url=card.url, desc=card.desc)
        return ["claude", "-p", prompt]

    def run(self, worktree: Worktree, card: Card) -> NodeResult:
        try:
            result = subprocess.run(
                self.build_command(card),
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
