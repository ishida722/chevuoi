"""vuoi triage の CLI 結線のテスト（Injector はスタブに差し替える）。

破壊的操作の入口（--apply と設定からの有効化）と終了コードだけを見る。
"""

from __future__ import annotations

from unittest.mock import patch

from chevuoi.application.usecases.triage_usecase import TriageUsecase
from chevuoi.domain.entities.triage_plan import TriagePlan, TriageReport
from chevuoi.domain.exceptions import TriageError
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import AppConfig, TriageConfig
from chevuoi.interfaces.cli import main as cli_main
from tests.unit.fakes import make_config


class FakeTriageUsecase:
    def __init__(self, report: TriageReport | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._report = report or TriageReport()
        self._error = error

    def execute(self, *, apply: bool = False, project=None, limit=None) -> TriageReport:
        self.calls.append({"apply": apply, "project": project, "limit": limit})
        if self._error is not None:
            raise self._error
        return self._report


class StubInjector:
    def __init__(self, config: AppConfig, usecase: FakeTriageUsecase) -> None:
        self._by_type = {AppConfig: config, TriageUsecase: usecase}

    def get(self, cls):
        return self._by_type[cls]


def run_cli(argv: list[str], config: AppConfig, usecase: FakeTriageUsecase) -> int:
    with patch.object(cli_main, "build_injector", return_value=StubInjector(config, usecase)):
        return cli_main.main(argv)


def failed_report() -> TriageReport:
    return TriageReport(
        planned=[TriagePlan(card_id=CardId(source="trello", external_id="a"), action="merge")],
        failed=[(CardId(source="trello", external_id="a"), "API エラー")],
    )


class TestTriageCommand:
    def test_disabled_triage_does_not_run(self):
        """[triage] enabled = false のとき、ユースケースを呼ばずに終了すること。"""
        usecase = FakeTriageUsecase()
        code = run_cli(["triage"], make_config(triage=TriageConfig(enabled=False)), usecase)
        assert usecase.calls == [] and code == 0

    def test_apply_defaults_to_false(self):
        """--apply を付けないとき、適用せずに計画だけを出すこと（既定は dry-run）。"""
        usecase = FakeTriageUsecase()
        code = run_cli(["triage"], make_config(), usecase)
        assert usecase.calls[0]["apply"] is False and code == 0

    def test_apply_flag_enables_application(self):
        """--apply を付けたとき、適用モードで実行すること。"""
        usecase = FakeTriageUsecase()
        run_cli(["triage", "--apply"], make_config(), usecase)
        assert usecase.calls[0]["apply"] is True

    def test_config_can_enable_application(self):
        """[triage] apply = true のとき、フラグ無しでも適用モードになること
        （破壊的操作を有効にする経路は 2 つある）。"""
        usecase = FakeTriageUsecase()
        run_cli(["triage"], make_config(triage=TriageConfig(apply=True)), usecase)
        assert usecase.calls[0]["apply"] is True

    def test_options_are_passed_through(self):
        """--project と --limit がそのままユースケースに渡ること。"""
        usecase = FakeTriageUsecase()
        run_cli(["triage", "--project", "MIRAI", "--limit", "5"], make_config(), usecase)
        assert usecase.calls[0]["project"] == "MIRAI" and usecase.calls[0]["limit"] == 5

    def test_failed_application_exits_nonzero(self):
        """適用に失敗したカードがあるとき、終了コード 1 で終わること
        （常駐から回したときに失敗を見落とさない）。"""
        usecase = FakeTriageUsecase(report=failed_report())
        assert run_cli(["triage", "--apply"], make_config(), usecase) == 1

    def test_domain_error_exits_nonzero(self):
        """設定不足などで実行できないとき、例外を投げずに終了コード 1 で終わること。"""
        usecase = FakeTriageUsecase(error=TriageError("trello.inbox_list_id が未設定"))
        assert run_cli(["triage"], make_config(), usecase) == 1
