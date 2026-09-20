"""プロジェクトタグの照合（純粋関数）。外部依存なし・副作用なし。

タグは人間がチケットのタイトルに手で書くため、`[テレ東] ログイン修正` のように
括弧で囲んで書かれることがある。対応表を引くときは、括弧などの記号と
大文字小文字の差を無視して同じタグとみなす。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable


def tag_key(value: str) -> str:
    """タグの照合キー。括弧などの記号を落とし、大文字小文字を無視する。

    例: `[テレ東]`・`【テレ東】`・`テレ東` はすべて同じキーになる。
    記号だけのタグ（`[]` など）は落とすと何も残らないため、記号を残したまま
    casefold だけを行う。空文字に潰すと、無関係な記号タグ同士や、タグの無い
    カードと同じキーになり、別プロジェクトを引き当ててしまう。
    """
    stripped = "".join(ch for ch in value if unicodedata.category(ch)[0] not in "PS")
    return (stripped or value).casefold()


def match_tag(value: str, keys: Iterable[str]) -> str | None:
    """タグに対応する対応表のキーを 1 つ選ぶ。引けなければ None。

    完全一致 → 大文字小文字だけの違い → 記号を無視した一致、の順に優先する。
    記号まで一致する設定があるのにそれを飛ばして別のプロジェクトを引くと、
    誤ったリポジトリで作業することになる。同じ優先度の候補が複数あるときは
    対応表の並び順で先のものを使い、結果を決定的にする。
    """
    candidates = list(keys)
    for matches in (
        lambda key: key == value,
        lambda key: key.casefold() == value.casefold(),
        lambda key: tag_key(key) == tag_key(value),
    ):
        hit = next((key for key in candidates if matches(key)), None)
        if hit is not None:
            return hit
    return None
