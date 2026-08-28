from __future__ import annotations

from injector import Binder, Module, singleton

from vuoi_sdk import Runner

from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.ports.card_provider import CardProvider
from chevuoi.domain.ports.graph_executor import GraphExecutor
from chevuoi.domain.ports.llm_factory import LlmFactory
from chevuoi.domain.ports.workflow_loader import WorkflowLoader
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner
from chevuoi.domain.ports.node_runner import NodeRunner
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.infrastructure.claude.claude_node_runner import ClaudeNodeRunner
from chevuoi.infrastructure.config.settings import AppConfig
from chevuoi.infrastructure.git.git_worktree_manager import GitWorktreeManager
from chevuoi.infrastructure.trello.client import TrelloClient
from chevuoi.infrastructure.trello.trello_card_provider import TrelloCardProvider
from chevuoi.infrastructure.workflows.claude_cli_runner import ClaudeCliRunner
from chevuoi.infrastructure.workflows.fs_workflow_scanner import FsWorkflowScanner
from chevuoi.infrastructure.workflows.langchain_llm_factory import LangchainLlmFactory
from chevuoi.infrastructure.workflows.langgraph_executor import LangGraphExecutor
from chevuoi.infrastructure.workflows.python_workflow_loader import PythonWorkflowLoader


class AppModule(Module):
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def configure(self, binder: Binder) -> None:
        binder.bind(AppConfig, to=self._config, scope=singleton)
        binder.bind(TrelloClient, scope=singleton)
        binder.bind(CardProvider, to=TrelloCardProvider, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(WorktreeManager, to=GitWorktreeManager, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(NodeRunner, to=ClaudeNodeRunner, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(WorkflowScanner, to=FsWorkflowScanner, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(WorkflowLoader, to=PythonWorkflowLoader, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(LlmFactory, to=LangchainLlmFactory, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(Runner, to=ClaudeCliRunner, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(GraphExecutor, to=LangGraphExecutor, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(WorkflowRegistry, scope=singleton)  # キャッシュを持つため singleton 必須
