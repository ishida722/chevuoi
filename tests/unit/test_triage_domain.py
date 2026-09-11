"""トリアージのドメイン（決定表・クラスタリング・類似度）のテスト。外部依存なし。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.entities.triage_judgment import TriageJudgment
from chevuoi.domain.entities.triage_plan import (
    LABEL_DUPLICATE,
    LABEL_NEEDS_INFO,
    LABEL_REVIEW,
    LABEL_STALE,
    TriagePlan,
    TriageReport,
)
from chevuoi.domain.services.similarity import normalize_for_similarity
from chevuoi.domain.services.triage_clustering import (
    build_clusters,
    choose_representative,
    find_duplicate_pairs,
)
from chevuoi.domain.services.triage_policy import DeterministicSignals, decide
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.strategies.similarity_factory import get_similarity_class
from chevuoi.infrastructure.strategies.trigram_similarity import TrigramSimilarity

EPOCH = datetime(2026, 9, 1, tzinfo=UTC)


def card(
    external_id: str,
    title: str = "MIRAI ログイン画面が落ちる",
    *,
    body: str = "本文",
    key: str = "k1",
    evidence: tuple[str, ...] = (),
    minutes: int = 0,
    base_commit: str = "",
) -> TriageCard:
    return TriageCard(
        id=CardId(source="trello", external_id=external_id),
        title=title,
        body=body,
        url=f"https://trello.com/c/{external_id}",
        created_at=EPOCH + timedelta(minutes=minutes),
        key=key,
        evidence=evidence,
        base_commit=base_commit,
    )


HIGH_DUPLICATE = TriageJudgment(verdict="duplicate", confidence="high", reason="同じ原因")
LOW_DUPLICATE = TriageJudgment(verdict="duplicate", confidence="low", reason="確信なし")
HIGH_RESOLVED = TriageJudgment(verdict="resolved", confidence="high", reason="既に直っている")


class TestDecisionTable:
    """decide() は「判定は LLM、行動は決定表」の行動側。表そのものを検証する。"""

    def test_non_target_card_is_skipped(self):
        """対象外（人間起票・settle 内・変化なし）のとき、行動は skip になること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(is_target=False),
            judgment=HIGH_DUPLICATE,
            representative=CardId(source="trello", external_id="b"),
            apply=True,
        )
        assert plan.action == "skip"

    def test_low_confidence_duplicate_is_not_merged(self):
        """重複判定の確信度が high でないとき、畳まずに人間が見るラベルに落ちること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(needs_judgment=True, body_is_empty=False),
            judgment=LOW_DUPLICATE,
            representative=CardId(source="trello", external_id="b"),
            apply=True,
        )
        assert plan.action == "label" and plan.labels == (LABEL_REVIEW,)

    def test_representative_itself_is_not_merged(self):
        """代表が自分自身のとき、自分を畳まないこと（クラスタが消えない）。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(body_is_empty=False, exact_title_duplicate=True),
            judgment=HIGH_DUPLICATE,
            representative=CardId(source="trello", external_id="a"),
            apply=True,
        )
        assert plan.action == "keep"

    def test_duplicate_is_downgraded_to_label_in_dry_run(self):
        """apply が偽のとき、merge は triage/duplicate ラベルに降格すること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(body_is_empty=False),
            judgment=HIGH_DUPLICATE,
            representative=CardId(source="trello", external_id="b"),
            apply=False,
        )
        assert plan.action == "label" and plan.labels == (LABEL_DUPLICATE,)
        assert plan.representative == CardId(source="trello", external_id="b")

    def test_exact_title_duplicate_merges_without_llm_judgment(self):
        """正規化タイトルが完全一致のとき、LLM 判定が無くても merge になること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(body_is_empty=False, exact_title_duplicate=True),
            representative=CardId(source="trello", external_id="b"),
            apply=True,
        )
        assert plan.action == "merge"

    def test_card_without_body_and_evidence_still_merges(self):
        """本文も evidence も無いカードでも、重複が確定していれば needs_info より
        merge が優先されること（決定表の行順が逆だと重複の畳み込みが働かない）。"""
        plan = decide(
            card("a", body=""),
            signals=DeterministicSignals(
                body_is_empty=True, has_evidence=False, exact_title_duplicate=True
            ),
            representative=CardId(source="trello", external_id="b"),
            apply=True,
        )
        assert plan.action == "merge"

    def test_needs_info_requires_empty_body_and_no_evidence(self):
        """本文が空でも evidence があれば needs_info にしないこと（条件は「かつ」）。"""
        plan = decide(
            card("a", body=""),
            signals=DeterministicSignals(body_is_empty=True, has_evidence=True),
        )
        assert plan.action == "keep"

    def test_empty_card_gets_needs_info_label(self):
        """本文が空かつ evidence も無いとき、triage/needs-info を付けること。"""
        plan = decide(
            card("a", body=""),
            signals=DeterministicSignals(body_is_empty=True, has_evidence=False),
        )
        assert plan.action == "label" and plan.labels == (LABEL_NEEDS_INFO,)

    def test_unasked_card_is_not_labeled_review(self):
        """第 2 層の照会が不要だったカードには review ラベルを付けないこと
        （全件に付くラベルは読む順序の手がかりにならない）。"""
        plan = decide(card("a"), signals=DeterministicSignals(body_is_empty=False))
        assert plan.action == "keep" and plan.labels == ()

    def test_budget_exceeded_card_is_labeled_review(self):
        """照会が必要だったのに判定が無い（予算超過）とき、triage/review を付けること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(body_is_empty=False, needs_judgment=True),
            judgment=None,
        )
        assert plan.action == "label" and plan.labels == (LABEL_REVIEW,)

    def test_deferred_judgment_is_labeled_review_even_with_another_verdict(self):
        """別の判定（重複ではない）が付いていても、必要な照会が予算で届かなかったときは
        triage/review を付けること（判定済みに見せかけて取りこぼさない）。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(
                body_is_empty=False, has_evidence=True,
                needs_judgment=True, judgment_deferred=True,
            ),
            judgment=TriageJudgment(verdict="distinct", confidence="high", reason="別の話"),
            apply=True,
        )
        assert plan.action == "label" and plan.labels == (LABEL_REVIEW,)

    def test_resolved_without_deterministic_signal_is_not_archived(self):
        """起票時コミット以降そのパスが変わっていないとき、resolved 判定でも
        アーカイブしないこと（解決しているはずがない）。"""
        plan = decide(
            card("a", base_commit="c0"),
            signals=DeterministicSignals(
                body_is_empty=False, has_evidence=True, unchanged_since_issue=True
            ),
            judgment=HIGH_RESOLVED,
            apply=True,
        )
        assert plan.action == "keep"

    def test_resolved_with_signal_archives_when_applying(self):
        """パスがベースに無く resolved かつ high のとき、アーカイブになること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(
                body_is_empty=False, has_evidence=True, path_missing_in_base=True
            ),
            judgment=HIGH_RESOLVED,
            apply=True,
        )
        assert plan.action == "archive" and plan.reason == "既に直っている"

    def test_resolved_is_downgraded_to_label_in_dry_run(self):
        """apply が偽のとき、archive は triage/stale ラベルに降格すること。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(
                body_is_empty=False, has_evidence=True, path_missing_in_base=True
            ),
            judgment=HIGH_RESOLVED,
            apply=False,
        )
        assert plan.action == "label" and plan.labels == (LABEL_STALE,)

    def test_low_confidence_resolved_is_not_archived(self):
        """解決済み判定の確信度が high でないとき、アーカイブしないこと。"""
        plan = decide(
            card("a"),
            signals=DeterministicSignals(
                body_is_empty=False, has_evidence=True, needs_judgment=True,
                path_missing_in_base=True,
            ),
            judgment=TriageJudgment(verdict="resolved", confidence="low"),
            apply=True,
        )
        assert plan.action == "label" and plan.labels == (LABEL_REVIEW,)


class TestClustering:
    def test_pairs_are_not_made_across_projects(self):
        """プロジェクトタグが異なるカード同士は、内容が同じでもペアにしないこと。"""
        pairs = find_duplicate_pairs(
            [card("a", "MIRAI 同じ話"), card("b", "SSC 同じ話")],
            TrigramSimilarity(),
            threshold=0.1,
        )
        assert pairs == []

    def test_exact_title_pair_is_kept_even_below_threshold(self):
        """正規化タイトルが完全一致するペアは、閾値を上げても候補に残ること。"""
        pairs = find_duplicate_pairs(
            [card("a", "MIRAI 同じ話", evidence=("x.py:1",)), card("b", "MIRAI 同じ話")],
            TrigramSimilarity(),
            threshold=0.99,
        )
        assert len(pairs) == 1 and pairs[0].exact_title is True

    def test_pairs_are_ordered_deterministically(self):
        """同じスコアのペアが並ぶとき、ID の昇順で決定的に並ぶこと（入力順に依存しない）。"""
        cards = [card("c", "MIRAI 同じ話"), card("a", "MIRAI 同じ話"), card("b", "MIRAI 同じ話")]
        pairs = find_duplicate_pairs(cards, TrigramSimilarity(), threshold=0.5)
        keys = [(str(p.card_id), str(p.other_id)) for p in pairs]
        assert keys == sorted(keys)
        reversed_pairs = find_duplicate_pairs(
            list(reversed(cards)), TrigramSimilarity(), threshold=0.5
        )
        assert [(str(p.card_id), str(p.other_id)) for p in reversed_pairs] == keys

    def test_representative_prefers_human_issued_card(self):
        """人間が書いたカードがクラスタにあるとき、作成時刻に関わらず代表になること。"""
        human = card("h", key="", minutes=60)
        auto = card("a", minutes=0)
        assert choose_representative([auto, human]).id == human.id

    def test_representative_is_the_oldest_auto_card(self):
        """自動起票だけのクラスタでは、最も古いカードが代表になること。"""
        old, new = card("z", minutes=0), card("a", minutes=10)
        assert choose_representative([new, old]).id == old.id

    def test_clusters_merge_transitively_and_ignore_singletons(self):
        """a=b, b=c と確定したとき 3 枚が同じ代表を持ち、単独のカードは含まれないこと。"""
        cards = [card("a", minutes=0), card("b", minutes=1), card("c", minutes=2), card("d")]
        confirmed = [(cards[0].id, cards[1].id), (cards[1].id, cards[2].id)]
        representatives = build_clusters(cards, confirmed)
        assert {k: str(v.id) for k, v in representatives.items()} == {
            "trello:a": "trello:a", "trello:b": "trello:a", "trello:c": "trello:a"
        }


class TestSimilarity:
    def test_project_tag_is_dropped_before_comparison(self):
        """比較用の正規化はプロジェクトタグを落とすこと（タグは類似度を一様に押し上げる）。"""
        assert normalize_for_similarity(card("a", "MIRAI ログイン修正")) == "ログイン修正"

    def test_identical_titles_score_one(self):
        """同じ内容のカードは類似度 1.0 になること。"""
        assert TrigramSimilarity().score(card("a"), card("b")) == 1.0

    def test_unrelated_titles_score_below_similar_ones(self):
        """無関係なタイトルの類似度は、言い換えのタイトルより低くなること。"""
        base = card("a", "MIRAI ログイン画面が落ちる")
        similar = card("b", "MIRAI ログイン画面が落ちます")
        unrelated = card("c", "MIRAI 請求書 PDF の余白を直す")
        similarity = TrigramSimilarity()
        assert similarity.score(base, unrelated) < similarity.score(base, similar)

    def test_shared_evidence_raises_the_score(self):
        """同じ evidence を指すカード同士は、指さないカード同士より類似度が高いこと。"""
        similarity = TrigramSimilarity()
        a = card("a", "MIRAI 落ちる", evidence=("src/foo.py:12",))
        b = card("b", "MIRAI 直したい", evidence=("src/foo.py:98",))
        c = card("c", "MIRAI 直したい", evidence=("src/bar.py:1",))
        assert similarity.score(a, b) > similarity.score(a, c)

    def test_unknown_similarity_name_falls_back_to_trigram(self):
        """設定に未知の類似度名が書かれていても、既定の実装に落ちて動くこと。"""
        assert get_similarity_class("embedding") is TrigramSimilarity


class TestTriageCard:
    def test_evidence_paths_drop_line_numbers(self):
        """evidence の行番号はパス比較に使わないこと（同じ箇所を別の行で指しても同じ）。"""
        assert card("a", evidence=("src/foo.py:12", "src/foo.py:98")).evidence_paths == (
            "src/foo.py",
        )

    def test_digest_changes_when_body_changes(self):
        """本文が変われば digest が変わること（再判定の要否がこれで決まる）。"""
        assert card("a", body="x").digest() != card("a", body="y").digest()

    def test_digest_ignores_whitespace_and_case(self):
        """空白と大文字小文字だけの違いでは digest が変わらないこと。"""
        assert card("a", body="Foo  Bar").digest() == card("a", body="foo bar").digest()

    def test_card_without_footer_key_is_human_issued(self):
        """フッターの冪等キーが無いカードは人間起票として扱うこと。"""
        assert card("a", key="").is_auto_issued is False


class TestReportText:
    """dry-run の出力は、人間が計画を読んで較正するための唯一の材料。"""

    def report(self) -> TriageReport:
        return TriageReport(
            planned=[
                TriagePlan(card_id=CardId(source="trello", external_id="a"), action="skip"),
                TriagePlan(card_id=CardId(source="trello", external_id="b"), action="keep"),
                TriagePlan(
                    card_id=CardId(source="trello", external_id="c"),
                    action="merge",
                    representative=CardId(source="trello", external_id="b"),
                    reason="同じ原因",
                ),
            ],
            judgments_used=3,
            pairs_found=7,
        )

    def test_merge_plan_shows_where_the_card_goes(self):
        """畳む計画は、どのカードをどこへ集約するのかが読める形で出ること。"""
        text = self.report().to_text(dry_run=True)
        assert "merge: trello:c -> trello:b" in text
        assert "同じ原因" in text

    def test_skipped_cards_are_not_listed(self):
        """対象外のカードは出力に並べないこと（読む量を増やさない）。"""
        assert "trello:a" not in self.report().to_text(dry_run=True)

    def test_pair_and_judgment_counts_are_shown(self):
        """閾値を較正できるよう、候補ペア数と LLM 判定回数を出すこと。"""
        summary = [line for line in self.report().to_text(dry_run=True).splitlines()
                   if "ペア" in line]
        assert summary, "候補ペア数の行が出力に無い"
        assert "7" in summary[0] and "3" in summary[0]

    def test_failures_are_shown_when_applying(self):
        """適用時は、失敗したカードとその理由を出すこと。"""
        report = self.report()
        report.failed.append((CardId(source="trello", external_id="d"), "API エラー"))
        text = report.to_text(dry_run=False)
        assert "trello:d" in text and "API エラー" in text
