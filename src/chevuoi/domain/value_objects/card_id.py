from __future__ import annotations

from pydantic import BaseModel


class CardId(BaseModel):
    """ソース修飾つきのカード ID（例: trello:oFm0QQAr）。"""

    model_config = {"frozen": True}

    source: str
    external_id: str

    def __str__(self) -> str:
        return f"{self.source}:{self.external_id}"
