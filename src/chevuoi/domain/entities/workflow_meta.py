from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from chevuoi.domain.value_objects.workflow_name import Intent, Tag, WorkflowName

SUPPORTED_API_VERSION = 1


class Capabilities(BaseModel):
    """実行特性。呼び出し側の事前判断に使う。"""

    model_config = ConfigDict(extra="forbid")

    requires_network: bool = False
    streaming: bool = False
    estimated_seconds: int | None = None


class WorkflowMeta(BaseModel):
    """workflow.toml の内容 + ディレクトリ情報。検証ルール（仕様 §3）を担う。"""

    model_config = ConfigDict(extra="forbid")

    # --- ディレクトリ由来（TOML には書かない。単一の真実源） ---
    name: WorkflowName
    path: Path
    entry_path: Path

    # --- TOML 由来 ---
    api_version: int = Field(ge=SUPPORTED_API_VERSION, le=SUPPORTED_API_VERSION)
    summary: str = Field(min_length=1)
    version: str = "0.0.0"
    enabled: bool = True
    when_to_use: str = ""
    tags: list[Tag] = []
    intents: list[Intent] = []
    priority: int = 50
    # 終端処理の宣言。pr: 差分があれば PR を作る / comment: 結果をカードにコメントするだけ
    outcome: Literal["pr", "comment"] = "pr"
    capabilities: Capabilities = Capabilities()
    settings: dict[str, Any] = {}
    entry: str = "workflow.py"


class ScanResult(BaseModel):
    """スキャン結果。壊れたワークフローも invalid として一覧可能なまま隔離する。"""

    metas: dict[str, WorkflowMeta] = {}
    invalid: dict[str, str] = {}
