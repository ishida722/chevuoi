"""JsonTriageLedger のテスト（ファイルはプロセス境界なので実ファイルを使う）。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from chevuoi.domain.ports.triage_ledger import TriageLedgerEntry
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.state.json_triage_ledger import JsonTriageLedger
from tests.unit.fakes import make_config

CARD = CardId(source="trello", external_id="a")


def entry(**kw) -> TriageLedgerEntry:
    fields = {
        "card_id": CARD, "digest": "d1", "action": "keep", "state": "planned",
        "updated_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    return TriageLedgerEntry(**{**fields, **kw})


def make_ledger(path: Path) -> JsonTriageLedger:
    from chevuoi.infrastructure.config.settings import TriageConfig

    return JsonTriageLedger(make_config(triage=TriageConfig(ledger_path=path)))


class TestJsonTriageLedger:
    def test_broken_file_is_treated_as_empty(self, tmp_path):
        """台帳のファイルが壊れているとき、例外にせず空の台帳として続けること
        （台帳は真実源ではないので、失っても再判定のコストが増えるだけ）。"""
        path = tmp_path / "triage.json"
        path.write_text("{壊れている", encoding="utf-8")
        assert make_ledger(path).load() == {}

    def test_missing_file_is_empty(self, tmp_path):
        """台帳がまだ無いとき、空の台帳として読めること。"""
        assert make_ledger(tmp_path / "state" / "triage.json").load() == {}

    def test_recorded_entry_is_visible_to_a_new_run(self, tmp_path):
        """記録した判定結果が、次のラン（別インスタンス）から読めること。"""
        path = tmp_path / "triage.json"
        make_ledger(path).record(entry(reason="budget"))
        loaded = make_ledger(path).load()
        assert loaded["trello:a"].digest == "d1" and loaded["trello:a"].reason == "budget"

    def test_same_card_keeps_a_single_entry(self, tmp_path):
        """同じカードを 2 回記録したとき、最後の状態だけが残ること。"""
        path = tmp_path / "triage.json"
        ledger = make_ledger(path)
        ledger.record(entry(state="planned"))
        ledger.record(entry(state="applied"))
        loaded = make_ledger(path).load()
        assert list(loaded) == ["trello:a"] and loaded["trello:a"].state == "applied"
