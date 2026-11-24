from __future__ import annotations

import logging
import secrets
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from injector import inject
from pydantic import BaseModel, ValidationError

from chevuoi.application.usecases.exec_usecase import ExecOutcome
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.exec_run import ExecRun
from chevuoi.domain.exceptions import RunConflict, RunError
from chevuoi.domain.ports.run_launcher import RunLauncher
from chevuoi.domain.ports.run_store import RunStore
from chevuoi.domain.ports.workspace_inspector import WorkspaceInspector

logger = logging.getLogger(__name__)

RunState = Literal["running", "done", "blocked", "error", "cancelled"]

# 実行中に返すログの行数。進み具合が分かれば足りるので短くする
RUNNING_LOG_LINES = 15
# 異常終了したときに返すログの行数。原因を追えるよう多めにする
FAILED_LOG_LINES = 60


class WorkflowInfo(BaseModel):
    """start に渡せるワークフロー。"""

    name: str
    summary: str
    when_to_use: str = ""


class RunStatus(BaseModel):
    """実行の状態。呼び出し元（対話中の Claude）が読む。

    state が running の間は log_tail で進み具合を示し、終了後は result に
    vuoi exec の結果を入れる。
    """

    run_id: str
    state: RunState
    workflow: str
    workdir: str
    started_at: datetime
    elapsed_sec: int | None = None  # 実行中だけ入れる（終了後は意味がないため）
    changed_before: list[str] = []  # 開始時点の未コミットの変更（結果の changed_files と比べる）
    result: ExecOutcome | None = None
    error: str = ""  # 結果を残さずに終わったときの説明
    log_tail: str = ""
    brief_path: str = ""
    result_path: str = ""
    log_path: str = ""


class ExecRunUsecase:
    """vuoi exec をバックグラウンドで起動し、状態を問い合わせる（MCP の start / status）。

    ワークフローの実行時間は長い（dev は十数分）ため、ツール呼び出しで終了を待たず、
    start で run_id を返して status で状態を見る。実行そのものは vuoi exec の
    別プロセスに任せ、ここでは記録と監視だけを行う。
    """

    @inject
    def __init__(
        self,
        registry: WorkflowRegistry,
        inspector: WorkspaceInspector,
        store: RunStore,
        launcher: RunLauncher,
    ) -> None:
        self._registry = registry
        self._inspector = inspector
        self._store = store
        self._launcher = launcher

    def workflows(self) -> list[WorkflowInfo]:
        self._registry.scan()
        return [
            WorkflowInfo(name=m.name, summary=m.summary, when_to_use=m.when_to_use)
            for m in self._registry.list()
        ]

    def start(
        self,
        workflow: str,
        brief: str,
        *,
        workdir: Path,
        test_commands: Sequence[str] = (),
    ) -> RunStatus:
        """検証してから起動する。検証で失敗したら何も起動せずに例外を送出する。"""
        if not brief.strip():
            # 空の指示で長時間のワークフローを動かさない
            raise RunError("ブリーフが空です")
        # 存在しないワークフロー・壊れたワークフローは、起動する前にここで弾く
        self._registry.scan()
        self._registry.get(workflow)
        workspace = self._inspector.locate(workdir)
        running = [
            r for r in self._store.list()
            if r.workdir == workspace.root and self._state_of(r) == "running"
        ]
        if running:
            # 同じ作業ツリーで 2 つ動かすと、変更が混ざって結果を読み分けられない
            raise RunConflict(
                f"この作業ツリーでは実行中のランがあります: {running[0].run_id}"
                f"（{running[0].workflow}）。終了を待つか cancel してください"
            )
        run = ExecRun(
            run_id=_new_run_id(),
            workflow=workflow,
            workdir=workspace.root,
            started_at=_now(),
            test_commands=list(test_commands),
            changed_before=self._inspector.changed_files(workspace.root),
        )
        files = self._store.create(run, brief)
        pid = self._launcher.launch(
            workflow, files, cwd=workspace.root, test_commands=test_commands
        )
        run = run.model_copy(update={"pid": pid})
        self._store.save(run)
        logger.info("バックグラウンド実行開始: %s %s (pid=%s)", run.run_id, workflow, pid)
        return self._status_of(run)

    def status(self, run_id: str) -> RunStatus:
        return self._status_of(self._store.get(run_id))

    def list(self, limit: int = 10) -> list[RunStatus]:
        """新しい順に limit 件。"""
        runs = sorted(self._store.list(), key=lambda r: r.started_at, reverse=True)
        return [self._status_of(r, with_log=False) for r in runs[:limit]]

    def cancel(self, run_id: str) -> RunStatus:
        """実行中なら止めて取り消しを記録する。終了済みなら何もせず状態を返す。"""
        run = self._store.get(run_id)
        if self._state_of(run) != "running":
            return self._status_of(run)
        if run.pid is not None:
            self._launcher.terminate(run.pid)
        run = run.model_copy(update={"cancelled_at": _now()})
        self._store.save(run)
        logger.info("バックグラウンド実行を取り消し: %s", run_id)
        return self._status_of(run)

    # --- 状態の判定 ---

    def _state_of(self, run: ExecRun) -> RunState:
        return self._resolve(run)[0]

    def _resolve(self, run: ExecRun) -> tuple[RunState, ExecOutcome | None, str]:
        """状態・結果・異常終了の説明を求める。

        生存確認を結果の読み込みより先に行う。逆順だと「結果を読んだ時点では未作成、
        生存確認の時点では終了済み」となり、正常終了を異常終了と取り違える。
        """
        alive = run.pid is not None and self._launcher.is_running(run.pid)
        raw = self._store.read_result(run.run_id)
        if raw is not None:
            try:
                outcome = ExecOutcome.model_validate_json(raw)
            except ValidationError as e:
                if alive:
                    return "running", None, ""  # 書き込み途中。次の問い合わせで読める
                return "error", None, f"結果の JSON を読めません: {e}"
            return outcome.status, outcome, ""
        if run.cancelled_at is not None:
            return "cancelled", None, ""
        if alive:
            return "running", None, ""
        if run.pid is None:
            return "error", None, "プロセスを起動する前に中断しました"
        return "error", None, "vuoi exec が結果を残さずに終了しました（log_tail を確認してください）"

    def _status_of(self, run: ExecRun, *, with_log: bool = True) -> RunStatus:
        state, outcome, error = self._resolve(run)
        files = self._store.files(run.run_id)
        log_tail = ""
        if with_log and state == "running":
            log_tail = self._store.log_tail(run.run_id, RUNNING_LOG_LINES)
        elif with_log and state == "error" and outcome is None:
            log_tail = self._store.log_tail(run.run_id, FAILED_LOG_LINES)
        return RunStatus(
            run_id=run.run_id,
            state=state,
            workflow=run.workflow,
            workdir=str(run.workdir),
            started_at=run.started_at,
            elapsed_sec=(
                int((_now() - run.started_at).total_seconds()) if state == "running" else None
            ),
            changed_before=run.changed_before,
            result=outcome,
            error=error,
            log_tail=log_tail,
            brief_path=str(files.brief),
            result_path=str(files.result),
            log_path=str(files.log),
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_run_id() -> str:
    """時刻で並び、同じ秒に起動しても衝突しない ID。"""
    return f"{_now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
