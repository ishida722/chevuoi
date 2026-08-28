from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class TrelloConfig(BaseModel):
    api_key: str
    api_token: str
    ready_list_id: str
    in_progress_list_id: str
    in_review_list_id: str


class LlmConfig(BaseModel):
    model: str  # 例: "claude-sonnet-5"。認証はプロバイダ既定の環境変数に委ねる


class AppConfig(BaseModel):
    trello: TrelloConfig
    projects: dict[str, Path]
    worktree_root: Path
    node_timeout_sec: int = 3600
    max_parallel: int = 4
    log_file: Path = Path.home() / ".local" / "state" / "vuoi" / "vuoi.log"
    workflows_dir: Path | None = None  # None なら load_config で既定値に解決される
    llm: LlmConfig | None = None  # 未設定でもスキャン・一覧は動く
    workflow_defaults: dict[str, Any] = {}


def default_workflows_dir() -> Path:
    """$XDG_CONFIG_HOME/vuoi/workflows（未設定時は ~/.config/vuoi/workflows）。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "vuoi" / "workflows"


def load_config(path: Path) -> AppConfig:
    """TOML の設定ファイルを読み込む。認証情報は環境変数から補う。"""
    with path.open("rb") as f:
        data = tomllib.load(f)

    trello = data.get("trello", {})
    trello.setdefault("api_key", os.environ.get("TRELLO_KEY", ""))
    trello.setdefault("api_token", os.environ.get("TRELLO_TOKEN", ""))
    data["trello"] = trello

    config = AppConfig.model_validate(data)
    if config.workflows_dir is None:
        # 以降のコードは常に絶対パスを受け取る（設計ドキュメント参照）
        config = config.model_copy(update={"workflows_dir": default_workflows_dir()})
    return config
