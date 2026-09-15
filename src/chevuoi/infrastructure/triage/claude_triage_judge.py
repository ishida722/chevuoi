from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from injector import inject

from vuoi_sdk import Runner

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.entities.triage_judgment import TriageJudgment
from chevuoi.domain.ports.triage_judge import TriageJudge
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger("vuoi.triage.judge")

# 重複判定はカードの内容だけで答えられる問いなので、ツールを 1 つも事前承認しない。
# --allowedTools は事前承認であって制限ではないため、これは「禁止」ではない
NO_TOOLS: tuple[str, ...] = ()
# 解決済み判定はベース参照のチェックアウトを読む。読み取り系だけを事前承認する
READ_ONLY_TOOLS = ("Read", "Grep", "Glob")

DUPLICATE_PROMPT = """\
あなたはチケットの重複判定器です。次の 2 枚のカードが「同じ問題」を指しているかを判定してください。

## カード A
タイトル: {a_title}
根拠: {a_evidence}
本文:
{a_body}

## カード B
タイトル: {b_title}
根拠: {b_evidence}
本文:
{b_body}

## 判断ルール
- 同じ原因・同じ修正で片付くなら "duplicate"、別々に対応が要るなら "distinct"
- 同じファイルを指していても、問題が別なら "distinct"
- 情報が足りず判断できない場合は confidence を "low" にする（無理に決めない）
- リポジトリは見ない。上の記述だけで判断する

## 出力
次の JSON だけを出力する（前後に説明文やコードフェンスを付けない）:
{{"verdict": "duplicate" | "distinct", "confidence": "high" | "low", "reason": "<1〜2 文の理由>"}}
"""

RESOLVED_PROMPT = """\
あなたはチケットの鮮度判定器です。次のカードが指摘する問題が、現在のコードで既に解消しているかを判定してください。

## カード
タイトル: {title}
根拠: {evidence}
本文:
{body}

## 判断ルール
- カレントディレクトリのコードはベースブランチの内容である。ここを読んで判断する
- 指摘された問題が現在のコードに残っているなら "unresolved"
- 指摘された箇所が既に直っている、または対象そのものが無くなっているなら "resolved"
- 読んでも確かめられない、判断材料が足りない場合は confidence を "low" にする（無理に決めない）
- ファイルは読むだけで、変更してはならない

## 出力
次の JSON だけを出力する（前後に説明文やコードフェンスを付けない）:
{{"verdict": "resolved" | "unresolved", "confidence": "high" | "low", "reason": "<1〜2 文の理由>"}}
"""


def _extract_json(text: str) -> dict | None:
    """出力中の最初の JSON オブジェクトを取り出す（コードフェンス混入にも耐える）。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _evidence_text(card: TriageCard) -> str:
    return ", ".join(card.evidence) if card.evidence else "（なし）"


class ClaudeTriageJudge(TriageJudge):
    """claude -p に判定を 1 つ出させ、想定外の出力はすべて棄権に落とす。

    ClaudeWorkflowRouter と同じ契約: 例外を投げず、確信が持てないことを値で返す。
    モデルは `[triage] model` で指定する（分類だけなので既定は軽量モデル）。
    """

    @inject
    def __init__(self, runner: Runner, config: AppConfig) -> None:
        self._runner = runner
        # 空文字列は「指定しない」＝ Claude Code の既定モデル
        self._model = config.triage.model or None

    def judge_duplicate(self, card: TriageCard, other: TriageCard) -> TriageJudgment:
        prompt = DUPLICATE_PROMPT.format(
            a_title=card.title,
            a_evidence=_evidence_text(card),
            a_body=card.body or "（本文なし）",
            b_title=other.title,
            b_evidence=_evidence_text(other),
            b_body=other.body or "（本文なし）",
        )
        judgment = self._judge(prompt, ("duplicate", "distinct"), allowed_tools=NO_TOOLS)
        if judgment.verdict == "duplicate":
            return judgment.model_copy(update={"duplicate_of": other.id})
        return judgment

    def judge_resolved(self, card: TriageCard, *, cwd: Path) -> TriageJudgment:
        prompt = RESOLVED_PROMPT.format(
            title=card.title,
            evidence=_evidence_text(card),
            body=card.body or "（本文なし）",
        )
        return self._judge(
            prompt, ("resolved", "unresolved"), allowed_tools=READ_ONLY_TOOLS, cwd=cwd
        )

    def _judge(
        self,
        prompt: str,
        allowed_verdicts: tuple[str, ...],
        *,
        allowed_tools: tuple[str, ...],
        cwd: Path | None = None,
    ) -> TriageJudgment:
        # permission_mode は渡さない。プロンプトには信頼できないカードの本文が入るので、
        # 判定フェーズに書き込みを一括承認する理由はない（ClaudeWorkflowRouter と同じ）
        result = self._runner.run(
            prompt, cwd=cwd, allowed_tools=allowed_tools, model=self._model
        )
        if not result.ok:
            logger.warning("トリアージ判定の実行失敗: %s", result.output)
            return TriageJudgment(reason=f"判定の実行に失敗: {result.output[:200]}")
        data = _extract_json(result.output)
        if data is None:
            return TriageJudgment(reason=f"判定出力を解析できません: {result.output[:200]}")
        verdict = data.get("verdict")
        reason = str(data.get("reason", ""))
        if verdict not in allowed_verdicts:
            return TriageJudgment(reason=f"想定外の verdict '{verdict}' が返されました: {reason}")
        confidence = "high" if data.get("confidence") == "high" else "low"
        return TriageJudgment(verdict=verdict, confidence=confidence, reason=reason)
