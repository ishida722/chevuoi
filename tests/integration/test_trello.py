from pathlib import Path

from urllib.parse import parse_qsl

import httpx

import pytest

from chevuoi.domain.exceptions import CardIssueError
from chevuoi.domain.ports.card_issuer import CardIssueRequest
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.trello.client import TrelloClient
from chevuoi.infrastructure.trello.trello_card import TrelloCard
from chevuoi.infrastructure.trello.trello_card_issuer import TrelloCardIssuer
from chevuoi.infrastructure.trello.trello_card_provider import TrelloCardProvider


def make_config(inbox: str | None = "inbox") -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k", api_token="t",
            ready_list_id="ready", in_progress_list_id="doing", in_review_list_id="review",
            inbox_list_id=inbox,
        ),
        projects={},
        worktree_root=Path("/tmp/worktrees"),
    )


CARD_JSON = {
    "id": "abc123",
    "shortLink": "oFm0QQAr",
    "name": "MIRAI 修正",
    "desc": "本文",
    "url": "https://trello.com/c/oFm0QQAr",
    "idList": "ready",
}


def _form(request: httpx.Request) -> dict[str, str]:
    return dict(parse_qsl(request.content.decode()))


class MockTrello:
    """httpx transport 差し替え用の最小 Trello サーバ。"""

    def __init__(self, card_list_id: str = "ready") -> None:
        self.card_list_id = card_list_id
        self.requests: list[httpx.Request] = []
        self.inbox: list[dict] = []
        self.board: list[dict] = []  # Inbox から動かされたカードを含むボード全体

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/1/lists/ready/cards":
            return httpx.Response(200, json=[CARD_JSON])
        if path == "/1/lists/inbox/cards":
            return httpx.Response(200, json=self.inbox)
        if path == "/1/lists/inbox":
            return httpx.Response(200, json={"idBoard": "board1"})
        if path == "/1/boards/board1/cards":
            return httpx.Response(200, json=self.inbox + self.board)
        if path == "/1/cards" and request.method == "POST":
            form = _form(request)
            n = len(self.inbox) + 1
            card = {
                "id": f"new{n}", "shortLink": f"NEW{n}", "name": form["name"],
                "desc": form["desc"], "url": f"https://trello.com/c/NEW{n}", "idList": form["idList"],
            }
            self.inbox.append(card)
            return httpx.Response(200, json=card)
        if path == "/1/cards/abc123" and request.method == "GET":
            return httpx.Response(200, json={"idList": self.card_list_id})
        if path == "/1/cards/abc123" and request.method == "PUT":
            self.card_list_id = _form(request)["idList"]
            return httpx.Response(200, json={})
        if path == "/1/cards/abc123/actions" and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"type": "commentCard", "data": {"text": "新しいコメント"}},
                    {"type": "commentCard", "data": {"text": "古いコメント"}},
                ],
            )
        if path == "/1/cards/abc123/actions/comments":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_provider(server: MockTrello) -> TrelloCardProvider:
    config = make_config()
    client = TrelloClient(config, transport=server.transport())
    return TrelloCardProvider(client, config)


class TestTrelloCardProvider:
    def test_fetch_ready_cards(self):
        cards = make_provider(MockTrello()).fetch_ready_cards()
        assert len(cards) == 1
        card = cards[0]
        assert str(card.id) == "trello:oFm0QQAr"
        assert card.name == "MIRAI 修正"
        assert card.project_tag and card.project_tag.value == "MIRAI"


class TestTrelloCard:
    def test_claim_moves_ready_card_to_in_progress(self):
        server = MockTrello()
        card = make_provider(server).fetch_ready_cards()[0]
        assert card.claim() is True
        assert server.card_list_id == "doing"

    def test_claim_is_idempotent_when_already_in_progress(self):
        server = MockTrello(card_list_id="doing")
        card = make_provider(server).fetch_ready_cards()[0]
        assert card.claim() is True

    def test_claim_fails_from_other_list(self):
        server = MockTrello(card_list_id="review")
        card = make_provider(server).fetch_ready_cards()[0]
        assert card.claim() is False
        assert server.card_list_id == "review"

    def test_add_comment_and_move_to_review(self):
        server = MockTrello()
        card = make_provider(server).fetch_ready_cards()[0]
        card.add_comment("done")
        card.move_to_review()
        assert server.card_list_id == "review"
        comment_reqs = [r for r in server.requests if "comments" in r.url.path]
        assert comment_reqs and _form(comment_reqs[0])["text"] == "done"

    def test_fetch_comments_returns_texts_newest_first(self):
        server = MockTrello()
        card = make_provider(server).fetch_ready_cards()[0]
        assert card.fetch_comments() == ["新しいコメント", "古いコメント"]
        action_reqs = [r for r in server.requests if r.url.path == "/1/cards/abc123/actions"]
        assert action_reqs and dict(parse_qsl(action_reqs[0].url.query.decode()))["filter"] == "commentCard"


def make_issuer(server: MockTrello, inbox: str | None = "inbox") -> TrelloCardIssuer:
    config = make_config(inbox)
    return TrelloCardIssuer(TrelloClient(config, transport=server.transport()), config)


def make_request(**kw) -> CardIssueRequest:
    base = dict(
        title="flaky test",
        body="本文",
        project_tag=ProjectTag(value="MIRAI"),
        idempotency_key="3f9a1c2b7d4e",
        kind="bug",
        generation=1,
        parent=CardId(source="trello", external_id="oFm0QQAr"),
        parent_url="https://trello.com/c/oFm0QQAr",
    )
    return CardIssueRequest(**{**base, **kw})


class TestTrelloCardIssuer:
    def test_issue_posts_to_inbox_with_tag_and_footer(self):
        server = MockTrello()
        issued = make_issuer(server).issue(make_request())
        assert issued.created and str(issued.id) == "trello:NEW1"
        assert issued.url == "https://trello.com/c/NEW1"
        post = [r for r in server.requests if r.method == "POST"][0]
        form = _form(post)
        assert form["idList"] == "inbox"
        assert form["name"] == "MIRAI flaky test"
        assert form["desc"] == (
            "本文\n\n---\n"
            "vuoi: key=3f9a1c2b7d4e parent=trello:oFm0QQAr generation=1 kind=bug\n"
            "親カード: https://trello.com/c/oFm0QQAr"
        )

    def test_issue_is_idempotent(self):
        server = MockTrello()
        issuer = make_issuer(server)
        first = issuer.issue(make_request())
        second = issuer.issue(make_request(title="別タイトルでも同キー"))
        assert first.created and not second.created
        assert second.id == first.id
        assert len([r for r in server.requests if r.method == "POST"]) == 1

    def test_issue_without_tag_has_no_leading_space(self):
        server = MockTrello()
        make_issuer(server).issue(make_request(project_tag=ProjectTag(value="")))
        assert _form([r for r in server.requests if r.method == "POST"][0])["name"] == "flaky test"

    def test_board_scope_reuses_a_card_moved_out_of_inbox(self):
        server = MockTrello()
        make_issuer(server).issue(make_request(search_scope="board"))
        server.board = server.inbox  # 人間が Inbox から別リストへ動かした
        server.inbox = []
        second = make_issuer(server).issue(make_request(search_scope="board"))  # 次のラン
        assert not second.created and second.url == "https://trello.com/c/NEW1"
        assert len([r for r in server.requests if r.method == "POST"]) == 1

    def test_board_scope_lists_the_board_once_per_run(self):
        server = MockTrello()
        issuer = make_issuer(server)
        for key in ("aaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccccccc"):
            issuer.issue(make_request(idempotency_key=key, search_scope="board"))
        listings = [r for r in server.requests if r.url.path == "/1/boards/board1/cards"]
        assert len(listings) == 1
        assert len([r for r in server.requests if r.method == "POST"]) == 3

    def test_board_scope_sees_cards_issued_earlier_in_the_same_run(self):
        server = MockTrello()
        issuer = make_issuer(server)
        first = issuer.issue(make_request(search_scope="board"))
        second = issuer.issue(make_request(search_scope="board"))
        assert first.created and not second.created and second.id == first.id
        assert len([r for r in server.requests if r.method == "POST"]) == 1

    def test_inbox_scope_does_not_see_cards_outside_inbox(self):
        server = MockTrello()
        issuer = make_issuer(server)
        issuer.issue(make_request())
        server.board = server.inbox
        server.inbox = []
        assert issuer.issue(make_request()).created
        assert len([r for r in server.requests if r.method == "POST"]) == 2

    def test_footer_round_trips_into_trello_card(self):
        server = MockTrello()
        make_issuer(server).issue(make_request())
        c = server.inbox[0]
        card = TrelloCard(
            None, make_config().trello, card_id=c["id"], short_link=c["shortLink"],
            name=c["name"], desc=c["desc"], url=c["url"], list_id=c["idList"],
        )
        assert card.generation == 1
        assert card.parent_id == CardId(source="trello", external_id="oFm0QQAr")

    def test_card_without_footer_has_defaults(self):
        card = make_provider(MockTrello()).fetch_ready_cards()[0]
        assert card.generation == 0 and card.parent_id is None

    def test_missing_inbox_raises_card_issue_error(self):
        with pytest.raises(CardIssueError, match="inbox_list_id"):
            make_issuer(MockTrello(), inbox=None).issue(make_request())

    def test_api_error_becomes_card_issue_error(self):
        def handler(request):
            return httpx.Response(500)
        config = make_config()
        issuer = TrelloCardIssuer(TrelloClient(config, transport=httpx.MockTransport(handler)), config)
        with pytest.raises(CardIssueError):
            issuer.issue(make_request())

    def test_footer_parse_is_linear_and_single_line(self):
        from chevuoi.infrastructure.trello.trello_card import parse_footer

        assert parse_footer("vuoi: key=a generation=1\nfoo=bar generation=9") == {
            "key": "a", "generation": "1"
        }
        # 失敗経路でもバックトラックが爆発しない
        assert parse_footer("vuoi: " + "a=b=c=d " * 40 + "noeq") == {}

    def test_transport_error_becomes_card_issue_error(self):
        def handler(request):
            raise httpx.ConnectError("down")
        config = make_config()
        issuer = TrelloCardIssuer(TrelloClient(config, transport=httpx.MockTransport(handler)), config)
        with pytest.raises(CardIssueError, match="Inbox"):
            issuer.issue(make_request())
