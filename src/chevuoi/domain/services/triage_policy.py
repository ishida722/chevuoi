"""トリアージの決定表（純粋関数）。外部依存なし・副作用なし。

判定は LLM、行動はこの表が決める。表そのものを単体テストする。
"""

from __future__ import annotations

from pydantic import BaseModel

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.entities.triage_judgment import TriageJudgment
from chevuoi.domain.entities.triage_plan import (
    LABEL_DUPLICATE,
    LABEL_NEEDS_INFO,
    LABEL_REVIEW,
    LABEL_STALE,
    TriagePlan,
)
from chevuoi.domain.value_objects.card_id import CardId


class DeterministicSignals(BaseModel):
    """決定表に渡す、LLM を使わずに得られた事実。"""

    model_config = {"frozen": True}

    has_evidence: bool = False
    body_is_empty: bool = True
    path_missing_in_base: bool = False  # evidence のパスがベースに存在しない
    unchanged_since_issue: bool = False  # 起票時コミット以降そのパスが変わっていない
    exact_title_duplicate: bool = False
    # 決定的な事前フィルタ（Inbox / 自動起票 / settle / 前回から変化）を通ったか
    is_target: bool = True
    # 第 1 層で決まらず、第 2 層（LLM）の照会が必要だったか
    needs_judgment: bool = False
    # 必要な照会が予算（判定回数・候補ペア）に阻まれて行えなかったか。棄権と同じく
    # 「人間が見る」へ落とし、台帳には budget として残して次のランで拾い直す
    judgment_deferred: bool = False


def decide(
    card: TriageCard,
    *,
    signals: DeterministicSignals,
    judgment: TriageJudgment | None = None,
    representative: CardId | None = None,
    apply: bool = False,
) -> TriagePlan:
    """決定表（先に一致した行を採る）。

    apply が偽なら、破壊的な行（merge / archive）はラベル付けに降格する。
    dry-run で外部作用そのものを行うかどうかは呼び側（ユースケース）が決める。
    """
    judgment = judgment or TriageJudgment()

    # 1. 対象外カード（人間起票 / 非 Inbox / settle 内 / 変化なし）
    if not signals.is_target:
        return TriagePlan(card_id=card.id, action="skip", reason="対象外")

    # 2. 重複（代表が自分でないときだけ畳む）
    confirmed_duplicate = signals.exact_title_duplicate or (
        judgment.verdict == "duplicate" and not judgment.abstained
    )
    if confirmed_duplicate and representative is not None and representative != card.id:
        reason = judgment.reason or ("正規化タイトルが完全一致" if signals.exact_title_duplicate else "")
        if apply:
            return TriagePlan(
                card_id=card.id, action="merge", representative=representative, reason=reason
            )
        return TriagePlan(
            card_id=card.id,
            action="label",
            labels=(LABEL_DUPLICATE,),
            representative=representative,
            reason=reason,
        )

    # 3. 解決済み（決定的シグナルを伴う high な判定のときだけ）
    resolved_signal = signals.path_missing_in_base or not signals.unchanged_since_issue
    if judgment.verdict == "resolved" and not judgment.abstained and resolved_signal:
        if apply:
            return TriagePlan(card_id=card.id, action="archive", reason=judgment.reason)
        return TriagePlan(
            card_id=card.id, action="label", labels=(LABEL_STALE,), reason=judgment.reason
        )

    # 4. 情報不足（本文が空、かつ evidence が 1 件もない）
    if judgment.verdict == "needs_info" or (signals.body_is_empty and not signals.has_evidence):
        return TriagePlan(
            card_id=card.id,
            action="label",
            labels=(LABEL_NEEDS_INFO,),
            reason="本文が空で根拠もありません",
        )

    # 5. 棄権 / 上限超過
    if signals.judgment_deferred or (signals.needs_judgment and judgment.abstained):
        return TriagePlan(
            card_id=card.id,
            action="label",
            labels=(LABEL_REVIEW,),
            reason=(
                "予算超過のため今回は判定できませんでした"
                if signals.judgment_deferred
                else judgment.reason or "判定できませんでした"
            ),
        )

    # 6. それ以外（外部作用なし。台帳に keep を記録するだけ）
    return TriagePlan(card_id=card.id, action="keep")
