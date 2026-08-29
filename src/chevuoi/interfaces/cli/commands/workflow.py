from __future__ import annotations

import sys
from typing import Annotated

import typer

from chevuoi.application.usecases.run_workflow_usecase import RunWorkflowUsecase
from chevuoi.application.usecases.select_workflow_usecase import SelectWorkflowUsecase
from chevuoi.application.usecases.workflow_report_usecase import WorkflowReportUsecase
from chevuoi.domain.exceptions import WorkflowError
from chevuoi.interfaces.cli.adhoc_card import AdhocCard
from chevuoi.interfaces.cli.context import get_injector

app = typer.Typer(help="ユーザー定義ワークフローの管理", no_args_is_help=True)


@app.command("list", help="ワークフローの一覧を表示")
def list_workflows(ctx: typer.Context) -> None:
    print(get_injector(ctx).get(WorkflowReportUsecase).execute())


@app.command("run", help="ワークフローを名指しで1回実行")
def run_workflow(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="ワークフロー名（ディレクトリ名）")],
    message: Annotated[str, typer.Argument(help="初期メッセージ（省略可）")] = "",
) -> None:
    try:
        result = get_injector(ctx).get(RunWorkflowUsecase).execute(name, message)
    except WorkflowError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(code=1)
    if result.output:
        print(result.output)
    else:
        # messages を増やさないワークフローは state が成果物
        extra = {k: v for k, v in result.state.items() if k != "messages"}
        print(extra if extra else "(出力なし)")
    if result.proposals:
        # プロジェクトが無いので起票はしない。申告内容の確認用に表示する
        print(f"\n申告された追加タスク ({len(result.proposals)} 件):")
        for p in result.proposals:
            evidence = f" ({', '.join(p.evidence)})" if p.evidence else ""
            print(f"- [{p.kind}] {p.title}{evidence}")


@app.command(
    "select", help="カードのタイトル・本文からワークフローを選ぶ（ルーターの動作確認）"
)
def select_workflow(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="カードのタイトル")],
    desc: Annotated[str, typer.Argument(help="カードの本文（省略可）")] = "",
) -> None:
    meta, decision = get_injector(ctx).get(SelectWorkflowUsecase).execute(
        AdhocCard(title, desc)
    )
    chosen = meta.name if meta else "（棄権 → needs_human）"
    print(f"選択: {chosen}")
    print(f"確信度: {decision.confidence}")
    print(f"理由: {decision.reason}")
    if not meta:
        raise typer.Exit(code=2)
