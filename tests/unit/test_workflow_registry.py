"""WorkflowRegistry のテスト（フェイクの Scanner / Loader を注入）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.application.usecases.workflow_report_usecase import WorkflowReportUsecase
from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.exceptions import WorkflowError, WorkflowNotFound
from chevuoi.domain.ports.workflow_loader import (
    LoadedWorkflow,
    LoadFailure,
    WorkflowLoader,
)
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner


def make_meta(name: str, **kwargs) -> WorkflowMeta:
    base = {
        "name": name,
        "path": Path(f"/tmp/{name}"),
        "entry_path": Path(f"/tmp/{name}/workflow.py"),
        "api_version": 1,
        "summary": f"{name} の説明",
    }
    return WorkflowMeta.model_validate({**base, **kwargs})


class FakeScanner(WorkflowScanner):
    def __init__(self, result: ScanResult) -> None:
        self.result = result
        self.scan_count = 0

    def scan(self) -> ScanResult:
        self.scan_count += 1
        return self.result


class FakeLoader(WorkflowLoader):
    def __init__(self, fail: set[str] = frozenset()) -> None:
        self.fail = fail
        self.load_count = 0

    def load(self, meta: WorkflowMeta) -> LoadedWorkflow | LoadFailure:
        self.load_count += 1
        if meta.name in self.fail:
            return LoadFailure(name=meta.name, traceback="Traceback: boom")
        return LoadedWorkflow(name=meta.name, graph=object())


def make_registry(result: ScanResult, loader: FakeLoader | None = None):
    loader = loader or FakeLoader()
    registry = WorkflowRegistry(FakeScanner(result), loader)
    registry.scan()
    return registry, loader


def test_lazy_load_and_cache():
    registry, loader = make_registry(ScanResult(metas={"a": make_meta("a")}))
    assert loader.load_count == 0  # scan では load されない
    first = registry.get("a")
    second = registry.get("a")
    assert first is second
    assert loader.load_count == 1


def test_load_failure_raises_and_is_not_cached():
    loader = FakeLoader(fail={"a"})
    registry, _ = make_registry(ScanResult(metas={"a": make_meta("a")}), loader)
    with pytest.raises(WorkflowError, match="boom"):
        registry.get("a")
    loader.fail = set()  # 修正後の再試行は成功する
    assert registry.get("a").name == "a"
    assert loader.load_count == 2


def test_get_unknown_or_disabled_raises_not_found():
    registry, _ = make_registry(
        ScanResult(metas={"off": make_meta("off", enabled=False)})
    )
    with pytest.raises(WorkflowNotFound):
        registry.get("missing")
    with pytest.raises(WorkflowNotFound):
        registry.get("off")


def test_env_var_overrides_enabled(monkeypatch):
    result = ScanResult(
        metas={"a": make_meta("a", enabled=False), "b": make_meta("b")}
    )
    registry, _ = make_registry(result)
    assert [m.name for m in registry.list()] == ["b"]
    monkeypatch.setenv("VUOI_WORKFLOWS", " a ,")
    assert [m.name for m in registry.list()] == ["a"]
    monkeypatch.setenv("VUOI_WORKFLOWS", "")
    assert registry.list() == []


def test_scan_applies_intent_conflict_check():
    result = ScanResult(
        metas={"a": make_meta("a", intents=["x"]), "b": make_meta("b", intents=["x"])}
    )
    registry, _ = make_registry(result)
    assert registry.list() == []
    with pytest.raises(WorkflowNotFound):
        registry.get("a")


def test_report_format():
    result = ScanResult(
        metas={
            "research": make_meta(
                "research",
                version="0.2.0",
                tags=["web", "research"],
                intents=["research.web"],
                summary="ウェブを検索して調査レポートを作成する",
            ),
            "experiment": make_meta(
                "experiment", enabled=False, priority=10, tags=["wip"]
            ),
        },
        invalid={"broken_flow": "workflow.toml: 未知のフィールド ['when_to_used']"},
    )
    registry, _ = make_registry(result)
    report = WorkflowReportUsecase(registry).execute()
    assert report == (
        "✓ ワークフロー 2 件\n"
        "● research         v0.2.0   p50   #research #web\n"
        "    ウェブを検索して調査レポートを作成する\n"
        "    intents: research.web\n"
        "○ experiment       v0.0.0   p10   #wip\n"
        "    experiment の説明\n"
        "\n"
        "✗ 1 件が読み込めません\n"
        "✗ broken_flow      workflow.toml: 未知のフィールド ['when_to_used']"
    )
