from __future__ import annotations

from typing import Any

import httpx
from injector import inject, noninjectable

from chevuoi.domain.exceptions import ChevuoiError
from chevuoi.infrastructure.config.settings import AppConfig

BASE_URL = "https://api.trello.com/1"


class TrelloApiError(ChevuoiError):
    """Trello API のエラー応答。認証情報を含まないメッセージだけを持つ。"""


class TrelloClient:
    """Trello REST API を httpx で呼ぶ薄い HTTP クライアント。MCP は使わない。"""

    @inject
    @noninjectable("transport")
    def __init__(self, config: AppConfig, transport: httpx.BaseTransport | None = None) -> None:
        self._auth_params = {
            "key": config.trello.api_key,
            "token": config.trello.api_token,
        }
        self._client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=30)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params)

    def put(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("PUT", path, params)

    def post(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, params)

    def _request(self, method: str, path: str, params: dict[str, Any] | None) -> Any:
        response = self._client.request(
            method, path, params={**self._auth_params, **(params or {})}
        )
        if response.is_error:
            # URL に認証クエリが含まれるため、raise_for_status は使わず
            # key/token を含まないメッセージで投げ直す（ログ・stderr への漏洩防止）
            raise TrelloApiError(f"Trello API error {response.status_code}: {method} {path}")
        return response.json()
