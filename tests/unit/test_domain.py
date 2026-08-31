import pytest

from chevuoi.domain.entities.issue_report import IssuedCard, IssueReport
from chevuoi.domain.entities.task_proposal import TaskProposal
from chevuoi.domain.services.proposal_policy import select_proposals
from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag


class TestProjectTag:
    def test_extracts_tag_from_title(self):
        assert ProjectTag.from_title("MIRAI ログイン修正") == ProjectTag(value="MIRAI")

    def test_extracts_japanese_tag(self):
        assert ProjectTag.from_title("未来リサーチ テストを実施する") == ProjectTag(
            value="未来リサーチ"
        )

    def test_extracts_tag_from_fullwidth_space_title(self):
        assert ProjectTag.from_title(
            "未来リサーチ　後処理MAのデザインドックとADR"
        ) == ProjectTag(value="未来リサーチ")

    def test_no_delimiter_returns_none(self):
        assert ProjectTag.from_title("ログイン修正") is None

    def test_empty_tag_returns_none(self):
        assert ProjectTag.from_title(" ログイン修正") is None

    def test_fullwidth_space_only_prefix_returns_none(self):
        assert ProjectTag.from_title("　ログイン修正") is None


class TestBranchName:
    def test_derived_deterministically_from_card_id(self):
        card_id = CardId(source="trello", external_id="oFm0QQAr")
        assert BranchName.from_card_id(card_id).value == "chevuoi/trello-oFm0QQAr"

    def test_card_id_str(self):
        assert str(CardId(source="trello", external_id="abc")) == "trello:abc"


def _p(title: str) -> TaskProposal:
    return TaskProposal(title=title)


class TestTaskProposal:
    def test_key_is_deterministic_and_depends_on_parent(self):
        parent = CardId(source="trello", external_id="abc")
        assert _p("Foo bar").key(parent) == _p("  foo   BAR ").key(parent)
        assert _p("Foo bar").key(parent) != _p("Foo bar").key(None)
        assert len(_p("x").key(None)) == 12

    def test_empty_title_is_invalid(self):
        with pytest.raises(ValueError):
            TaskProposal(title="")


class TestSelectProposals:
    def test_accepts_within_limit(self):
        r = select_proposals([_p("a"), _p("b")], parent_generation=0, max_per_run=3, max_generation=2)
        assert [p.title for p in r.accepted] == ["a", "b"] and r.rejected == [] and r.overflow == 0

    def test_depth_limit_rejects_all(self):
        r = select_proposals([_p("a")], parent_generation=2, max_per_run=3, max_generation=2)
        assert r.accepted == [] and r.rejected[0][1] == "世代深度の上限"

    def test_duplicates_are_first_wins_and_not_counted_as_overflow(self):
        r = select_proposals(
            [_p("Foo"), _p("foo "), _p("bar")], parent_generation=1, max_per_run=1, max_generation=2
        )
        assert [p.title for p in r.accepted] == ["Foo"]
        assert [(p.title, why) for p, why in r.rejected] == [("foo ", "重複"), ("bar", "上限超過")]
        assert r.overflow == 1 and [p.title for p in r.overflowed] == ["bar"]


class TestIssueReport:
    def test_empty_and_comment(self):
        assert IssueReport().is_empty
        report = IssueReport(
            issued=[IssuedCard(id=CardId(source="t", external_id="1"), url="u1", created=True)],
            skipped=["上限超過: x"],
        )
        assert report.to_comment() == "🤖 起票:\n- 新規: u1\n- 見送り: 上限超過: x"

    def test_skipped_is_capped(self):
        report = IssueReport(skipped=[f"上限超過: p{i}" for i in range(15)])
        text = report.to_comment()
        assert text.count("見送り:") == 11 and text.endswith("他 5 件（ログ参照）")
