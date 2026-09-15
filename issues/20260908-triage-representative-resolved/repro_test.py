"""代表カード自身が「解決済み」と判定されたときの挙動を観測する再現テスト。

リポジトリのソースは変更しない。tests/unit/test_triage_usecase.py の
テスト用の部品（FakeTriageRepo / ScriptedJudge / run など）をそのまま借りて、
仕様に定めが無いケースだけを走らせる。

実行方法（実装ブランチ chevuoi/trello-qNH3k8sL のツリーを /tmp に展開して実行する）:

    rm -rf /tmp/triage-repro && mkdir -p /tmp/triage-repro
    git archive chevuoi/trello-qNH3k8sL | tar -x -C /tmp/triage-repro
    cp issues/20260908-triage-representative-resolved/repro_test.py \
       /tmp/triage-repro/tests/unit/test_repro_representative_resolved.py
    cd /tmp/triage-repro && uv run pytest tests/unit/test_repro_representative_resolved.py -s
"""

from __future__ import annotations

from chevuoi.domain.entities.triage_judgment import TriageJudgment
from chevuoi.domain.services.triage_clustering import find_duplicate_pairs
from chevuoi.infrastructure.strategies.trigram_similarity import TrigramSimilarity
from tests.unit.fakes import FakeInspector
from tests.unit.test_triage_usecase import (
    FakeLedger,
    FakeTriageRepo,
    ScriptedJudge,
    card,
    make_config,
    plan_for,
    run,
)

RESOLVED = TriageJudgment(verdict="resolved", confidence="high", reason="ベースで修正済み")
DUPLICATE = TriageJudgment(verdict="duplicate", confidence="high", reason="同一の不具合")


def _cluster():
    """正規化タイトルが完全一致する 2 枚。古い方（rep）が代表になる。

    代表が鮮度判定に載るためには evidence が要る。base_commit を持たせずに
    パスをベースから消しておくと _should_judge_resolved が真になる。
    """
    rep = card("rep", evidence=("src/a.py:1",), age_minutes=120)
    dup = card("dup", evidence=("src/a.py:1",), age_minutes=60)
    return rep, dup


def test_A_apply_representative_is_archived_while_duplicates_merge_into_it():
    """--apply: 代表が archive、重複が代表へ merge。クラスタ全体が Inbox から消える。"""
    rep, dup = _cluster()
    repo = FakeTriageRepo([rep, dup])
    report, repo, judge, ledger, _ = run(
        [rep, dup],
        repo=repo,
        judge=ScriptedJudge(resolved=RESOLVED),
        inspector=FakeInspector(missing_paths=("src/a.py",)),
        apply=True,
    )
    print("\n[A] plans:", [(p.card_id.external_id, p.action) for p in report.planned])
    print("[A] archived:", repo.archived)
    for cid, texts in repo.comments.items():
        for t in texts:
            print(f"[A] comment on {cid}: {t.splitlines()[0]}")
    assert plan_for(report, "rep").action == "archive"
    assert plan_for(report, "dup").action == "merge"
    # 代表が先にアーカイブされ、そのあとに集約コメントが載る
    assert repo.archived == ["trello:rep", "trello:dup"]
    # 代表への集約コメントは投稿されない。digest が rep 自身の archive コメントと衝突し、
    # has_comment による冪等判定が働いてしまうため（範囲外の別問題）
    assert not any("集約しました" in t for t in repo.comments["trello:rep"])


def test_B_dry_run_labels_contradict_each_other():
    """dry-run: 代表に triage/stale、重複に triage/duplicate。レポート上で矛盾が見える。"""
    rep, dup = _cluster()
    report, repo, _, _, _ = run(
        [rep, dup],
        judge=ScriptedJudge(resolved=RESOLVED),
        inspector=FakeInspector(missing_paths=("src/a.py",)),
        apply=False,
    )
    print("\n[B] plans:", [(p.card_id.external_id, p.action, p.labels) for p in report.planned])
    assert plan_for(report, "rep").labels == ("triage/stale",)
    assert plan_for(report, "dup").labels == ("triage/duplicate",)


def test_C_llm_confirmed_cluster_swallows_the_resolved_verdict():
    """LLM 判定で確定したクラスタでは、_pick_judgment が重複判定を優先して
    代表の resolved 判定が握り潰され、代表は keep になる（同じ状況で結果が変わる）。"""
    rep = card("rep", title="MIRAI ログイン画面が落ちる", evidence=("src/a.py:1",), age_minutes=120)
    dup = card("dup", title="MIRAI ログイン画面が落ちます", evidence=("src/a.py:1",), age_minutes=60)
    pairs = find_duplicate_pairs([rep, dup], TrigramSimilarity(), threshold=0.55)
    print("\n[C] pairs:", [(p.card_id.external_id, p.other_id.external_id, round(p.score, 3), p.exact_title) for p in pairs])
    report, repo, judge, _, _ = run(
        [rep, dup],
        judge=ScriptedJudge(duplicate=DUPLICATE, resolved=RESOLVED),
        inspector=FakeInspector(missing_paths=("src/a.py",)),
        apply=True,
        config=make_config(similarity_threshold=0.55),
    )
    print("[C] plans:", [(p.card_id.external_id, p.action) for p in report.planned])
    print("[C] resolved judge calls:", judge.resolved_calls)
    print("[C] archived:", repo.archived)
    assert plan_for(report, "rep").action == "keep"
    assert plan_for(report, "dup").action == "merge"


def test_D_exact_title_cluster_of_three_posts_only_one_merge_comment():
    """（範囲外の観測）digest はタイトル+本文だけから作るので、完全一致の重複同士では
    衝突する。has_comment による冪等判定が働き、代表に載る集約コメントが 1 件だけになる。"""
    rep = card("rep", age_minutes=180)
    d1 = card("d1", age_minutes=120)
    d2 = card("d2", age_minutes=60)
    print("\n[D] digests:", rep.digest(), d1.digest(), d2.digest())
    report, repo, _, _, _ = run([rep, d1, d2], apply=True)
    print("[D] plans:", [(p.card_id.external_id, p.action) for p in report.planned])
    print("[D] archived:", repo.archived)
    print("[D] comments on rep:", repo.comments.get("trello:rep", []))
    assert plan_for(report, "d1").action == "merge"
    assert plan_for(report, "d2").action == "merge"
    assert len(repo.comments.get("trello:rep", [])) == 1  # d2 の集約コメントが落ちる


def test_E_bodies_differ_so_the_merge_comment_is_posted_on_the_archived_representative():
    """本文が違えば digest は衝突しない。集約コメントはアーカイブ済みの代表に投稿される
    （＝カード原文の記述どおりになる）。クラスタは正規化タイトルだけで作られるのに対し
    digest はタイトル + 本文から作るため、ケース A との差はここだけで生まれる。"""
    rep = card("rep", body="本文A", evidence=("src/a.py:1",), age_minutes=120)
    dup = card("dup", body="本文B", evidence=("src/a.py:1",), age_minutes=60)
    print("\n[E] digests:", rep.digest(), dup.digest())
    report, repo, _, _, _ = run(
        [rep, dup],
        judge=ScriptedJudge(resolved=RESOLVED),
        inspector=FakeInspector(missing_paths=("src/a.py",)),
        apply=True,
    )
    print("[E] plans:", [(p.card_id.external_id, p.action) for p in report.planned])
    print("[E] archived:", repo.archived)
    print("[E] comments on rep:", repo.comments.get("trello:rep"))
    print("[E] comments on dup:", repo.comments.get("trello:dup"))
    assert plan_for(report, "rep").action == "archive"
    assert plan_for(report, "dup").action == "merge"
    assert any("集約しました" in t for t in repo.comments["trello:rep"])  # 抑止されない
    # 畳んだ先を指すコメントは、ケース A でも E でも重複側に残る
    assert any("https://trello.com/c/rep" in t for t in repo.comments["trello:dup"])


def test_F_representative_that_ended_as_keep_is_never_rejudged_in_the_next_run():
    """keep で終わった代表は、内容が変わらない限り次のラン以降も対象に戻らない。
    _is_target は台帳の action が EFFECTFUL_ACTIONS のときだけ再開するため、
    「クラスタのために鮮度判定を見送る」は次のランへ持ち越されない。"""
    rep = card("rep", title="MIRAI ログイン画面が落ちる", evidence=("src/a.py:1",), age_minutes=120)
    dup = card("dup", title="MIRAI ログイン画面が落ちます", evidence=("src/a.py:1",), age_minutes=60)
    ledger = FakeLedger()
    config = make_config(similarity_threshold=0.55)
    first, _, _, _, _ = run(
        [rep, dup],
        judge=ScriptedJudge(duplicate=DUPLICATE, resolved=RESOLVED),
        inspector=FakeInspector(missing_paths=("src/a.py",)),
        apply=True,
        ledger=ledger,
        config=config,
    )
    print("\n[F] run1 plans:", [(p.card_id.external_id, p.action) for p in first.planned])
    print("[F] ledger:", {k: (e.action, e.state, e.reason) for k, e in ledger.entries.items()})
    assert plan_for(first, "rep").action == "keep"
    # 次のラン: dup はアーカイブ済みなので rep だけが Inbox に残る
    second, _, judge, _, _ = run(
        [rep],
        judge=ScriptedJudge(resolved=RESOLVED),
        inspector=FakeInspector(missing_paths=("src/a.py",)),
        apply=True,
        ledger=ledger,
        config=config,
    )
    print("[F] run2 plans:", [(p.card_id.external_id, p.action) for p in second.planned])
    print("[F] run2 resolved judge calls:", judge.resolved_calls)
    assert plan_for(second, "rep").action == "skip"
    assert judge.resolved_calls == []
