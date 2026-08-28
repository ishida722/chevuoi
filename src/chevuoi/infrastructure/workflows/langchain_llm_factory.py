from __future__ import annotations

from typing import Any

from injector import inject

from chevuoi.domain.exceptions import WorkflowError
from chevuoi.domain.ports.llm_factory import LlmFactory
from chevuoi.infrastructure.config.settings import AppConfig


class LangchainLlmFactory(LlmFactory):
    """AppConfig.llm のモデル名から BaseChatModel を構築する。

    llm 未設定なら None を返す（ctx.llm = None）。runner だけで完結する
    ワークフローは [llm] なしでロード・実行できる。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def create(self) -> Any:
        if self._config.llm is None:
            return None
        try:
            from langchain.chat_models import init_chat_model
        except ImportError as exc:
            raise WorkflowError(
                "LLM の構築には langchain パッケージが必要です（uv add langchain）"
            ) from exc
        return init_chat_model(self._config.llm.model)
