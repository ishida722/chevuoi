from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.domain.entities.project import Project
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
    if not tag or " " in tag:
        print("tag は空白を含まない 1 語で指定してください", file=sys.stderr)
        raise typer.Exit(code=1)
    project = Project(tag=ProjectTag(value=tag), repo_path=Path("."))
    proposal = TaskProposal(title=title, body=body, kind=kind)
    try:
        issued = get_injector(ctx).get(IssueCardUsecase).execute(proposal, project)
    except CardIssueError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(code=1)
    print(f"{'発行' if issued.created else '既存'}: {issued.url}")
