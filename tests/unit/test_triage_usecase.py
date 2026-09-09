"""TriageUsecase のテスト。プロセス境界（Trello / claude / git / 台帳 / 時刻）だけを差し替える。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from chevuoi.application.usecases.triage_usecase import TriageUsecase
from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.entities.triage_judgment import TriageJudgment
from chevuoi.domain.entities.triage_plan import (
    LABEL_DUPLICATE,
    LABEL_REVIEW,
    TriagePlan,
)
from chevuoi.domain.exceptions import TriageError
from chevuoi.domain.ports.triage_card_repository import TriageCardRepository
from chevuoi.domain.ports.triage_judge import TriageJudge
from chevuoi.domain.ports.triage_ledger import TriageLedger, TriageLedgerEntry
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import (
    AppConfig,
    ProjectConfig,
    TrelloConfig,
    TriageConfig,
)
from chevuoi.infrastructure.strategies.trigram_similarity import TrigramSimilarity
from tests.unit.fakes import FakeInspector

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def card(
    external_id: str,
    title: str = "MIRAI ログイン画面が落ちる",
    *,
    body: str = "本文",
    key: str = "auto",
    evidence: tuple[str, ...] = (),
    base_commit: str = "",
    age_minutes: int = 60,
) -> TriageCard:
    return TriageCard(
        id=CardId(source="trello", external_id=external_id),
        title=title,
        body=body,
        url=f"https://trello.com/c/{external_id}",
        created_at=NOW - timedelta(minutes=age_minutes),
        key=key,
        evidence=evidence,
        base_commit=base_commit,
    )


class FakeTriageRepo(TriageCardRepository):
    """カードの集合をインメモリで持つリポジトリ。外部作用を記録する。"""

    def __init__(self, cards: list[TriageCard], *, existing_comments: dict[str, str] | None = None,
                 archive_errors: tuple[str, ...] = ()) -> None:
        self.cards = cards
        self.comments: dict[str, list[str]] = {k: [v] for k, v in (existing_comments or {}).items()}
        self.labels: dict[str, list[str]] = {}
        self.archived: list[str] = []
        # 実物の API と同じく、失敗するのは特定のカードだけ
        self._archive_errors = set(archive_errors)

    def fetch_open(self) -> list[TriageCard]:
        return list(self.cards)

    def add_comment(self, card_id: CardId, text: str) -> None:
        self.comments.setdefault(str(card_id), []).append(text)

    def has_comment(self, card_id: CardId, digest: str) -> bool:
        return any(digest in text for text in self.comments.get(str(card_id), []))

    def add_label(self, card_id: CardId, label: str) -> None:
        self.labels.setdefault(str(card_id), []).append(label)

    def archive(self, card_id: CardId) -> None:
        if str(card_id) in self._archive_errors:
            raise TriageError(f"API エラー: {card_id}")
        self.archived.append(str(card_id))

    @property
    def effects(self) -> int:
        return len(self.comments) + len(self.labels) + len(self.archived)


class ScriptedJudge(TriageJudge):
    """決められた判定を返す判定器。呼ばれた回数と相手を記録する。"""

    def __init__(
        self,
        duplicate: TriageJudgment | None = None,
        resolved: TriageJudgment | None = None,
    ) -> None:
        self._duplicate = duplicate or TriageJudgment()
        self._resolved = resolved or TriageJudgment()
        self.duplicate_calls: list[tuple[str, str]] = []
        self.resolved_calls: list[tuple[str, Path]] = []

    def judge_duplicate(self, card: TriageCard, other: TriageCard) -> TriageJudgment:
        self.duplicate_calls.append((str(card.id), str(other.id)))
        return self._duplicate

    def judge_resolved(self, card: TriageCard, *, cwd: Path) -> TriageJudgment:
        self.resolved_calls.append((str(card.id), cwd))
        return self._resolved


class FakeLedger(TriageLedger):
    def __init__(self, entries: dict[str, TriageLedgerEntry] | None = None) -> None:
        self.entries = dict(entries or {})
        self.recorded: list[TriageLedgerEntry] = []

    def load(self) -> dict[str, TriageLedgerEntry]:
        return dict(self.entries)

    def record(self, entry: TriageLedgerEntry) -> None:
        self.entries[str(entry.card_id)] = entry
        self.recorded.append(entry)


def make_config(**triage) -> AppConfig:
    """閾値は明示する。較正で既定値が動いても、テストの意味が変わらないようにする。"""
    triage.setdefault("similarity_threshold", 0.55)
    return AppConfig(
        trello=TrelloConfig(
            api_key="k", api_token="t", ready_list_id="r",
            in_progress_list_id="p", in_review_list_id="v", inbox_list_id="inbox",
        ),
        projects={"MIRAI": ProjectConfig(path=Path("/repo/mirai"))},
        worktree_root=Path("/tmp/worktrees"),
        triage=TriageConfig(**triage),
    )


def run(
    cards: list[TriageCard],
    *,
    judge: ScriptedJudge | None = None,
    repo: FakeTriageRepo | None = None,
    ledger: FakeLedger | None = None,
    inspector: FakeInspector | None = None,
    apply: bool = False,
    config: AppConfig | None = None,
    **kwargs,
):
    repo = repo or FakeTriageRepo(cards)
    judge = judge or ScriptedJudge()
    ledger = ledger or FakeLedger()
    inspector = inspector or FakeInspector()
    usecase = TriageUsecase(
        repo, judge, TrigramSimilarity(), inspector, ledger, config or make_config()
    )
    report = usecase.execute(apply=apply, now=NOW, **kwargs)
    return report, repo, judge, ledger, inspector


def plan_for(report, external_id: str) -> TriagePlan:
    return next(p for p in report.planned if p.card_id.external_id == external_id)


class TestTargetSelection:
    def test_recently_created_card_is_not_touched(self):
        """settle_minutes 以内に作られたカードのとき、判定も外部作用も行わないこと
        （実行中のランが今まさに起票したカードを触らない）。"""
        cards = [card("a", age_minutes=1), card("b", age_minutes=1)]
        report, repo, judge, ledger, _ = run(cards, apply=True)
        assert [p.action for p in report.planned] == ["skip", "skip"]
        assert judge.duplicate_calls == [] and repo.effects == 0 and ledger.recorded == []

    def test_card_exactly_at_the_settle_boundary_is_a_target(self):
        """作成から settle_minutes ちょうど経過したカードは対象になること
        （条件は「以上経過している」。境界を跨ぐ向きを固定する）。"""
        report, _, _, _, _ = run([card("a", age_minutes=10), card("b", age_minutes=10)])
        assert plan_for(report, "b").action != "skip"

    def test_human_issued_card_is_never_modified(self):
        """人間が書いたカード（フッターなし）のとき、代表にはなるが自身は畳まれないこと。"""
        cards = [card("h", key=""), card("a")]
        report, repo, _, _, _ = run(cards, apply=True)
        assert plan_for(report, "h").action == "skip"
        assert plan_for(report, "a").action == "merge"
        assert repo.archived == ["trello:a"]

    def test_unchanged_card_is_not_rejudged(self):
        """内容が前回から変わっていないカードのとき、再判定しないこと（コスト）。"""
        target = card("a")
        ledger = FakeLedger(
            {
                "trello:a": TriageLedgerEntry(
                    card_id=target.id, digest=target.digest(), action="keep",
                    state="applied", updated_at=NOW,
                )
            }
        )
        report, _, judge, _, _ = run([target, card("b")], ledger=ledger)
        assert plan_for(report, "a").action == "skip"
        assert judge.duplicate_calls == []

    def test_budget_exceeded_card_is_rejudged_next_run(self):
        """前回が予算超過だったカードは、内容が変わっていなくても再判定すること。"""
        target = card("a")
        ledger = FakeLedger(
            {
                "trello:a": TriageLedgerEntry(
                    card_id=target.id, digest=target.digest(), action="label",
                    reason="budget", state="applied", updated_at=NOW,
                )
            }
        )
        report, _, _, _, _ = run([target, card("b")], ledger=ledger)
        assert plan_for(report, "a").action != "skip"


class TestDryRun:
    def test_dry_run_performs_no_external_effects(self):
        """apply が偽のとき、コメント・アーカイブ・ラベルのいずれも行わないこと。"""
        report, repo, _, _, _ = run([card("a"), card("b")], apply=False)
        assert repo.effects == 0
        assert plan_for(report, "b").labels == (LABEL_DUPLICATE,)

    def test_pairs_found_is_reported(self):
        """閾値を超えた候補ペアの数を報告すること（閾値較正の材料）。"""
        report, _, _, _, _ = run([card("a"), card("b")])
        assert report.pairs_found == 1


class TestApply:
    def test_merge_comments_both_cards_and_archives_the_duplicate(self):
        """--apply で重複を畳むとき、代表と自分にコメントを残してからアーカイブすること。"""
        _, repo, _, _, _ = run([card("a"), card("b")], apply=True)
        assert repo.archived == ["trello:b"]
        assert "https://trello.com/c/b" in repo.comments["trello:a"][0]  # 集約元を追える
        assert repo.comments["trello:b"]

    def test_triage_comments_start_with_the_bot_mark(self):
        """トリアージのコメントは 1 行目が 🤖 で始まること
        （人間の追加指示として次のランに読まれない）。"""
        _, repo, _, _, _ = run([card("a"), card("b")], apply=True)
        posted = [text for texts in repo.comments.values() for text in texts]
        assert posted and all(text.startswith("🤖 triage:") for text in posted)

    def test_merge_comment_is_not_posted_twice(self):
        """代表カードに同じ digest のコメントが既にあるとき、再投稿しないこと（冪等）。"""
        duplicate = card("b")
        repo = FakeTriageRepo(
            [card("a"), duplicate],
            existing_comments={"trello:a": f"🤖 triage: 済み digest={duplicate.digest()}"},
        )
        _, repo, _, _, _ = run([], repo=repo, apply=True)
        assert len(repo.comments["trello:a"]) == 1
        assert repo.archived == ["trello:b"]

    def test_failure_of_one_card_does_not_stop_the_run(self):
        """1 枚の適用に失敗しても、後続のカードの適用は続けること。

        失敗したカードのあとに外部作用を持つカードを置いておく。ここで打ち切ると
        c が適用されないので、continue を break に変えるとこのテストが落ちる。
        """
        repo = FakeTriageRepo([card("a"), card("b"), card("c")], archive_errors=("trello:b",))
        report, repo, _, _, _ = run([], repo=repo, apply=True)
        assert [str(c) for c, _ in report.failed] == ["trello:b"]
        assert repo.archived == ["trello:c"]
        assert [str(c) for c in report.applied] == ["trello:c"]

    def test_label_is_applied_only_when_applying(self):
        """--apply のとき、ラベル計画は実際にラベル付けとして実行されること。"""
        _, repo, _, _, _ = run([card("a", body="", key="auto")], apply=True)
        assert repo.labels == {"trello:a": ["triage/needs-info"]}


class TestResumeAcrossRuns:
    def test_dry_run_does_not_disable_the_next_apply_run(self):
        """dry-run を 1 度回したあとでも、--apply が計画どおり適用されること。

        仕様は「dry-run で較正してから --apply を使う」なので、dry-run が台帳を
        「処理済み」で埋めると機能そのものが死ぬ。
        """
        ledger = FakeLedger()
        cards = [card("a"), card("b")]
        _, dry, _, _, _ = run([], repo=FakeTriageRepo(cards), ledger=ledger, apply=False)
        assert dry.effects == 0
        _, applied, _, _, _ = run([], repo=FakeTriageRepo(cards), ledger=ledger, apply=True)
        assert applied.archived == ["trello:b"]

    def test_failed_application_is_retried_in_the_next_run(self):
        """適用に失敗したカードは、次のランで再開されること
        （台帳に計画だけ残ったカードを取り残さない）。"""
        ledger = FakeLedger()
        cards = [card("a"), card("b")]
        failing = FakeTriageRepo(cards, archive_errors=("trello:b",))
        report, _, _, _, _ = run([], repo=failing, ledger=ledger, apply=True)
        assert [str(c) for c, _ in report.failed] == ["trello:b"]
        _, retried, _, _, _ = run([], repo=FakeTriageRepo(cards), ledger=ledger, apply=True)
        assert retried.archived == ["trello:b"]

    def test_applied_card_is_not_touched_again(self):
        """適用が済んだカードは、次のランで再度触らないこと（冪等・コスト）。"""
        ledger = FakeLedger()
        cards = [card("a"), card("b")]
        run([], repo=FakeTriageRepo(cards), ledger=ledger, apply=True)
        _, second, _, _, _ = run([], repo=FakeTriageRepo(cards), ledger=ledger, apply=True)
        assert second.archived == []


class TestDuplicateJudgment:
    def test_exact_title_duplicate_does_not_call_the_judge(self):
        """正規化タイトルが完全一致のとき、LLM に照会しないこと（第 1 層で確定）。"""
        _, _, judge, _, _ = run([card("a"), card("b")])
        assert judge.duplicate_calls == []

    def test_similar_titles_are_asked_to_the_judge(self):
        """閾値を超えるだけの類似ペアのとき、LLM に 1 回だけ照会すること。"""
        cards = [card("a", "MIRAI ログイン画面が落ちる"), card("b", "MIRAI ログイン画面が落ちます")]
        _, _, judge, _, _ = run(cards)
        assert judge.duplicate_calls == [("trello:a", "trello:b")]

    def test_abstained_judgment_leaves_the_card_for_a_human(self):
        """判定器が棄権したとき、畳まずに triage/review ラベルの計画にすること。"""
        cards = [card("a", "MIRAI ログイン画面が落ちる"), card("b", "MIRAI ログイン画面が落ちます")]
        judge = ScriptedJudge(duplicate=TriageJudgment(verdict="duplicate", confidence="low"))
        report, repo, _, _, _ = run(cards, judge=judge, apply=True)
        assert plan_for(report, "b").labels == (LABEL_REVIEW,)
        assert repo.archived == []

    def test_abstained_pair_is_not_treated_as_a_cluster(self):
        """重複判定が棄権したとき、そのペアを重複として確定させないこと。

        確定させると「畳む予定のカード」として鮮度判定の対象から外れ、棄権したはずの
        カードが黙って判定されないまま残る。
        """
        cards = [
            card("a", "MIRAI ログイン画面が落ちる", evidence=("src/foo.py:1",)),
            card("b", "MIRAI ログイン画面が落ちます", evidence=("src/foo.py:9",)),
        ]
        judge = ScriptedJudge(
            duplicate=TriageJudgment(verdict="duplicate", confidence="low", reason="確信なし"),
            resolved=TriageJudgment(verdict="resolved", confidence="high", reason="消えている"),
        )
        _, _, judge, _, _ = run(
            cards, judge=judge, inspector=FakeInspector(missing_paths=("src/foo.py",))
        )
        assert sorted(c[0] for c in judge.resolved_calls) == ["trello:a", "trello:b"]

    def test_pairs_over_the_pair_budget_are_carried_over(self):
        """候補ペアの上限で今回照会しないペアは、黙って捨てずに review ラベルと
        台帳の budget で次のランへ持ち越すこと。"""
        cards = [
            card("a", "MIRAI ログイン画面が落ちる"),
            card("b", "MIRAI ログイン画面が落ちます"),
            card("c", "MIRAI 請求書 PDF の余白がずれる"),
            card("d", "MIRAI 請求書 PDF の余白がずれます"),
        ]
        judge = ScriptedJudge(
            duplicate=TriageJudgment(verdict="distinct", confidence="high", reason="別の話")
        )
        report, _, judge, ledger, _ = run(
            cards, judge=judge, config=make_config(max_pairs_per_run=1)
        )
        assert len(judge.duplicate_calls) == 1
        assert report.budget_exceeded is True
        carried = [p.card_id.external_id for p in report.planned if p.labels == (LABEL_REVIEW,)]
        assert len(carried) == 2
        assert {ledger.entries[f"trello:{x}"].reason for x in carried} == {"budget"}

    def test_exact_title_pairs_do_not_consume_the_pair_budget(self):
        """LLM 照会の要らない完全一致の重複は、候補ペアの上限で落とさないこと。"""
        _, repo, judge, _, _ = run(
            [card("a"), card("b")], config=make_config(max_pairs_per_run=0), apply=True
        )
        assert judge.duplicate_calls == []
        assert repo.archived == ["trello:b"]

    def test_budget_exceeded_is_reported_and_recorded(self):
        """判定予算を使い切ったとき、budget_exceeded を立て、台帳に budget を残すこと
        （次回のランで拾い直せる）。"""
        cards = [card("a", "MIRAI ログイン画面が落ちる"), card("b", "MIRAI ログイン画面が落ちます")]
        report, _, judge, ledger, _ = run(cards, config=make_config(max_judgments_per_run=0))
        assert report.budget_exceeded is True and judge.duplicate_calls == []
        assert plan_for(report, "b").labels == (LABEL_REVIEW,)
        assert {e.reason for e in ledger.recorded} == {"budget"}


class TestResolvedJudgment:
    def base_cards(self, **kw) -> list[TriageCard]:
        return [card("a", "MIRAI 落ちる", evidence=("src/foo.py:12",), **kw)]

    def test_unchanged_path_is_not_asked(self):
        """起票時コミット以降その箇所が変わっていないとき、LLM に照会しないこと。"""
        cards = self.base_cards(base_commit="c0")
        _, _, judge, _, _ = run(cards, inspector=FakeInspector(changed_paths=()))
        assert judge.resolved_calls == []

    def test_changed_path_is_asked_in_the_base_checkout(self):
        """起票時から変わった箇所を持つカードは、ベースのチェックアウトを cwd に照会すること。"""
        cards = self.base_cards(base_commit="c0")
        inspector = FakeInspector(changed_paths=("src/foo.py",), checkout=Path("/tmp/base"))
        _, _, judge, _, _ = run(cards, inspector=inspector)
        assert judge.resolved_calls == [("trello:a", Path("/tmp/base"))]

    def test_card_without_base_commit_is_asked_only_when_path_is_gone(self):
        """起票時コミットを持たない古いカードは、パスがベースに無いときだけ照会すること。"""
        _, _, judge, _, _ = run(self.base_cards(), inspector=FakeInspector(changed_paths=("src/foo.py",)))
        assert judge.resolved_calls == []
        _, _, judge, _, _ = run(
            self.base_cards(), inspector=FakeInspector(missing_paths=("src/foo.py",))
        )
        assert [c[0] for c in judge.resolved_calls] == ["trello:a"]

    def test_fetch_failure_skips_freshness_but_keeps_duplicates(self):
        """ベース参照を更新できないプロジェクトでは鮮度判定を丸ごと見送り、
        重複の畳み込みは続けること。"""
        cards = [
            card("a", "MIRAI 落ちる", evidence=("src/foo.py:12",), base_commit="c0"),
            card("b", "MIRAI 落ちる", evidence=("src/foo.py:12",), base_commit="c0"),
        ]
        inspector = FakeInspector(refreshed=False, missing_paths=("src/foo.py",))
        report, _, judge, _, inspector = run(cards, inspector=inspector, apply=True)
        assert judge.resolved_calls == [] and inspector.checkouts == []
        assert plan_for(report, "b").action == "merge"

    def test_unknown_project_tag_skips_freshness(self):
        """タグに対応するプロジェクトが設定に無いとき、鮮度判定を行わないこと。"""
        cards = [card("a", "OTHER 落ちる", evidence=("src/foo.py:12",), base_commit="c0")]
        _, _, judge, _, inspector = run(cards, inspector=FakeInspector(changed_paths=("src/foo.py",)))
        assert judge.resolved_calls == [] and inspector.refreshed == []

    def test_duplicate_judgments_consume_the_budget_first(self):
        """予算が 1 回しかないとき、重複判定を先に行い鮮度判定は次回へ回すこと。"""
        cards = [
            card("a", "MIRAI ログイン画面が落ちる", evidence=("src/foo.py:1",), base_commit="c0"),
            card("b", "MIRAI ログイン画面が落ちます", evidence=("src/foo.py:9",), base_commit="c0"),
        ]
        inspector = FakeInspector(changed_paths=("src/foo.py",))
        report, _, judge, _, _ = run(
            cards, inspector=inspector, config=make_config(max_judgments_per_run=1)
        )
        assert len(judge.duplicate_calls) == 1 and judge.resolved_calls == []
        assert report.budget_exceeded is True

    def test_distinct_verdict_does_not_hide_the_freshness_verdict(self):
        """重複ではないと判定されたカードでも、鮮度判定の結果でアーカイブできること
        （2 種類の判定を 1 つの箱に混ぜると、先の判定があとの判定を握り潰す）。"""
        cards = [
            card("a", "MIRAI ログイン画面が落ちる", evidence=("src/foo.py:1",)),
            card("b", "MIRAI ログイン画面が落ちます", evidence=("src/foo.py:9",)),
        ]
        judge = ScriptedJudge(
            duplicate=TriageJudgment(verdict="distinct", confidence="high", reason="別の話"),
            resolved=TriageJudgment(verdict="resolved", confidence="high", reason="消えている"),
        )
        _, repo, judge, _, _ = run(
            cards, judge=judge, inspector=FakeInspector(missing_paths=("src/foo.py",)), apply=True
        )
        assert len(judge.resolved_calls) == 2
        assert sorted(repo.archived) == ["trello:a", "trello:b"]

    def test_deferred_freshness_judgment_is_carried_over(self):
        """重複判定が済んでいるカードでも、鮮度判定が予算で届かなかったときは
        review ラベルを付け、台帳に budget を残して次回に回すこと。"""
        cards = [
            card("a", "MIRAI ログイン画面が落ちる", evidence=("src/foo.py:1",)),
            card("b", "MIRAI ログイン画面が落ちます", evidence=("src/foo.py:9",)),
        ]
        judge = ScriptedJudge(
            duplicate=TriageJudgment(verdict="distinct", confidence="high", reason="別の話")
        )
        report, _, judge, ledger, _ = run(
            cards, judge=judge, inspector=FakeInspector(missing_paths=("src/foo.py",)),
            config=make_config(max_judgments_per_run=1),
        )
        assert len(judge.duplicate_calls) == 1 and judge.resolved_calls == []
        assert plan_for(report, "a").labels == (LABEL_REVIEW,)
        assert ledger.entries["trello:a"].reason == "budget"

    def test_high_confidence_resolved_card_is_archived(self):
        """パスが消えたカードを resolved かつ high と判定したとき、理由を残して
        アーカイブすること。"""
        cards = self.base_cards()
        judge = ScriptedJudge(
            resolved=TriageJudgment(verdict="resolved", confidence="high", reason="消えている")
        )
        _, repo, _, _, _ = run(
            cards, judge=judge, inspector=FakeInspector(missing_paths=("src/foo.py",)), apply=True
        )
        assert repo.archived == ["trello:a"]
        assert "消えている" in repo.comments["trello:a"][0]


class TestFilters:
    def test_project_option_limits_the_run(self):
        """--project でタグを指定したとき、そのタグのカードだけを読むこと。"""
        cards = [card("a", "MIRAI 落ちる"), card("b", "SSC 落ちる")]
        report, _, _, _, _ = run(cards, project="mirai")
        assert [p.card_id.external_id for p in report.planned] == ["a"]

    def test_project_tag_lookup_ignores_case(self):
        """カードのタグと設定のキーは大文字小文字を無視して照合すること
        （ProcessCardUsecase.resolve_project と同じ規則）。"""
        cards = [card("a", "mirai 落ちる", evidence=("src/foo.py:1",))]
        _, _, judge, _, inspector = run(
            cards, inspector=FakeInspector(missing_paths=("src/foo.py",))
        )
        assert [str(p.repo_path) for p in inspector.refreshed] == ["/repo/mirai"]
        assert [c[0] for c in judge.resolved_calls] == ["trello:a"]

    def test_limit_option_caps_the_cards(self):
        """--limit を指定したとき、読むカード数がその件数で打ち切られること。"""
        report, _, _, _, _ = run([card("a"), card("b"), card("c")], limit=2)
        assert len(report.planned) == 2
