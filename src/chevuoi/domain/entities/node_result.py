from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class NodeStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"


class NodeResult(BaseModel):
    """処理ノード（claude -p 1回）の実行結果。"""

    status: NodeStatus
    output: str
