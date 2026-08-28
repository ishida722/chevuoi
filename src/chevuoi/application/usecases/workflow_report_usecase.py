from __future__ import annotations

from injector import inject

from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.workflow_meta import WorkflowMeta


class WorkflowReportUsecase:
    """スキャン結果を仕様 §9 の書式で整形する。コードは一切実行しない。"""

    @inject
    def __init__(self, registry: WorkflowRegistry) -> None:
        self._registry = registry

    def execute(self) -> str:
        result = self._registry.scan()
        metas = self._registry.list(include_disabled=True)
        lines: list[str] = [f"✓ ワークフロー {len(metas)} 件"]
        for meta in metas:
            lines.extend(self._format_meta(meta))
        if result.invalid:
            lines.append("")
            lines.append(f"✗ {len(result.invalid)} 件が読み込めません")
            for name in sorted(result.invalid):
                lines.append(f"✗ {name:<16} {result.invalid[name]}")
        return "\n".join(lines)

    def _format_meta(self, meta: WorkflowMeta) -> list[str]:
        mark = "●" if self._registry.is_enabled(meta) else "○"
        tags = " ".join(f"#{t}" for t in sorted(meta.tags))
        lines = [
            f"{mark} {meta.name:<16} v{meta.version:<7} p{meta.priority:<4} {tags}".rstrip(),
            f"    {meta.summary}",
        ]
        if meta.intents:
            lines.append(f"    intents: {', '.join(meta.intents)}")
        return lines
