from __future__ import annotations

from injector import Binder, Module, singleton

from chevuoi.domain.ports.card_provider import CardProvider
from chevuoi.domain.ports.node_runner import NodeRunner
from chevuoi.domain.ports.worktree_manager import WorktreeManager
from chevuoi.infrastructure.claude.claude_node_runner import ClaudeNodeRunner
from chevuoi.infrastructure.config.settings import AppConfig
from chevuoi.infrastructure.git.git_worktree_manager import GitWorktreeManager
from chevuoi.infrastructure.trello.client import TrelloClient
from chevuoi.infrastructure.trello.trello_card_provider import TrelloCardProvider


class AppModule(Module):
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def configure(self, binder: Binder) -> None:
        binder.bind(AppConfig, to=self._config, scope=singleton)
        binder.bind(TrelloClient, scope=singleton)
        binder.bind(CardProvider, to=TrelloCardProvider, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(WorktreeManager, to=GitWorktreeManager, scope=singleton)  # type: ignore[type-abstract]
        binder.bind(NodeRunner, to=ClaudeNodeRunner, scope=singleton)  # type: ignore[type-abstract]
