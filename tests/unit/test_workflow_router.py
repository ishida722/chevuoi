"""ClaudeWorkflowRouter / SelectWorkflowUsecase のテスト（runner はフェイク）。"""

from __future__ import annotations

from vuoi_sdk import Runner, RunResult

from chevuoi.application.usecases.select_workflow_usecase import SelectWorkflowUsecase
from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.routing_decision import RoutingDecision
from chevuoi.domain.entities.workflow_meta import ScanResult, WorkflowMeta
from chevuoi.domain.ports.workflow_loader import WorkflowLoader
from chevuoi.domain.ports.workflow_router import WorkflowRouter
from chevuoi.domain.ports.workflow_scanner import WorkflowScanner
from chevuoi.infrastructure.workflows.claude_workflow_router import ClaudeWorkflowRouter
from chevuoi.infrastructure.config.settings import RouterConfig
from chevuoi.interfaces.cli.adhoc_card import AdhocCard
from tests.unit.fakes import make_config


def meta(name: str, **kw) -> WorkflowMeta:
    return WorkflowMeta(
        name=name, path=f"/x/{name}", entry_path=f"/x/{name}/workflow.py",
        api_version=1, summary=f"{name} の説明", **kw,
    )


DEV = meta("dev", intents=["card.dev"], when_to_use="コード変更。調査だけなら research")
RESEARCH = meta("research", intents=["card.research"], when_to_use="調査・報告書")


class ScriptedRunner(Runner):
    def __init__(self, output: str, ok: bool = True) -> None:
        self.output, self.ok, self.calls = output, ok, []

    def run(
        self, prompt, *, cwd=None, session_id=None, allowed_tools=None, model=None,
        permission_mode=None,
    ):
        self.calls.append({
            "prompt": prompt, "allowed_tools": allowed_tools, "model": model,
            "permission_mode": permission_mode,
        })
        return RunResult(ok=self.ok, output=self.output)


class TestClaudeWorkflowRouter:
    def route(self, output, ok=True, model=None):
        runner = ScriptedRunner(output, ok)
        # model=None のときは [router] セクション省略（既定値）の経路を通す
        config = make_config() if model is None else make_config(router=RouterConfig(model=model))
        d = ClaudeWorkflowRouter(runner, config).route(
            AdhocCard("X: 調べて", "ログを調査"), [DEV, RESEARCH]
        )
        return d, runner

    def test_picks_candidate(self):
        d, runner = self.route('{"workflow": "research", "confidence": "high", "reason": "調査依頼"}')
        assert d.workflow == "research" and d.confidence == "high"
        assert runner.calls[0]["allowed_tools"] == ("Read", "Grep", "Glob")
        assert "when_to_use: 調査・報告書" in runner.calls[0]["prompt"]

    def test_permission_mode_is_not_passed(self):
        # 分類フェーズは信頼できないカード本文をプロンプトに含むので、書き込みを通さない
        _, runner = self.route('{"workflow": "dev", "confidence": "high", "reason": "r"}')
        assert runner.calls[0]["permission_mode"] is None

    def test_model_from_config_is_passed(self):
        _, runner = self.route('{"workflow": "dev", "confidence": "high", "reason": "r"}', model="haiku")
        assert runner.calls[0]["model"] == "haiku"

    def test_model_unset_passes_none(self):
        _, runner = self.route('{"workflow": "dev", "confidence": "high", "reason": "r"}')
        assert runner.calls[0]["model"] is None

    def test_prompt_excludes_feasibility_from_confidence(self):
        # 資料の取得可否などの実行可能性を確信度に混ぜないルールが明記されている
        _, runner = self.route('{"workflow": "dev", "confidence": "high", "reason": "r"}')
        prompt = runner.calls[0]["prompt"]
        assert "取得可否" in prompt and "確信度に反映しない" in prompt

    def test_code_fence_tolerated(self):
        d, _ = self.route('```json\n{"workflow": "dev", "confidence": "high", "reason": "r"}\n```')
        assert d.workflow == "dev"

    def test_null_is_abstain(self):
        d, _ = self.route('{"workflow": null, "confidence": "low", "reason": "曖昧"}')
        assert d.abstained and "曖昧" in d.reason

    def test_unknown_name_is_abstain(self):
        d, _ = self.route('{"workflow": "ghost", "confidence": "high", "reason": "r"}')
        assert d.abstained and "ghost" in d.reason

    def test_garbage_and_failure_are_abstain(self):
        assert self.route("わかりません")[0].abstained
        assert self.route("x", ok=False)[0].abstained

    def test_no_candidates_skips_runner(self):
        runner = ScriptedRunner("{}")
        d = ClaudeWorkflowRouter(runner, make_config()).route(AdhocCard("X: a"), [])
        assert d.abstained and runner.calls == []


class FakeScanner(WorkflowScanner):
    def scan(self):
        return ScanResult(metas={"dev": DEV, "research": RESEARCH})


class FakeLoader(WorkflowLoader):
    def load(self, meta):
        raise AssertionError("選択で load は呼ばれない")


class FixedRouter(WorkflowRouter):
    def __init__(self, decision: RoutingDecision) -> None:
        self.decision, self.calls = decision, 0

    def route(self, card, candidates, *, cwd=None):
        self.calls += 1
        return self.decision


def usecase(decision: RoutingDecision):
    router = FixedRouter(decision)
    return SelectWorkflowUsecase(WorkflowRegistry(FakeScanner(), FakeLoader()), router), router


class TestSelectWorkflowUsecase:
    def test_marker_bypasses_llm(self):
        uc, router = usecase(RoutingDecision(workflow="dev", confidence="high"))
        meta_, d = uc.execute(AdhocCard("X: [card.research] 何か"))
        assert meta_ is RESEARCH and router.calls == 0 and d.confidence == "high"

    def test_llm_high_confidence_selected(self):
        uc, _ = usecase(RoutingDecision(workflow="dev", confidence="high", reason="r"))
        meta_, _ = uc.execute(AdhocCard("X: 直して"))
        assert meta_ is DEV

    def test_low_confidence_abstains(self):
        uc, _ = usecase(RoutingDecision(workflow="dev", confidence="low"))
        meta_, d = uc.execute(AdhocCard("X: 直して"))
        assert meta_ is None and d.workflow == "dev"

    def test_router_abstain(self):
        uc, _ = usecase(RoutingDecision(workflow=None, reason="曖昧"))
        meta_, d = uc.execute(AdhocCard("X: なにか"))
        assert meta_ is None and d.abstained
