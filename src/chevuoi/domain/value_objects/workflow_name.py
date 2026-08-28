from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

WorkflowName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
"""ワークフロー名。ディレクトリ名がそのまま名前になる（仕様 §11-2）。"""

Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
"""分類ラベル。重複可・集合フィルタ用。"""

Intent = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]*$")]
"""直接指名キー。全体で一意（スキャン時に衝突検証される）。"""
