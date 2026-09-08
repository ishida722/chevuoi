"""リポジトリ名（"owner/name"）の表記ゆれを揃える純粋関数。外部依存なし。"""

from __future__ import annotations

import re

_SEPARATORS = re.compile(r"[/:]")


def normalize_repo(value: str) -> str | None:
    """設定値やリモート URL を "owner/name" に揃える。解釈できなければ None。

    受け付ける形は "owner/name" / "owner/name.git" / "https://github.com/owner/name" /
    "git@github.com:owner/name.git" など。設定とリモート URL で同じ規則を使うため、
    どちらの書き方をしても同じ PR に一致する。
    """
    parts = [p for p in _SEPARATORS.split(value.strip().rstrip("/")) if p]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1].removesuffix(".git")
    # GitHub の owner（ユーザー／組織）に "." は使えないので、ホスト名を owner と
    # 取り違えた場合（"https://github.com/owner" など）はここで弾く
    if not owner or not name or "." in owner:
        return None
    return f"{owner}/{name}"
