"""MCP の start / status（vuoi exec のバックグラウンド実行）の仕様。"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from chevuoi.application.usecases.exec_run_usecase import ExecRunUsecase
from chevuoi.application.usecases.exec_usecase import ExecOutcome
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.exec_run import RunFiles
from chevuoi.domain.exceptions import RunConflict, RunError, RunNotFound, WorkflowNotFound
from chevuoi.domain.ports.run_launcher import RunLauncher
from chevuoi.infrastructure.runs.fs_run_store import FsRunStore
from chevuoi.interfaces.mcp.server import build_server, wait_for_finish
from tests.unit.fakes import make_config
from tests.unit.test_exec import ROOT, FakeInspector, simple_graph
from tests.unit.test_run_workflow_usecase import FakeLoader, FakeScanner, make_meta


class FakeLauncher(RunLauncher):
    """起動を記録し、生存は alive で切り替える。"""

    def __init__(self) -> None:
        self.launched: list[tuple[str, RunFiles, Path, list[str]]] = []
        self.alive: set[int] = set()
        self.terminated: list[int] = []
        self._next_pid = 1000

    def launch(self, workflow, files, *, cwd, test_commands: Sequence[str] = ()) -> int:
        self._next_pid += 1
        self.launched.append((workflow, files, cwd, list(test_commands)))
        self.alive.add(self._next_pid)
        return self._next_pid

    def is_running(self, pid: int) -> bool:
        return pid in self.alive

    def terminate(self, pid: int) -> None:
        self.terminated.append(pid)
        self.alive.discard(pid)


def make_usecase(tmp_path: Path, inspector: FakeInspector | None = None):
    registry = WorkflowRegistry(
        FakeScanner({"dev": make_meta("dev")}), FakeLoader(simple_graph(lambda s: {}))
    )
    store = FsRunStore(make_config(runs_dir=tmp_path / "runs"))
    launcher = FakeLauncher()
    usecase = ExecRunUsecase(registry, inspector or FakeInspector(), store, launcher)
    return usecase, store, launcher


def finish(store: FsRunStore, run_id: str, launcher: FakeLauncher, pid: int, **fields) -> None:
    """vuoi exec が結果を書いて終了した状態にする。"""
    outcome = ExecOutcome(workflow="dev", **{"status": "done", **fields})
    store.files(run_id).result.write_text(outcome.model_dump_json(), encoding="utf-8")
    launcher.alive.discard(pid)


class TestStart:
    def test_launches_in_workspace_root_and_records_run(self, tmp_path):
        inspector = FakeInspector(changed=[" M before.py"])
        usecase, store, launcher = make_usecase(tmp_path, inspector)

        status = usecase.start("dev", "方針です", workdir=ROOT / "sub", test_commands=["pytest"])

        assert status.state == "running"
        assert status.changed_before == [" M before.py"]
        workflow, files, cwd, tests = launcher.launched[0]
        assert (workflow, cwd, tests) == ("dev", ROOT, ["pytest"])
        assert files.brief.read_text(encoding="utf-8") == "方針です"
        # 作業ツリーの外（runs_dir）に置く
        assert files.brief.is_relative_to(tmp_path / "runs")
        run = store.get(status.run_id)
        assert run.pid == 1001
        assert run.workdir == ROOT

    def test_rejects_empty_brief_without_launching(self, tmp_path):
        usecase, _, launcher = make_usecase(tmp_path)
        with pytest.raises(RunError, match="空"):
            usecase.start("dev", "  \n", workdir=ROOT)
        assert launcher.launched == []

    def test_rejects_unknown_workflow_without_launching(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        with pytest.raises(WorkflowNotFound):
            usecase.start("nope", "x", workdir=ROOT)
        assert launcher.launched == []
        assert store.list() == []

    def test_rejects_second_run_in_same_workdir_while_running(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        first = usecase.start("dev", "x", workdir=ROOT)
        with pytest.raises(RunConflict, match=first.run_id):
            usecase.start("dev", "y", workdir=ROOT)
        # 終われば次を起動できる
        finish(store, first.run_id, launcher, 1001)
        assert usecase.start("dev", "y", workdir=ROOT).state == "running"


class TestStatus:
    def test_running_shows_log_tail(self, tmp_path):
        usecase, store, _ = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id
        store.files(run_id).log.write_text("".join(f"line{i}\n" for i in range(30)))

        status = usecase.status(run_id)

        assert status.state == "running"
        assert status.result is None
        assert status.elapsed_sec is not None
        assert status.log_tail.splitlines()[-1] == "line29"
        assert len(status.log_tail.splitlines()) == 15

    @pytest.mark.parametrize("state", ["done", "blocked", "error"])
    def test_finished_returns_result(self, tmp_path, state):
        usecase, store, launcher = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id
        finish(store, run_id, launcher, 1001, status=state, summary="要約", changed_files=["?? a.py"])

        status = usecase.status(run_id)

        assert status.state == state
        assert status.result is not None
        assert status.result.summary == "要約"
        assert status.result.changed_files == ["?? a.py"]
        assert status.elapsed_sec is None

    def test_exited_without_result_is_error_with_log(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id
        store.files(run_id).log.write_text("Traceback ...\nKeyError: x\n")
        launcher.alive.clear()

        status = usecase.status(run_id)

        assert status.state == "error"
        assert "結果を残さずに終了" in status.error
        assert "KeyError" in status.log_tail

    def test_broken_result_while_alive_is_still_running(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id
        store.files(run_id).result.write_text('{"status": "do')
        assert usecase.status(run_id).state == "running"
        launcher.alive.clear()
        status = usecase.status(run_id)
        assert status.state == "error"
        assert "JSON" in status.error

    def test_unknown_run_id(self, tmp_path):
        usecase, _, _ = make_usecase(tmp_path)
        with pytest.raises(RunNotFound):
            usecase.status("20260101-000000-abcdef")

    @pytest.mark.parametrize("run_id", ["../x", "a/b", ".hidden", ""])
    def test_run_id_cannot_escape_runs_dir(self, tmp_path, run_id):
        usecase, _, _ = make_usecase(tmp_path)
        with pytest.raises(RunNotFound):
            usecase.status(run_id)


class TestCancelAndList:
    def test_cancel_terminates_and_records(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id

        status = usecase.cancel(run_id)

        assert launcher.terminated == [1001]
        assert status.state == "cancelled"
        assert store.get(run_id).cancelled_at is not None

    def test_cancel_finished_run_does_nothing(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id
        finish(store, run_id, launcher, 1001)

        assert usecase.cancel(run_id).state == "done"
        assert launcher.terminated == []

    def test_list_newest_first(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        ids = []
        for i in range(3):
            status = usecase.start("dev", "x", workdir=ROOT)
            run = store.get(status.run_id)
            # 起動時刻をずらす（同じ秒に起動しても順序が決まるように）
            store.save(run.model_copy(update={"started_at": run.started_at + timedelta(minutes=i)}))
            finish(store, status.run_id, launcher, run.pid)
            ids.append(status.run_id)

        listed = usecase.list(limit=2)

        assert [s.run_id for s in listed] == [ids[2], ids[1]]
        assert all(s.log_tail == "" for s in listed)

    def test_workflows(self, tmp_path):
        usecase, _, _ = make_usecase(tmp_path)
        assert [w.name for w in usecase.workflows()] == ["dev"]


class TestMcpServer:
    def test_tools_are_listed(self, tmp_path):
        usecase, _, _ = make_usecase(tmp_path)
        tools = asyncio.run(build_server(usecase).list_tools())
        assert {t.name for t in tools} == {"workflows", "start", "status", "runs", "cancel"}

    def test_start_and_status_via_tools(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        server = build_server(usecase)

        started = asyncio.run(
            server.call_tool("start", {"workflow": "dev", "brief": "x", "workdir": str(ROOT)})
        )
        run_id = started.structured_content["run_id"]
        assert started.structured_content["state"] == "running"
        finish(store, run_id, launcher, 1001, summary="要約")
        result = asyncio.run(server.call_tool("status", {"run_id": run_id}))

        assert result.structured_content["state"] == "done"
        assert result.structured_content["result"]["summary"] == "要約"

    def test_domain_errors_reach_the_model(self, tmp_path):
        """ドメインの例外はメッセージ付きの ToolError になる（SDK に伏せられない）。"""
        usecase, _, _ = make_usecase(tmp_path)
        server = build_server(usecase)
        with pytest.raises(ToolError, match="nope"):
            asyncio.run(
                server.call_tool("start", {"workflow": "nope", "brief": "x", "workdir": str(ROOT)})
            )

    def test_wait_for_finish_returns_when_finished(self, tmp_path):
        usecase, store, launcher = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id

        async def scenario():
            waiting = asyncio.create_task(
                wait_for_finish(usecase, run_id, 10, interval=0.01)
            )
            await asyncio.sleep(0.05)
            finish(store, run_id, launcher, 1001)
            return await asyncio.wait_for(waiting, timeout=5)

        assert asyncio.run(scenario()).state == "done"

    def test_wait_for_finish_gives_up_after_wait_sec(self, tmp_path):
        usecase, _, _ = make_usecase(tmp_path)
        run_id = usecase.start("dev", "x", workdir=ROOT).run_id
        status = asyncio.run(wait_for_finish(usecase, run_id, 0.05, interval=0.01))
        assert status.state == "running"
