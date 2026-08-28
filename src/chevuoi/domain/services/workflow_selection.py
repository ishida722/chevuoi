"""ワークフロー選択の純粋関数群。

外部依存なし。すべての一覧・選択は (-priority, name) の全順序でソートし、
ファイルシステムの走査順に依存しない（仕様 §7）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.exceptions import AmbiguousSelection, WorkflowNotFound


def sort_key(meta: WorkflowMeta) -> tuple[int, str]:
    return (-meta.priority, meta.name)


def check_intent_conflicts(result: ScanResult) -> ScanResult:
    """intent 重複を検出し、関係する全ワークフローを invalid へ移した新しい ScanResult を返す。

    片方を勝たせると走査順依存になるため、全員を無効化してユーザーに修正させる。
    検出順は name のソート順で決定的。
    """
    owners: dict[str, list[str]] = {}
    for name in sorted(result.metas):
        for intent in result.metas[name].intents:
            owners.setdefault(intent, []).append(name)

    metas = dict(result.metas)
    invalid = dict(result.invalid)
    for intent in sorted(owners):
        names = owners[intent]
        if len(names) < 2:
            continue
        for name in names:
            metas.pop(name, None)
            reason = f"intent '{intent}' が重複しています: {', '.join(names)}"
            if name in invalid:
                invalid[name] += f"; {reason}"
            else:
                invalid[name] = reason
    return ScanResult(metas=metas, invalid=invalid)


def list_metas(
    result: ScanResult,
    *,
    enabled: Callable[[WorkflowMeta], bool],
    include_disabled: bool = False,
) -> list[WorkflowMeta]:
    metas = [
        m
        for m in result.metas.values()
        if include_disabled or enabled(m)
    ]
    return sorted(metas, key=sort_key)


def by_intent(
    result: ScanResult,
    intent: str,
    *,
    enabled: Callable[[WorkflowMeta], bool],
) -> WorkflowMeta:
    """完全一致で 0 or 1 件。無ければ WorkflowNotFound。

    intent は一意なので 2 件以上はあり得ない（check_intent_conflicts 通過後が前提）。
    """
    for meta in list_metas(result, enabled=enabled):
        if intent in meta.intents:
            return meta
    raise WorkflowNotFound(f"intent '{intent}' に一致するワークフローがありません")


def by_tags(
    result: ScanResult,
    *,
    require: Iterable[str] = (),
    exclude: Iterable[str] = (),
    capabilities: dict[str, object] | None = None,
    enabled: Callable[[WorkflowMeta], bool],
) -> list[WorkflowMeta]:
    require_set = frozenset(require)
    exclude_set = frozenset(exclude)
    matched = []
    for meta in list_metas(result, enabled=enabled):
        tags = set(meta.tags)
        if not require_set <= tags or tags & exclude_set:
            continue
        if capabilities is not None:
            caps = meta.capabilities.model_dump()
            if any(caps.get(k) != v for k, v in capabilities.items()):
                continue
        matched.append(meta)
    return matched


def resolve_one(
    result: ScanResult,
    *,
    enabled: Callable[[WorkflowMeta], bool],
    intent: str | None = None,
    require: Iterable[str] = (),
    exclude: Iterable[str] = (),
    capabilities: dict[str, object] | None = None,
) -> WorkflowMeta:
    """候補 0 件なら WorkflowNotFound、最高 priority 同点で複数なら AmbiguousSelection。"""
    if intent is not None:
        return by_intent(result, intent, enabled=enabled)
    candidates = by_tags(
        result,
        require=require,
        exclude=exclude,
        capabilities=capabilities,
        enabled=enabled,
    )
    if not candidates:
        raise WorkflowNotFound("条件に合うワークフローがありません")
    if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
        names = [m.name for m in candidates if m.priority == candidates[0].priority]
        raise AmbiguousSelection(
            f"候補が 1 件に定まりません（priority 同点）: {', '.join(names)}"
        )
    return candidates[0]
