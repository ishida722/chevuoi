from __future__ import annotations

import sys
from typing import Annotated

import typer

from chevuoi.application.services.project_resolver import ProjectResolver
from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.domain.entities.task_proposal import ProposalKind, TaskProposal
from chevuoi.domain.exceptions import CardIssueError
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.interfaces.cli.context import get_injector

app = typer.Typer(help="カードの操作", no_args_is_help=True)


@app.command("issue", help="Inbox にカードを 1 枚発行する（発行サービスの動作確認）")
def issue_card(
    ctx: typer.Context,
    tag: Annotated[str, typer.Argument(help="プロジェクトタグ（タイトル先頭に前置される）")],
    title: Annotated[str, typer.Argument(help="カードのタイトル（タグを除く）")],
    body: Annotated[str, typer.Option(help="カードの本文")] = "",
    kind: Annotated[ProposalKind, typer.Option(help="種別")] = "chore",
) -> None:
    tag = tag.strip()
    # 全角スペースなども区切りとみなす（ProjectTag.from_title と同じ空白判定に揃える）。
    # ここを半角スペースだけで見ると、対応表で引けないタグを黙って作ってしまう
    if len(tag.split()) != 1:
        print("tag は空白を含まない 1 語で指定してください", file=sys.stderr)
        raise typer.Exit(code=1)
    injector = get_injector(ctx)
    # 対象プロジェクトはタグを設定の対応表で引いて決める。カレントディレクトリで
    # 代用すると、対象プロジェクトではなく実行場所のリポジトリを対象として渡すことになる
    project = injector.get(ProjectResolver).resolve(ProjectTag(value=tag))
    if project.is_null:
        # 設定漏れでも起票そのものは止めない（対象リポジトリは特定できないまま発行する）
        print(
            f"警告: タグ「{tag}」に対応するプロジェクトが設定にありません。"
            "対象リポジトリを特定しないまま起票します",
            file=sys.stderr,
        )
    proposal = TaskProposal(title=title, body=body, kind=kind)
    try:
        issued = injector.get(IssueCardUsecase).execute(proposal, project)
    except CardIssueError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(code=1)
    print(f"{'発行' if issued.created else '既存'}: {issued.url}")
