from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel


class TrelloConfig(BaseModel):
    api_key: str
    api_token: str
    ready_list_id: str
    in_progress_list_id: str
    in_review_list_id: str


class AppConfig(BaseModel):
    trello: TrelloConfig
    projects: dict[str, Path]
    worktree_root: Path
    node_timeout_sec: int = 3600
    log_file: Path = Path.home() / ".local" / "state" / "vuoi" / "vuoi.log"


def load_config(path: Path) -> AppConfig:
    """TOML の設定ファイルを読み込む。認証情報は環境変数から補う。"""
    with path.open("rb") as f:
        data = tomllib.load(f)

    trello = data.get("trello", {})
    trello.setdefault("api_key", os.environ.get("TRELLO_KEY", ""))
    trello.setdefault("api_token", os.environ.get("TRELLO_TOKEN", ""))
    data["trello"] = trello

    return AppConfig.model_validate(data)
