from __future__ import annotations

import logging
import os
from collections import deque
from pathlib import Path

from injector import inject
from pydantic import ValidationError

from chevuoi.domain.entities.exec_run import ExecRun, RunFiles
from chevuoi.domain.exceptions import RunError, RunNotFound
from chevuoi.domain.ports.run_store import RunStore
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

RECORD = "run.json"


class FsRunStore(RunStore):
    """runs_dir/<run_id>/ に記録・ブリーフ・結果・ログを置く。

    作業ツリーの外に置くので、ワークフローの変更（changed_files）に混ざらない。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._root = config.runs_dir

    def create(self, run: ExecRun, brief: str) -> RunFiles:
        directory = self._dir(run.run_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
            files = self.files(run.run_id)
            files.brief.write_text(brief, encoding="utf-8")
        except OSError as e:
            raise RunError(f"実行記録を作成できません（{directory}）: {e}") from e
        self.save(run)
        return files

    def save(self, run: ExecRun) -> None:
        path = self._dir(run.run_id) / RECORD
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            raise RunError(f"実行記録を保存できません（{path}）: {e}") from e

    def get(self, run_id: str) -> ExecRun:
        # run_id は外部（MCP の引数）から来るので、runs_dir の外を指せないようにする
        if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
            raise RunNotFound(f"実行記録がありません: {run_id}")
        path = self._dir(run_id) / RECORD
        try:
            return ExecRun.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise RunNotFound(f"実行記録がありません: {run_id}") from e
        except (OSError, ValidationError) as e:
            raise RunError(f"実行記録を読めません（{path}）: {e}") from e

    def list(self) -> list[ExecRun]:
        if not self._root.is_dir():
            return []
        runs: list[ExecRun] = []
        for record in self._root.glob(f"*/{RECORD}"):
            try:
                runs.append(ExecRun.model_validate_json(record.read_text(encoding="utf-8")))
            except (OSError, ValidationError) as e:
                logger.warning("実行記録を読めないため飛ばします: %s (%s)", record, e)
        return runs

    def files(self, run_id: str) -> RunFiles:
        directory = self._dir(run_id)
        return RunFiles(
            brief=directory / "brief.md",
            result=directory / "result.json",
            log=directory / "exec.log",
        )

    def read_result(self, run_id: str) -> str | None:
        try:
            return self.files(run_id).result.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            raise RunError(f"結果を読めません: {e}") from e

    def log_tail(self, run_id: str, lines: int) -> str:
        try:
            with self.files(run_id).log.open(encoding="utf-8", errors="replace") as f:
                return "".join(deque(f, maxlen=lines)).rstrip("\n")
        except OSError:
            return ""

    def _dir(self, run_id: str) -> Path:
        return self._root / run_id
