from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel

from chevuoi.domain.entities.task_proposal import normalize_title
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag


class TriageCard(BaseModel):
    """トリアージ対象カードのスナップショット。振る舞いを持たない不変値。

    集合を純粋関数で扱うため、Card ADT ではなく値として持つ。
    """

    model_config = {"frozen": True}

    id: CardId
    title: str  # プロジェクトタグを含む生のタイトル
    body: str = ""
    url: str = ""
    created_at: datetime
    labels: tuple[str, ...] = ()
    # --- フッター由来（自動起票カードのみ） ---
    key: str = ""  # 冪等キー。空なら人間起票
    kind: str = "chore"
    generation: int = 0
    parent_id: CardId | None = None
    base_commit: str = ""  # 起票時に見ていたベースのコミット（フッターの base=）
    evidence: tuple[str, ...] = ()  # 本文の「根拠:」行から復元（"path:line" の形）

    @property
    def is_auto_issued(self) -> bool:
        return bool(self.key)

    @property
    def project_tag(self) -> ProjectTag | None:
        return ProjectTag.from_title(self.title)

    @property
    def evidence_paths(self) -> tuple[str, ...]:
        """evidence から行番号を落としたパスの列（重複は除き、順序は保つ）。

        リポジトリへの問い合わせにも類似度計算にも、行番号は使わない。
        """
        paths: list[str] = []
        for entry in self.evidence:
            path = entry.rsplit(":", 1)[0] if _has_line_suffix(entry) else entry
            path = path.strip()
            if path and path not in paths:
                paths.append(path)
        return tuple(paths)

    def digest(self) -> str:
        """再判定要否の判断に使う内容ダイジェスト。タイトル + 本文の正規化 sha1 先頭 12 桁。"""
        seed = f"{normalize_title(self.title)}\n{normalize_title(self.body)}"
        return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]


def _has_line_suffix(entry: str) -> bool:
    head, sep, tail = entry.rpartition(":")
    return bool(sep) and bool(head) and tail.isdigit()
