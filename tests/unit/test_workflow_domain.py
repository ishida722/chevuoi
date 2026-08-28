"""ワークフロー機構のドメイン層テスト（外部依存なし）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.exceptions import AmbiguousSelection, WorkflowNotFound
from chevuoi.domain.services import workflow_selection as sel


def make_meta(name: str, **kwargs) -> WorkflowMeta:
    base = {
        "name": name,
        "path": Path(f"/tmp/{name}"),
        "entry_path": Path(f"/tmp/{name}/workflow.py"),
        "api_version": 1,
        "summary": f"{name} の説明",
    }
    return WorkflowMeta.model_validate({**base, **kwargs})


ALL = lambda m: True  # noqa: E731


class TestWorkflowMetaValidation:
    def test_minimal_valid(self):
        meta = make_meta("hello")
        assert meta.priority == 50
        assert meta.entry == "workflow.py"

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError, match="when_to_used"):
            make_meta("hello", when_to_used="typo")

    def test_api_version_mismatch(self):
        with pytest.raises(ValidationError):
            make_meta("hello", api_version=2)

    def test_empty_summary(self):
        with pytest.raises(ValidationError):
            make_meta("hello", summary="")

    def test_bad_tag_pattern(self):
        with pytest.raises(ValidationError):
            make_meta("hello", tags=["Bad Tag"])

    def test_bad_intent_pattern(self):
        with pytest.raises(ValidationError):
            make_meta("hello", intents=["Bad/Intent"])

    def test_unknown_capability_key(self):
        with pytest.raises(ValidationError):
            make_meta("hello", capabilities={"gpu": True})

    def test_bad_directory_name(self):
        with pytest.raises(ValidationError):
            make_meta("1bad_name")

    def test_custom_entry(self):
        meta = make_meta("hello", entry="main.py")
        assert meta.entry == "main.py"


class TestSelection:
    def test_sort_order_is_total(self):
        result = ScanResult(
            metas={
                "b": make_meta("b", priority=50),
                "a": make_meta("a", priority=50),
                "c": make_meta("c", priority=90),
            }
        )
        names = [m.name for m in sel.list_metas(result, enabled=ALL)]
        assert names == ["c", "a", "b"]

    def test_intent_conflict_invalidates_all(self):
        result = ScanResult(
            metas={
                "a": make_meta("a", intents=["x"]),
                "b": make_meta("b", intents=["x"]),
                "c": make_meta("c", intents=["y"]),
            }
        )
        checked = sel.check_intent_conflicts(result)
        assert set(checked.metas) == {"c"}
        assert set(checked.invalid) == {"a", "b"}
        assert "x" in checked.invalid["a"]

    def test_by_intent_found_and_not_found(self):
        result = ScanResult(metas={"a": make_meta("a", intents=["go"])})
        assert sel.by_intent(result, "go", enabled=ALL).name == "a"
        with pytest.raises(WorkflowNotFound):
            sel.by_intent(result, "nope", enabled=ALL)

    def test_by_tags_require_exclude_capabilities(self):
        result = ScanResult(
            metas={
                "a": make_meta("a", tags=["web"], capabilities={"requires_network": True}),
                "b": make_meta("b", tags=["web", "wip"]),
            }
        )
        assert [m.name for m in sel.by_tags(result, require={"web"}, enabled=ALL)] == ["a", "b"]
        assert [m.name for m in sel.by_tags(result, require={"web"}, exclude={"wip"}, enabled=ALL)] == ["a"]
        assert [
            m.name
            for m in sel.by_tags(result, capabilities={"requires_network": True}, enabled=ALL)
        ] == ["a"]

    def test_resolve_one_priority_tiebreak(self):
        result = ScanResult(
            metas={
                "a": make_meta("a", tags=["t"], priority=90),
                "b": make_meta("b", tags=["t"], priority=50),
            }
        )
        assert sel.resolve_one(result, enabled=ALL, require={"t"}).name == "a"

    def test_resolve_one_ambiguous(self):
        result = ScanResult(
            metas={
                "a": make_meta("a", tags=["t"]),
                "b": make_meta("b", tags=["t"]),
            }
        )
        with pytest.raises(AmbiguousSelection):
            sel.resolve_one(result, enabled=ALL, require={"t"})

    def test_resolve_one_not_found(self):
        with pytest.raises(WorkflowNotFound):
            sel.resolve_one(ScanResult(), enabled=ALL, require={"t"})

    def test_disabled_excluded_from_selection(self):
        result = ScanResult(metas={"a": make_meta("a", enabled=False, intents=["go"])})
        enabled = lambda m: m.enabled  # noqa: E731
        assert sel.list_metas(result, enabled=enabled) == []
        assert len(sel.list_metas(result, enabled=enabled, include_disabled=True)) == 1
        with pytest.raises(WorkflowNotFound):
            sel.by_intent(result, "go", enabled=enabled)
