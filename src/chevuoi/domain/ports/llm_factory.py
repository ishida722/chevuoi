from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LlmFactory(ABC):
    """ワークフローへ注入する LLM を構築するポート。

    実体（BaseChatModel）の型はインフラ層と SDK にしか現れないため Any を返す。
    """

    @abstractmethod
    def create(self) -> Any: ...
