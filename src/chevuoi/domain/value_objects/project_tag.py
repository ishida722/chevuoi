from __future__ import annotations

from pydantic import BaseModel


class ProjectTag(BaseModel):
    """カードタイトル先頭のプロジェクトタグ（例: "MIRAI ログイン修正" の MIRAI）。"""

    model_config = {"frozen": True}

    value: str

    def __str__(self) -> str:
        return self.value

    @classmethod
    def from_title(cls, title: str) -> ProjectTag | None:
        """タイトル先頭のタグを取り出す。最初のスペースまでをタグとする。無ければ None。

        運用は「未来リサーチ テストを実施する」のようにスペース区切りで
        先頭の1語をタグとする。
        """
        head, sep, _ = title.partition(" ")
        if not sep:
            return None
        tag = head.strip()
        if not tag:
            return None
        return cls(value=tag)
