from __future__ import annotations

from pydantic import BaseModel

from chevuoi.domain.value_objects.card_id import CardId

MAX_SKIPPED_IN_COMMENT = 10  # 見送り理由の表示上限。超過分はコメント長を圧迫しないよう件数だけ出す


class IssuedCard(BaseModel):
    """発行した（または既存を再利用した）カードへの参照。Card は返さない。"""

    model_config = {"frozen": True}

    id: CardId
    url: str
    created: bool  # False なら既存カードを再利用した


class IssueReport(BaseModel):
    """1 ランの起票結果。親カードのコメントに載せる。"""

    issued: list[IssuedCard] = []
    skipped: list[str] = []  # 破棄理由つき（"上限超過: <title>" など）
    summary_card: IssuedCard | None = None  # 上限超過時の要約カード

    @property
    def is_empty(self) -> bool:
        return not (self.issued or self.skipped or self.summary_card)

    def to_comment(self) -> str:
        """親カードへのコメント文。"""
        lines = ["🤖 起票:"]
        for card in self.issued:
            label = "新規" if card.created else "既存"
            lines.append(f"- {label}: {card.url}")
        if self.summary_card is not None:
            label = "新規" if self.summary_card.created else "既存"
            lines.append(f"- 要約カード（{label}）: {self.summary_card.url}")
        for reason in self.skipped[:MAX_SKIPPED_IN_COMMENT]:
            lines.append(f"- 見送り: {reason}")
        rest = len(self.skipped) - MAX_SKIPPED_IN_COMMENT
        if rest > 0:
            lines.append(f"- 見送り: 他 {rest} 件（ログ参照）")
        return "\n".join(lines)
