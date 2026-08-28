from __future__ import annotations

import os
from collections.abc import Iterable

from injector import inject

from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.exceptions import WorkflowError, WorkflowNotFound
from chevuoi.domain.ports.workflow_loader import (
    LoadedWorkflow,
    LoadFailure,
    WorkflowLoader,
)
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner
from chevuoi.domain.services import workflow_selection


class WorkflowRegistry:
    """ワークフローの一覧・選択・遅延ロード + キャッシュ（仕様 §7）。

    選択ロジックはドメインの純粋関数へ委譲する。キャッシュを持つため
    DI では singleton でバインドすること。
    """

    @inject
    def __init__(self, scanner: WorkflowScanner, loader: WorkflowLoader) -> None:
        self._scanner = scanner
        self._loader = loader
        self._result: ScanResult = ScanResult()
        self._cache: dict[str, LoadedWorkflow] = {}

    def scan(self) -> ScanResult:
        """スキャン + intent 衝突検証。結果を保持する。起動時に 1 回呼ぶ。"""
        self._result = workflow_selection.check_intent_conflicts(self._scanner.scan())
        return self._result

    def is_enabled(self, meta: WorkflowMeta) -> bool:
        # 環境変数 VUOI_WORKFLOWS が TOML の enabled より強い（仕様 §8）
        env = os.environ.get("VUOI_WORKFLOWS")
        if env is not None:
            return meta.name in {s.strip() for s in env.split(",") if s.strip()}
        return meta.enabled

    # --- 選択（ドメイン関数への委譲） ---

    def list(self, include_disabled: bool = False) -> list[WorkflowMeta]:
        return workflow_selection.list_metas(
            self._result, enabled=self.is_enabled, include_disabled=include_disabled
        )

    def by_intent(self, intent: str) -> WorkflowMeta:
        return workflow_selection.by_intent(
            self._result, intent, enabled=self.is_enabled
        )

    def by_tags(
        self,
        *,
        require: Iterable[str] = (),
        exclude: Iterable[str] = (),
        capabilities: dict[str, object] | None = None,
    ) -> list[WorkflowMeta]:
        return workflow_selection.by_tags(
            self._result,
            require=require,
            exclude=exclude,
            capabilities=capabilities,
            enabled=self.is_enabled,
        )

    def resolve_one(
        self,
        *,
        intent: str | None = None,
        require: Iterable[str] = (),
        exclude: Iterable[str] = (),
        capabilities: dict[str, object] | None = None,
    ) -> WorkflowMeta:
        return workflow_selection.resolve_one(
            self._result,
            enabled=self.is_enabled,
            intent=intent,
            require=require,
            exclude=exclude,
            capabilities=capabilities,
        )

    # --- 遅延ロード + キャッシュ ---

    def get(self, name: str) -> LoadedWorkflow:
        """初回のみ loader.load()。成功はキャッシュ、失敗はキャッシュしない。

        失敗はローダ側の sys.modules purge により再試行可能なため。
        """
        if name in self._cache:
            return self._cache[name]
        meta = self._result.metas.get(name)
        if meta is None or not self.is_enabled(meta):
            raise WorkflowNotFound(f"ワークフロー '{name}' は存在しないか無効です")
        loaded = self._loader.load(meta)
        if isinstance(loaded, LoadFailure):
            raise WorkflowError(
                f"ワークフロー '{name}' のロードに失敗しました:\n{loaded.traceback}"
            )
        self._cache[name] = loaded
        return loaded
