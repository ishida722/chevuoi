from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from injector import inject

from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.triage_card import TriageCard
from chevuoi.domain.entities.triage_judgment import TriageJudgment
from chevuoi.domain.entities.triage_plan import EFFECTFUL_ACTIONS, TriagePlan, TriageReport
from chevuoi.domain.ports.repository_inspector import RepositoryInspector
from chevuoi.domain.ports.similarity_strategy import SimilarityStrategy
from chevuoi.domain.ports.triage_card_repository import TriageCardRepository
from chevuoi.domain.ports.triage_judge import TriageJudge
from chevuoi.domain.ports.triage_ledger import TriageLedger, TriageLedgerEntry
from chevuoi.domain.services.triage_clustering import build_clusters, find_duplicate_pairs
from chevuoi.domain.services.triage_policy import DeterministicSignals, decide
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger("vuoi.triage")

# トリアージが残すコメントの 1 行目。ProcessCardUsecase は 1 行目が 🤖 で始まる
# コメントを自動処理のものとみなすので、この印を外すと人間の追加指示として読まれる
COMMENT_MARK = "🤖 triage:"


class TriageUsecase:
    """vuoi triage の 1 巡。プロジェクトを横断し、Inbox のカード集合を整理する。

    段の順序:
      1. 収集       cards = repo.fetch_open()（作成順）
      2. 対象決定   settle / 自動起票 / ledger の digest で対象の集合を絞る
      3. 決定的判定 evidence の有無・タイトル完全一致・git の読み取り（プロジェクト単位）
      4. LLM 判定   決まらなかったものだけ、予算の範囲で judge へ
      5. 計画       decide() で TriagePlan を作り、ledger に planned を書く
      6. 適用       apply が真なら外部作用を行い、ledger に applied を書く

    カード 1 枚の適用失敗はランを止めず TriageReport.failed に落とす。判定器の失敗は
    棄権であって失敗ではない（人間が見るラベルに落ちる）。
    """

    @inject
    def __init__(
        self,
        cards: TriageCardRepository,
        judge: TriageJudge,
        similarity: SimilarityStrategy,
        inspector: RepositoryInspector,
        ledger: TriageLedger,
        config: AppConfig,
    ) -> None:
        self._cards = cards
        self._judge = judge
        self._similarity = similarity
        self._inspector = inspector
        self._ledger = ledger
        self._config = config

    def execute(
        self,
        *,
        apply: bool = False,
        project: str | None = None,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> TriageReport:
        settings = self._config.triage
        now = now or datetime.now(UTC)
        report = TriageReport()

        cards = self._cards.fetch_open()
        if project is not None:
            wanted = project.casefold()
            cards = [c for c in cards if _tag_value(c).casefold() == wanted]
        if limit is not None:
            cards = cards[:limit]

        entries = self._ledger.load()
        targets = {str(c.id): c for c in cards if self._is_target(c, entries, now)}
        logger.info("トリアージ対象: %d 件 / 読み込み %d 件", len(targets), len(cards))

        # --- 重複: 決定的なふるい → 予算の範囲で LLM 照会 -------------------------
        all_pairs = find_duplicate_pairs(
            cards, self._similarity, threshold=settings.similarity_threshold
        )
        report.pairs_found = len(all_pairs)
        relevant = [p for p in all_pairs if str(p.card_id) in targets or str(p.other_id) in targets]
        # 完全一致は第 1 層で確定するので LLM を呼ばない。候補ペアの上限を消費させると、
        # 照会不要な確実な重複がスコア順の切り捨てで落ちる
        exact_pairs = [p for p in relevant if p.exact_title]
        candidates = [p for p in relevant if not p.exact_title]
        asked_pairs = candidates[: settings.max_pairs_per_run]
        overflow_pairs = candidates[settings.max_pairs_per_run :]
        logger.info(
            "閾値超えの候補ペア: %d 組（完全一致 %d 組 / 照会 %d 組 / 次回へ %d 組）",
            len(all_pairs), len(exact_pairs), len(asked_pairs), len(overflow_pairs),
        )

        by_id = {str(c.id): c for c in cards}
        # 重複判定と鮮度判定は別に持つ。1 つの辞書に混ぜると、先に入った確信のある判定が
        # あとの別種の判定を握り潰す（例: distinct と resolved が同じカードに来る）
        duplicate_judgments: dict[str, TriageJudgment] = {}
        resolved_judgments: dict[str, TriageJudgment] = {}
        asked: set[str] = set()
        deferred: set[str] = set()  # 予算に阻まれて照会できなかった。次のランで拾い直す
        confirmed: list[tuple[CardId, CardId]] = [
            (pair.card_id, pair.other_id) for pair in exact_pairs
        ]
        for pair in asked_pairs:
            keys = [k for k in (str(pair.card_id), str(pair.other_id)) if k in targets]
            asked.update(keys)
            if report.judgments_used >= settings.max_judgments_per_run:
                report.budget_exceeded = True
                deferred.update(keys)
                continue
            judgment = self._judge.judge_duplicate(by_id[str(pair.card_id)], by_id[str(pair.other_id)])
            report.judgments_used += 1
            for key in (str(pair.card_id), str(pair.other_id)):
                self._keep_judgment(duplicate_judgments, key, judgment)
            if judgment.verdict == "duplicate" and not judgment.abstained:
                confirmed.append((pair.card_id, pair.other_id))
        for pair in overflow_pairs:
            # 候補ペアの上限で今回は見ない。黙って捨てず、次回のランで拾えるようにする
            report.budget_exceeded = True
            keys = [k for k in (str(pair.card_id), str(pair.other_id)) if k in targets]
            asked.update(keys)
            deferred.update(keys)

        representatives = build_clusters(cards, confirmed)
        exact_dupes = {str(p.card_id) for p in exact_pairs} | {
            str(p.other_id) for p in exact_pairs
        }

        # --- 解決済み: ベース参照を根拠に、決定的に絞ってから照会 -----------------
        resolved_context = self._resolvable_projects(targets)
        resolution: dict[str, DeterministicSignals] = {}
        for key, card in targets.items():
            context = resolved_context.get(_tag_value(card).casefold())
            if context is not None and card.evidence:
                # git の読み取りは 1 枚につき 1 回だけ行い、判定にも決定表にも同じ事実を使う
                resolution[key] = self._resolution_signals(card, context[0])

        for key, card in targets.items():
            if key in representatives and str(representatives[key].id) != key:
                continue  # 重複として畳む予定のカードに鮮度判定は要らない
            context = resolved_context.get(_tag_value(card).casefold())
            if context is None:
                continue
            checkout = context[1]
            signals = resolution.get(key, DeterministicSignals())
            if not self._should_judge_resolved(card, signals):
                continue
            asked.add(key)
            if report.judgments_used >= settings.max_judgments_per_run:
                report.budget_exceeded = True
                deferred.add(key)
                continue
            resolved_judgments[key] = self._judge.judge_resolved(card, cwd=checkout)
            report.judgments_used += 1

        # --- 計画と適用 -----------------------------------------------------------
        for card in cards:
            key = str(card.id)
            is_target = key in targets
            representative = representatives.get(key)
            resolved_signals = resolution.get(key, DeterministicSignals())
            signals = DeterministicSignals(
                has_evidence=bool(card.evidence),
                body_is_empty=not card.body.strip(),
                exact_title_duplicate=key in exact_dupes,
                is_target=is_target,
                needs_judgment=key in asked,
                judgment_deferred=key in deferred,
                path_missing_in_base=resolved_signals.path_missing_in_base,
                unchanged_since_issue=resolved_signals.unchanged_since_issue,
            )
            judgment = _pick_judgment(
                duplicate_judgments.get(key), resolved_judgments.get(key)
            )
            plan = decide(
                card,
                signals=signals,
                judgment=judgment,
                representative=representative.id if representative is not None else None,
                apply=apply,
            )
            report.planned.append(plan)
            if not is_target:
                continue
            self._record(card, plan, judgment, signals, state="planned", now=now)
            if not apply or not plan.has_effect:
                continue
            try:
                self._apply(card, plan, representatives.get(key))
            except Exception as e:  # noqa: BLE001 - 1 枚の失敗でランを止めない
                logger.warning("適用に失敗: %s (%s)", card.id, e)
                report.failed.append((card.id, str(e)))
                continue
            report.applied.append(card.id)
            self._record(card, plan, judgment, signals, state="applied", now=now)
        return report

    # --- 対象の決定 ---------------------------------------------------------------

    def _is_target(
        self, card: TriageCard, entries: dict[str, TriageLedgerEntry], now: datetime
    ) -> bool:
        """判定対象（畳んでよいカード）かを決定的に決める。

        人間が書いたカードは触らない。実行中のランが今まさに起票したカードも触らない。
        内容が前回から変わっていないカードは再判定しない。ただし前回が予算超過だった
        カードと、計画だけ書かれて実行が済んでいないカードは再開する。
        """
        if not card.is_auto_issued:
            return False
        settle = timedelta(minutes=self._config.triage.settle_minutes)
        if now - card.created_at < settle:
            return False
        entry = entries.get(str(card.id))
        if entry is None:
            return True
        if entry.digest != card.digest() or entry.reason == "budget":
            return True
        # 計画だけ書かれて実行が済んでいないカード（dry-run・適用失敗・途中クラッシュ）は
        # 次のランで再開する。これを見ないと、dry-run を 1 度回した時点で以降の --apply が
        # 何もしなくなる
        return entry.state == "planned" and entry.action in EFFECTFUL_ACTIONS

    # --- 解決済み判定の材料 -------------------------------------------------------

    def _resolvable_projects(
        self, targets: dict[str, TriageCard]
    ) -> dict[str, tuple[Project, Path]]:
        """解決済み判定ができるプロジェクトだけを、ベースチェックアウト付きで返す。

        ベース参照の取得（fetch）に失敗したプロジェクトは丸ごと見送る。重複判定は続く。
        """
        context: dict[str, tuple[Project, Path]] = {}
        tags = {_tag_value(c) for c in targets.values() if _tag_value(c)}
        for tag in sorted(tags):
            project = self._resolve_project(tag)
            if project is None:
                logger.info("タグ %s に対応するプロジェクトが無いため鮮度判定を見送り", tag)
                continue
            if not self._inspector.refresh(project):
                logger.warning("ベース参照を更新できないため鮮度判定を見送り: %s", tag)
                continue
            try:
                checkout = self._inspector.base_checkout(project)
            except Exception as e:  # noqa: BLE001 - 判定を諦めるだけでランは続ける
                logger.warning("ベースのチェックアウトに失敗（%s）: %s", tag, e)
                continue
            context[tag.casefold()] = (project, checkout)
        return context

    def _resolve_project(self, tag: str) -> Project | None:
        """タグ → Project の写像。タグの照合は大文字小文字を無視する
        （ProcessCardUsecase.resolve_project と同じ規則）。"""
        wanted = tag.casefold()
        entry = next(
            (cfg for key, cfg in self._config.projects.items() if key.casefold() == wanted), None
        )
        if entry is None:
            return None
        return Project(
            tag=ProjectTag(value=tag),
            repo_path=entry.path,
            test_commands=list(entry.test_commands),
            base_ref=entry.base_ref,
        )

    def _resolution_signals(self, card: TriageCard, project: Project) -> DeterministicSignals:
        paths = card.evidence_paths
        missing = any(not self._inspector.path_exists(project, p) for p in paths)
        unchanged = bool(paths) and bool(card.base_commit) and not any(
            self._inspector.changed_since(project, p, card.base_commit) for p in paths
        )
        return DeterministicSignals(
            path_missing_in_base=missing, unchanged_since_issue=unchanged
        )

    @staticmethod
    def _should_judge_resolved(card: TriageCard, signals: DeterministicSignals) -> bool:
        """LLM に鮮度を聞くべきカードか。決定的に答えが出るものは聞かない。

        - evidence が無ければ、ベースを見ても確かめようがない
        - 起票時コミット以降その箇所が変わっていないなら、解決しているはずがない
        - 起票時コミットを持たない古いカードは、パスがベースに無い場合だけ聞く（安全側）
        """
        if not card.evidence:
            return False
        if signals.unchanged_since_issue:
            return False
        if not card.base_commit:
            return signals.path_missing_in_base
        return True

    # --- 記録と適用 ---------------------------------------------------------------

    def _record(
        self,
        card: TriageCard,
        plan: TriagePlan,
        judgment: TriageJudgment | None,
        signals: DeterministicSignals,
        *,
        state: str,
        now: datetime,
    ) -> None:
        reason = ""
        if signals.judgment_deferred:
            # 照会が必要だったのに予算で届かなかった。内容が変わらなくても次回やり直す
            reason = "budget"
        elif judgment is not None and judgment.abstained:
            # 棄権は内容が変わらない限り何度やっても同じなので、再判定しない
            reason = "abstained"
        self._ledger.record(
            TriageLedgerEntry(
                card_id=card.id,
                digest=card.digest(),
                action=plan.action,
                reason=reason,
                state=state,
                updated_at=now,
            )
        )

    def _apply(self, card: TriageCard, plan: TriagePlan, representative: TriageCard | None) -> None:
        digest = card.digest()
        if plan.action == "label":
            for label in plan.labels:
                self._cards.add_label(card.id, label)
            return
        if plan.action == "merge":
            if representative is None:  # 決定表が merge を出す限り代表は必ずある
                raise ValueError("集約先が決まっていません")
            # 照合はコメント本文の部分一致なので、鍵の前後（ラベルと改行）まで含めた印で
            # 照合する。鍵だけを渡すと、あるカード ID が別のカード ID の接頭辞のとき
            # （trello:b と trello:b2）に別カードの集約コメントを既存とみなしてしまう
            marker = f"key={merge_comment_key(card)}\n"
            text = (
                f"{COMMENT_MARK} 重複カードを集約しました {marker}"
                f"- {card.title}: {card.url or card.id}\n"
                f"理由: {plan.reason or '（記載なし）'}"
            )
            if not self._cards.has_comment(representative.id, marker):
                self._cards.add_comment(representative.id, text)
            self._cards.add_comment(
                card.id,
                f"{COMMENT_MARK} 重複のためアーカイブしました digest={digest}\n"
                f"集約先: {representative.url or representative.id}\n"
                f"理由: {plan.reason or '（記載なし）'}",
            )
            self._cards.archive(card.id)
            return
        if plan.action == "archive":
            self._cards.add_comment(
                card.id,
                f"{COMMENT_MARK} ベースで解消済みと判定しアーカイブしました digest={digest}\n"
                f"理由: {plan.reason or '（記載なし）'}",
            )
            self._cards.archive(card.id)

    @staticmethod
    def _keep_judgment(
        judgments: dict[str, TriageJudgment], key: str, judgment: TriageJudgment
    ) -> None:
        """確信のある判定を優先して 1 枚につき 1 つ持つ（棄権は上書きされてよい）。"""
        existing = judgments.get(key)
        if existing is None or (existing.abstained and not judgment.abstained):
            judgments[key] = judgment


def merge_comment_key(card: TriageCard) -> str:
    """代表カードに残す集約コメントの冪等キー（コメントには `key=` として書く）。

    digest は正規化したタイトルと本文だけの関数なので、完全一致の重複カード同士では
    必ず等しくなる。digest だけを冪等キーにすると、1 枚目の集約コメントが 2 枚目以降の
    投稿を「既に投稿済み」として抑止し、どのカードを畳んだかの記録が代表に残らない。
    畳まれる側のカード ID を含めて、同じ内容の同じカードの再投稿だけを抑止する。
    """
    return f"{card.digest()}/{card.id}"


def _pick_judgment(
    duplicate: TriageJudgment | None, resolved: TriageJudgment | None
) -> TriageJudgment | None:
    """決定表に渡す判定を 1 つ選ぶ。決定表の行順（重複が先）と同じ優先順位にする。"""
    if duplicate is not None and duplicate.verdict == "duplicate" and not duplicate.abstained:
        return duplicate
    return resolved if resolved is not None else duplicate


def _tag_value(card: TriageCard) -> str:
    tag = card.project_tag
    return tag.value if tag is not None else ""
