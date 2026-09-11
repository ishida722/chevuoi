"""ClaudeTriageJudge のテスト（runner はプロセス境界なのでフェイク）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from vuoi_sdk import Runner, RunResult

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import DEFAULT_TRIAGE_MODEL, TriageConfig
from chevuoi.infrastructure.triage.claude_triage_judge import ClaudeTriageJudge
from tests.unit.fakes import make_config


def card(external_id: str, title: str = "MIRAI 落ちる") -> TriageCard:
    return TriageCard(
        id=CardId(source="trello", external_id=external_id),
        title=title,
        body="本文",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        key="auto",
        evidence=("src/foo.py:1",),
    )


class ScriptedRunner(Runner):
    def __init__(self, output: str, ok: bool = True) -> None:
        self.output, self.ok, self.calls = output, ok, []

    def run(
        self, prompt, *, cwd=None, session_id=None, allowed_tools=None, model=None,
        permission_mode=None,
    ):
        self.calls.append({
            "prompt": prompt, "cwd": cwd, "allowed_tools": allowed_tools, "model": model,
            "permission_mode": permission_mode,
        })
        return RunResult(ok=self.ok, output=self.output)


def judge_duplicate(output: str, ok: bool = True, model: str | None = None):
    runner = ScriptedRunner(output, ok)
    config = make_config() if model is None else make_config(triage=TriageConfig(model=model))
    judgment = ClaudeTriageJudge(runner, config).judge_duplicate(card("a"), card("b"))
    return judgment, runner


class TestAbstention:
    def test_runner_failure_is_an_abstention(self):
        """判定器の実行に失敗したとき、例外を投げず棄権を返すこと。"""
        judgment, _ = judge_duplicate("落ちた", ok=False)
        assert judgment.abstained and judgment.verdict == "unknown"

    def test_broken_json_is_an_abstention(self):
        """出力が JSON として読めないとき、棄権を返すこと。"""
        judgment, _ = judge_duplicate("たぶん同じだと思います")
        assert judgment.abstained

    def test_unexpected_verdict_is_an_abstention(self):
        """想定外の verdict が返ったとき、それを採らずに棄権すること。"""
        judgment, _ = judge_duplicate('{"verdict": "merge", "confidence": "high"}')
        assert judgment.abstained

    def test_low_confidence_is_an_abstention(self):
        """確信度が high でないとき、判定は棄権として扱われること。"""
        judgment, _ = judge_duplicate('{"verdict": "duplicate", "confidence": "low"}')
        assert judgment.abstained is True and judgment.verdict == "duplicate"


class TestJudgeDuplicate:
    def test_duplicate_verdict_records_the_other_card(self):
        """重複と判定したとき、相手のカード ID を記録して返すこと。"""
        judgment, _ = judge_duplicate(
            '{"verdict": "duplicate", "confidence": "high", "reason": "同じ原因"}'
        )
        assert judgment.verdict == "duplicate" and not judgment.abstained
        assert judgment.duplicate_of == CardId(source="trello", external_id="b")

    def test_duplicate_judgment_pre_approves_no_tools(self):
        """重複判定はカードの内容だけで答える問いなので、ツールを 1 つも事前承認しないこと。"""
        _, runner = judge_duplicate('{"verdict": "distinct", "confidence": "high"}')
        assert runner.calls[0]["allowed_tools"] == ()
        assert runner.calls[0]["cwd"] is None

    def test_permission_mode_is_not_passed(self):
        """判定フェーズには書き込みを一括承認しないこと（プロンプトにカード本文が入る）。"""
        _, runner = judge_duplicate('{"verdict": "distinct", "confidence": "high"}')
        assert runner.calls[0]["permission_mode"] is None

    def test_model_defaults_to_the_light_model(self):
        """[triage] を書かなくても軽量モデルで動くこと（既定モデルに落ちない）。"""
        _, runner = judge_duplicate('{"verdict": "distinct", "confidence": "high"}')
        assert runner.calls[0]["model"] == DEFAULT_TRIAGE_MODEL

    def test_empty_model_passes_none(self):
        """model = "" は「指定しない」＝ Claude Code の既定モデルになること。"""
        _, runner = judge_duplicate('{"verdict": "distinct", "confidence": "high"}', model="")
        assert runner.calls[0]["model"] is None


class TestJudgeResolved:
    def test_resolved_judgment_reads_the_base_checkout_only(self):
        """鮮度判定はベースのチェックアウトを cwd にし、読み取り系だけを事前承認すること。"""
        runner = ScriptedRunner('{"verdict": "resolved", "confidence": "high", "reason": "直っている"}')
        judgment = ClaudeTriageJudge(runner, make_config()).judge_resolved(
            card("a"), cwd=Path("/tmp/base")
        )
        assert judgment.verdict == "resolved" and not judgment.abstained
        assert runner.calls[0]["cwd"] == Path("/tmp/base")
        assert runner.calls[0]["allowed_tools"] == ("Read", "Grep", "Glob")

    def test_duplicate_verdict_is_rejected_for_freshness(self):
        """鮮度判定に重複の verdict が返ってきたとき、採らずに棄権すること。"""
        runner = ScriptedRunner('{"verdict": "duplicate", "confidence": "high"}')
        judgment = ClaudeTriageJudge(runner, make_config()).judge_resolved(
            card("a"), cwd=Path("/tmp/base")
        )
        assert judgment.abstained
