from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from injector import inject

from vuoi_sdk import Runner

from chevuoi.domain.entities.card import Card
from chevuoi.domain.entities.routing_decision import RoutingDecision
from chevuoi.domain.entities.workflow_meta import WorkflowMeta
from chevuoi.domain.ports.workflow_router import WorkflowRouter

logger = logging.getLogger("vuoi.workflows.router")

# 読み取りのみ許可（triage 仕様: チケット内容とリポジトリの読み取りのみ）
READ_ONLY_TOOLS = ("Read", "Grep", "Glob")

PROMPT_TEMPLATE = """\
あなたはタスクのルーターです。次のカードに最も適したワークフローを候補から 1 つ選んでください。

## カード
タイトル: {title}

本文:
{desc}

## 候補ワークフロー
{candidates}

## 判断ルール
- 各候補の summary / when_to_use に照らして選ぶ。when_to_use の「〜なら X を使う」という除外条件を尊重する
- どれにも明確に当てはまらない、または複数に同程度当てはまって決められない場合は棄権する（workflow を null にする）
- 確信度は「カードの記述がどの候補に当てはまるか」の明確さだけで決める。用途が明確に読み取れるときは "high" とする
- 作業の実行可能性（外部資料や Google Docs 等の取得可否、アクセス権、作業の難易度・規模）は確信度に反映しない。それらは後続のワークフローが判断する
- 変更対象がコードかドキュメントかの別も、候補の when_to_use がその区別を求めていない限り確信度に反映しない

## 出力
次の JSON だけを出力する（前後に説明文やコードフェンスを付けない）:
{{"workflow": "<候補の名前 または null>", "confidence": "high" | "low", "reason": "<1〜2 文の理由>"}}
"""


def _format_candidates(candidates: list[WorkflowMeta]) -> str:
    blocks = []
    for m in candidates:
        block = f"### {m.name}\nsummary: {m.summary}"
        if m.when_to_use.strip():
            block += f"\nwhen_to_use: {m.when_to_use.strip()}"
        blocks.append(block)
    return "\n\n".join(blocks)


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


class ClaudeWorkflowRouter(WorkflowRouter):
    """claude -p に名前を 1 つ出させ、候補に実在するものだけを採用する。

    候補外の名前・解析不能・runner 失敗はすべて棄権として返す。
    """

    @inject
    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    def build_prompt(self, card: Card, candidates: list[WorkflowMeta]) -> str:
        return PROMPT_TEMPLATE.format(
            title=card.name,
            desc=card.desc or "（本文なし）",
            candidates=_format_candidates(candidates),
        )

    def route(
        self, card: Card, candidates: list[WorkflowMeta], *, cwd: Path | None = None
    ) -> RoutingDecision:
        if not candidates:
            return RoutingDecision(workflow=None, reason="候補ワークフローがありません")
        result = self._runner.run(
            self.build_prompt(card, candidates),
            cwd=cwd,
            allowed_tools=READ_ONLY_TOOLS,
        )
        if not result.ok:
            logger.warning("ルーター実行失敗: %s", result.output)
            return RoutingDecision(workflow=None, reason=f"ルーター実行失敗: {result.output}")
        data = _extract_json(result.output)
        if data is None:
            return RoutingDecision(
                workflow=None, reason=f"ルーター出力を解析できません: {result.output[:200]}"
            )
        name = data.get("workflow")
        confidence = "high" if data.get("confidence") == "high" else "low"
        reason = str(data.get("reason", ""))
        if name is None:
            return RoutingDecision(workflow=None, confidence=confidence, reason=reason)
        names = {m.name for m in candidates}
        if name not in names:
            return RoutingDecision(
                workflow=None,
                confidence=confidence,
                reason=f"候補外の名前 '{name}' が返されました: {reason}",
            )
        return RoutingDecision(workflow=name, confidence=confidence, reason=reason)
