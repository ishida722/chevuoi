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
        """タイトル先頭のタグを取り出す。最初の空白までをタグとする。無ければ None。

        運用は「未来リサーチ テストを実施する」のようにスペース区切りで
        先頭の1語をタグとする。区切りは半角スペースに限らず、全角スペース
        （U+3000）やタブなど Unicode の空白文字全般を受け付ける。
        """
        parts = title.split(maxsplit=1)
        if len(parts) < 2:
            return None
        return cls(value=parts[0])
