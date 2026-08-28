from __future__ import annotations

import logging

from injector import inject

from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.ports.graph_executor import ExecutionResult, GraphExecutor

logger = logging.getLogger(__name__)


class RunWorkflowUsecase:
    """名指しされたワークフローを 1 回実行する（vuoi workflow run）。

    存在しない・無効・ロード失敗は Registry が WorkflowError として送出する。
    """

    @inject
    def __init__(self, registry: WorkflowRegistry, executor: GraphExecutor) -> None:
        self._registry = registry
        self._executor = executor

    def execute(self, name: str, message: str) -> ExecutionResult:
        self._registry.scan()
        workflow = self._registry.get(name)
        logger.info("ワークフロー実行開始: %s", name)
        result = self._executor.execute(workflow, message)
        logger.info("ワークフロー実行終了: %s", name)
        return result
