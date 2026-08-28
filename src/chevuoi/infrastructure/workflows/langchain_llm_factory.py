from __future__ import annotations

from typing import Any

from injector import inject

from chevuoi.domain.exceptions import WorkflowError
from chevuoi.domain.ports.llm_factory import LlmFactory
from chevuoi.infrastructure.config.settings import AppConfig


class LangchainLlmFactory(LlmFactory):
    """AppConfig.llm のモデル名から BaseChatModel を構築する。

    llm 未設定でもスキャン・一覧は動き、ロード時にのみエラーになる
    （二段階ロードの利点を設定面でも保つ）。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def create(self) -> Any:
        if self._config.llm is None:
            raise WorkflowError(
                "設定に [llm] がありません。ワークフローのロードには llm.model が必要です"
            )
        try:
            from langchain.chat_models import init_chat_model
        except ImportError as exc:
            raise WorkflowError(
                "LLM の構築には langchain パッケージが必要です（uv add langchain）"
            ) from exc
        return init_chat_model(self._config.llm.model)
