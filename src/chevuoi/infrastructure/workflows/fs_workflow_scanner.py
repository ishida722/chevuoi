from __future__ import annotations

import re
import tomllib

from injector import inject
from pydantic import ValidationError

from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner
from chevuoi.infrastructure.config.settings import AppConfig

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _summarize_validation_error(exc: ValidationError) -> str:
    """どのフィールドが何に落ちたかを人間可読に要約する。"""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        if err["type"] == "extra_forbidden":
            parts.append(f"未知のフィールド '{loc}'")
        else:
            parts.append(f"{loc}: {err['msg']}")
    return "workflow.toml: " + "; ".join(parts)


class FsWorkflowScanner(WorkflowScanner):
    """workflows_dir 直下を走査して workflow.toml を解析する。コードは実行しない。

    各ディレクトリの処理は独立で、1 件の失敗は invalid に入れて続行する。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def scan(self) -> ScanResult:
        result = ScanResult()
        root = self._config.workflows_dir
        if root is None or not root.is_dir():
            return result

        # 走査順を固定し、invalid の内容もファイルシステム順に依存させない
        for path in sorted(root.iterdir()):
            name = path.name
            if not path.is_dir() or name.startswith(("_", ".")):
                continue
            if not _NAME_RE.match(name):
                result.invalid[name] = (
                    "ディレクトリ名が名前規則 ^[a-z][a-z0-9_]*$ に合いません"
                )
                continue
            toml_path = path / "workflow.toml"
            if not toml_path.is_file():
                result.invalid[name] = "workflow.toml がありません"
                continue
            try:
                with toml_path.open("rb") as f:
                    data = tomllib.load(f)
            except tomllib.TOMLDecodeError as exc:
                result.invalid[name] = f"workflow.toml の解析に失敗しました: {exc}"
                continue
            # 名前 = ディレクトリ名（仕様 §11-2）。TOML 側の二重管理を拒否する
            reserved = {"name", "path", "entry_path"} & data.keys()
            if reserved:
                result.invalid[name] = (
                    "workflow.toml: 未知のフィールド "
                    f"{sorted(reserved)}（名前はディレクトリ名から決まります）"
                )
                continue
            entry = data.get("entry", "workflow.py")
            try:
                meta = WorkflowMeta.model_validate(
                    {**data, "name": name, "path": path, "entry_path": path / str(entry)}
                )
            except ValidationError as exc:
                result.invalid[name] = _summarize_validation_error(exc)
                continue
            if not meta.entry_path.is_file():
                result.invalid[name] = f"エントリファイル {meta.entry} がありません"
                continue
            result.metas[name] = meta
        return result
