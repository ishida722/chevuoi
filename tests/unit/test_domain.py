from chevuoi.domain.value_objects.branch_name import BranchName
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag


class TestProjectTag:
    def test_extracts_tag_from_title(self):
        assert ProjectTag.from_title("MIRAI: ログイン修正") == ProjectTag(value="MIRAI")

    def test_no_delimiter_returns_none(self):
        assert ProjectTag.from_title("ログイン修正") is None

    def test_empty_tag_returns_none(self):
        assert ProjectTag.from_title(": ログイン修正") is None

    def test_tag_with_space_returns_none(self):
        assert ProjectTag.from_title("MIRAI 修正: x") is None


class TestBranchName:
    def test_derived_deterministically_from_card_id(self):
        card_id = CardId(source="trello", external_id="oFm0QQAr")
        assert BranchName.from_card_id(card_id).value == "chevuoi/trello-oFm0QQAr"

    def test_card_id_str(self):
        assert str(CardId(source="trello", external_id="abc")) == "trello:abc"
