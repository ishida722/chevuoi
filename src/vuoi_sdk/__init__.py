"""vuoi ワークフローのユーザー契約（SDK）。

ユーザーのワークフローが import する唯一の公開インターフェース。
ホスト本体（chevuoi）には依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Mapping, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

API_VERSION = 1


class BaseState(TypedDict):
    """ホストが読み書きを保証するキー。ユーザーはこれを継承して拡張する。"""

    messages: Annotated[list[BaseMessage], add_messages]


@dataclass(frozen=True)
class WorkflowContext:
    """依存性注入。ユーザーは自前で LLM や接続を作らない。"""

    llm: BaseChatModel
    settings: Mapping[str, Any]
    logger: Any


__all__ = [
    "API_VERSION",
    "BaseState",
    "WorkflowContext",
    "StateGraph",
    "START",
    "END",
]
