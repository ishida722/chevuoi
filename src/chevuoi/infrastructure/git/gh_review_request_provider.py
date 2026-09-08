from __future__ import annotations

import json
import logging
import subprocess
from typing import Any

from chevuoi.domain.entities.review_request import ReviewRequest
from chevuoi.domain.exceptions import ReviewRequestError
from chevuoi.domain.ports.review_request_provider import ReviewRequestProvider

logger = logging.getLogger(__name__)

# gh に出させるフィールド。カードの本文・冪等キーに必要な分だけに絞る
JSON_FIELDS = "number,title,url,repository,author,updatedAt"
TIMEOUT_SEC = 60


class GhReviewRequestProvider(ReviewRequestProvider):
    """gh search prs でレビュー依頼中の PR を取得する。認証は gh のログインに委ねる
    （@me は gh の認証ユーザー）。MCP や GitHub API クライアントは使わない。
    """

    def fetch(self, *, limit: int) -> list[ReviewRequest]:
        args = [
            "gh", "search", "prs",
            "--review-requested", "@me",
            "--state", "open",
            "--sort", "updated",
            "--limit", str(limit),
            "--json", JSON_FIELDS,
        ]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired as e:
            raise ReviewRequestError(f"gh search prs がタイムアウトしました（{TIMEOUT_SEC}秒）") from e
        except OSError as e:
            raise ReviewRequestError(f"gh の起動に失敗: {e}") from e
        if proc.returncode != 0:
            raise ReviewRequestError(f"gh search prs 失敗: {proc.stderr.strip()}")
        try:
            items = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as e:
            raise ReviewRequestError(f"gh の出力を解釈できません: {e}") from e
        if not isinstance(items, list):
            raise ReviewRequestError("gh の出力が配列ではありません")
        return [r for r in (self._to_request(i) for i in items) if r is not None]

    @staticmethod
    def _to_request(item: Any) -> ReviewRequest | None:
        """1 件の検索結果を ReviewRequest にする。識別子が欠けていれば読み飛ばす。"""
        if not isinstance(item, dict):
            logger.warning("解釈できない検索結果を読み飛ばします: %r", item)
            return None
        repository = (item.get("repository") or {}).get("nameWithOwner", "")
        number = item.get("number")
        if not repository or not isinstance(number, int):
            logger.warning("リポジトリ／PR 番号が取れない検索結果を読み飛ばします: %r", item)
            return None
        return ReviewRequest(
            repository=repository,
            number=number,
            title=item.get("title", ""),
            url=item.get("url", ""),
            author=(item.get("author") or {}).get("login", ""),
            updated_at=item.get("updatedAt", ""),
        )
