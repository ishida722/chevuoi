from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from chevuoi.application.usecases.exec_run_usecase import (
    ExecRunUsecase,
    RunStatus,
    WorkflowInfo,
)
from chevuoi.domain.exceptions import ChevuoiError

# status の wait_sec の上限。クライアント側のツール呼び出しのタイムアウトより短くする
MAX_WAIT_SEC = 300
# 終了を待つ間の確認の間隔
POLL_INTERVAL_SEC = 5.0

INSTRUCTIONS = """\
vuoi のワークフロー（dev / research / design / doc など）を、指定した作業ツリーで
バックグラウンド実行する。実行には十数分以上かかることがあるため、start で起動して
run_id を受け取り、status で状態を確認する。

手順:
1. workflows で使えるワークフローを確認し、名前を 1 つ選ぶ
2. ブリーフ（目的・決定事項・作業内容・受け入れ条件・触らない範囲・参考）を書き、
   start に渡す。ワークフロー側の Claude は対話の経緯を知らないので、単体で読めるように書く
3. status を wait_sec 付きで呼ぶと、終了するか wait_sec が過ぎるまで待ってから返る。
   state が running の間は繰り返す
4. 終了したら result（summary / blocked / changed_files / proposals）を読んで報告する。
   changed_before は開始時点の未コミットの変更なので、changed_files と比べて
   ワークフローが変えたファイルを見分ける

注意:
- 実行中は、その作業ツリーのファイルを編集しない（ワークフローの変更と混ざる）
- vuoi はコミット・プッシュ・PR 作成を行わない。変更は作業ツリーに未コミットで残る
"""

T = TypeVar("T")


def build_server(usecase: ExecRunUsecase) -> MCPServer:
    server = MCPServer("vuoi", instructions=INSTRUCTIONS)

    @server.tool(description="start に渡せるワークフローの一覧（名前・概要・使いどころ）")
    def workflows() -> list[WorkflowInfo]:
        return _call(usecase.workflows)

    @server.tool(
        description=(
            "作業ツリーでワークフローをバックグラウンド実行し、すぐに run_id を返す。"
            "ワークフローの存在・作業ツリー・ブリーフはここで検証し、失敗すれば何も起動しない。"
            "同じ作業ツリーで実行中のランがあるときは起動しない"
        )
    )
    def start(
        workflow: Annotated[str, Field(description="ワークフロー名（workflows の name）")],
        brief: Annotated[str, Field(description="ワークフローに渡す方針（Markdown）")],
        workdir: Annotated[
            str, Field(description="実行する作業ツリー（git リポジトリ内の絶対パス）")
        ],
        test_commands: Annotated[
            list[str] | None,
            Field(description="テストゲートのコマンド。省略すると設定の test_commands を使う"),
        ] = None,
    ) -> RunStatus:
        return _call(
            lambda: usecase.start(
                workflow, brief, workdir=Path(workdir), test_commands=test_commands or ()
            )
        )

    @server.tool(
        description=(
            "ランの状態を返す。wait_sec を指定すると、終了するか wait_sec 秒が過ぎるまで"
            f"待ってから返す（上限 {MAX_WAIT_SEC} 秒）。running の間は log_tail に"
            "ログの末尾、終了後は result に結果が入る"
        )
    )
    async def status(
        run_id: str,
        wait_sec: Annotated[int, Field(ge=0, le=MAX_WAIT_SEC)] = 0,
    ) -> RunStatus:
        return await wait_for_finish(usecase, run_id, wait_sec)

    @server.tool(description="最近のランの一覧（新しい順）。run_id を忘れたときに使う")
    def runs(limit: Annotated[int, Field(ge=1, le=50)] = 10) -> list[RunStatus]:
        return _call(lambda: usecase.list(limit))

    @server.tool(description="実行中のランを止める。作業ツリーの途中までの変更は残る")
    def cancel(run_id: str) -> RunStatus:
        return _call(lambda: usecase.cancel(run_id))

    return server


async def wait_for_finish(
    usecase: ExecRunUsecase,
    run_id: str,
    wait_sec: float,
    *,
    interval: float = POLL_INTERVAL_SEC,
) -> RunStatus:
    """終了するか wait_sec が過ぎるまで待ち、最後の状態を返す。"""
    deadline = time.monotonic() + wait_sec
    while True:
        current = _call(lambda: usecase.status(run_id))
        remaining = deadline - time.monotonic()
        if current.state != "running" or remaining <= 0:
            return current
        await asyncio.sleep(min(interval, remaining))


def _call(fn: Callable[[], T]) -> T:
    """ドメインの例外を、モデルに読ませるエラー（ToolError）に変える。

    ToolError 以外の例外は MCP SDK がメッセージを伏せて返すため、原因が伝わらない。
    """
    try:
        return fn()
    except ChevuoiError as e:
        raise ToolError(str(e)) from e
