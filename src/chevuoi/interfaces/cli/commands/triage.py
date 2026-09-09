from __future__ import annotations

import sys
from typing import Annotated

import typer

from chevuoi.application.usecases.triage_usecase import TriageUsecase
from chevuoi.domain.exceptions import ChevuoiError
from chevuoi.infrastructure.config.settings import AppConfig
from chevuoi.interfaces.cli.context import get_injector


def triage(
    ctx: typer.Context,
    apply: Annotated[
        bool, typer.Option("--apply", help="計画を適用する（既定は dry-run）")
    ] = False,
    project: Annotated[
        str | None, typer.Option("--project", help="対象プロジェクトのタグで絞る")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", help="読むカード数の上限")
    ] = None,
) -> None:
    """Inbox のカード集合を整理する（重複の集約・解決済みの畳み込み・ラベル付け）。

    既定は dry-run で、計画を表示するだけ。破壊的操作は --apply の明示が要る。
    """
    injector = get_injector(ctx)
    config = injector.get(AppConfig)
    if not config.triage.enabled:
        print("トリアージは設定で無効です（[triage] enabled = false）", file=sys.stderr)
        raise typer.Exit(code=0)
    apply = apply or config.triage.apply
    try:
        report = injector.get(TriageUsecase).execute(apply=apply, project=project, limit=limit)
    except ChevuoiError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(code=1)
    print(report.to_text(dry_run=not apply))
    if report.failed:
        raise typer.Exit(code=1)
