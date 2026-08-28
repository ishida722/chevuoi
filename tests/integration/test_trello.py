from pathlib import Path

import httpx

from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.trello.client import TrelloClient
from chevuoi.infrastructure.trello.trello_card_provider import TrelloCardProvider


def make_config() -> AppConfig:
    return AppConfig(
        trello=TrelloConfig(
            api_key="k", api_token="t",
            ready_list_id="ready", in_progress_list_id="doing", in_review_list_id="review",
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


class MockTrello:
    """httpx transport 差し替え用の最小 Trello サーバ。"""

    def __init__(self, card_list_id: str = "ready") -> None:
        self.card_list_id = card_list_id
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/1/lists/ready/cards":
            return httpx.Response(200, json=[CARD_JSON])
        if path == "/1/cards/abc123" and request.method == "GET":
            return httpx.Response(200, json={"idList": self.card_list_id})
        if path == "/1/cards/abc123" and request.method == "PUT":
            self.card_list_id = request.url.params["idList"]
            return httpx.Response(200, json={})
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
        assert comment_reqs and comment_reqs[0].url.params["text"] == "done"
