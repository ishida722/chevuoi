from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from injector import Injector

from chevuoi.application.usecases.gc_usecase import GcUsecase
from chevuoi.application.usecases.issue_card_usecase import IssueCardUsecase
from chevuoi.application.usecases.run_usecase import RunUsecase
from chevuoi.application.usecases.run_workflow_usecase import RunWorkflowUsecase
from chevuoi.application.usecases.select_workflow_usecase import SelectWorkflowUsecase
from chevuoi.application.usecases.workflow_report_usecase import WorkflowReportUsecase
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.task_proposal import TaskProposal
from chevuoi.domain.exceptions import CardIssueError, WorkflowError
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import load_config
from chevuoi.interface.di_modules import AppModule
from chevuoi.interfaces.cli.adhoc_card import AdhocCard

DEFAULT_CONFIG = Path.home() / ".config" / "vuoi" / "config.toml"
# 標準エラー・ログファイル共通の書式。行頭に日付時刻を付けて後から追えるようにする
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_file_logging(log_file: Path) -> None:
    """時刻付きフォーマットでログをファイルにも残す。

    カードごとの処理開始・終了時刻を後から追えるようにするため、
    標準エラーとは別にファイルへ追記する。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vuoi")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="Trello をポーリングして1巡")
    gc = sub.add_parser("gc", help="終端済み worktree の掃除")
    gc.add_argument("--older-than", type=int, default=7, help="経過日数（既定: 7）")
    workflow = sub.add_parser("workflow", help="ユーザー定義ワークフローの管理")
    workflow_sub = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_sub.add_parser("list", help="ワークフローの一覧を表示")
    workflow_run = workflow_sub.add_parser("run", help="ワークフローを名指しで1回実行")
    workflow_run.add_argument("name", help="ワークフロー名（ディレクトリ名）")
    workflow_run.add_argument(
        "message", nargs="?", default="", help="初期メッセージ（省略可）"
    )
    workflow_select = workflow_sub.add_parser(
        "select", help="カードのタイトル・本文からワークフローを選ぶ（ルーターの動作確認）"
    )
    workflow_select.add_argument("title", help="カードのタイトル")
    workflow_select.add_argument("desc", nargs="?", default="", help="カードの本文（省略可）")
    card = sub.add_parser("card", help="カードの操作")
    card_sub = card.add_subparsers(dest="card_command", required=True)
    card_issue = card_sub.add_parser(
        "issue", help="Inbox にカードを 1 枚発行する（発行サービスの動作確認）"
    )
    card_issue.add_argument("tag", help="プロジェクトタグ（タイトル先頭に前置される）")
    card_issue.add_argument("title", help="カードのタイトル（タグを除く）")
    card_issue.add_argument("--body", default="", help="カードの本文")
    card_issue.add_argument(
        "--kind", default="chore", choices=["bug", "chore", "spike", "debt"], help="種別"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    # httpx の INFO ログはリクエスト URL（key/token の認証クエリ）を出すため抑止する
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = parse_args(argv)
    config = load_config(args.config)
    setup_file_logging(config.log_file)
    injector = Injector([AppModule(config)])
    match args.command:
        case "run":
            injector.get(RunUsecase).execute()
        case "gc":
            injector.get(GcUsecase).execute(older_than_days=args.older_than)
        case "workflow":
            match args.workflow_command:
                case "list":
                    print(injector.get(WorkflowReportUsecase).execute())
                case "run":
                    try:
                        result = injector.get(RunWorkflowUsecase).execute(
                            args.name, args.message
                        )
                    except WorkflowError as e:
                        print(str(e), file=sys.stderr)
                        return 1
                    if result.output:
                        print(result.output)
                    else:
                        # messages を増やさないワークフローは state が成果物
                        extra = {
                            k: v for k, v in result.state.items() if k != "messages"
                        }
                        print(extra if extra else "(出力なし)")
                    if result.proposals:
                        # プロジェクトが無いので起票はしない。申告内容の確認用に表示する
                        print(f"\n申告された追加タスク ({len(result.proposals)} 件):")
                        for p in result.proposals:
                            evidence = f" ({', '.join(p.evidence)})" if p.evidence else ""
                            print(f"- [{p.kind}] {p.title}{evidence}")
                case "select":
                    meta, decision = injector.get(SelectWorkflowUsecase).execute(
                        AdhocCard(args.title, args.desc)
                    )
                    chosen = meta.name if meta else "（棄権 → needs_human）"
                    print(f"選択: {chosen}")
                    print(f"確信度: {decision.confidence}")
                    print(f"理由: {decision.reason}")
                    return 0 if meta else 2
        case "card":
            match args.card_command:
                case "issue":
                    tag = args.tag.strip()
                    if not tag or " " in tag:
                        print("tag は空白を含まない 1 語で指定してください", file=sys.stderr)
                        return 1
                    project = Project(tag=ProjectTag(value=tag), repo_path=Path("."))
                    proposal = TaskProposal(title=args.title, body=args.body, kind=args.kind)
                    try:
                        issued = injector.get(IssueCardUsecase).execute(proposal, project)
                    except CardIssueError as e:
                        print(str(e), file=sys.stderr)
                        return 1
                    print(f"{'発行' if issued.created else '既存'}: {issued.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
