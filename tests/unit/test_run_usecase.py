from pathlib import Path

from injector import Injector

from chevuoi.application.usecases.gc_usecase import GcUsecase
from chevuoi.application.usecases.process_card_usecase import ProcessCardUsecase
from chevuoi.application.usecases.run_usecase import RunUsecase
from chevuoi.domain.entities.card import Card
from chevuoi.domain.ports.card_provider import CardProvider
from chevuoi.infrastructure.config.settings import AppConfig, TrelloConfig
from chevuoi.infrastructure.trello.client import TrelloClient
from chevuoi.interface.di_modules import AppModule
from tests.unit.fakes import FakeCard


class FakeProvider(CardProvider):
    def __init__(self, cards: list[Card]) -> None:
        self.cards = cards

    def fetch_ready_cards(self) -> list[Card]:
        return self.cards


class ExplodingProcessCard:
    """1枚目で例外を投げる ProcessCardUsecase の代役。"""

    def __init__(self) -> None:
        self.processed: list[Card] = []
        self.exploded = False

    def execute(self, card: Card) -> None:
        if not self.exploded:
            self.exploded = True
            raise RuntimeError("boom")
        self.processed.append(card)


class TestRunUsecase:
    def test_exception_is_contained_per_card(self):
        cards = [FakeCard("A: 1"), FakeCard("A: 2")]
        process = ExplodingProcessCard()
        RunUsecase(FakeProvider(cards), process).execute()  # type: ignore[arg-type]
        assert process.processed == [cards[1]]


class TestDiWiring:
    def test_injector_resolves_usecases_and_client(self):
        config = AppConfig(
            trello=TrelloConfig(api_key="k", api_token="t", ready_list_id="r",
                                in_progress_list_id="d", in_review_list_id="v"),
            projects={},
            worktree_root=Path("/tmp/wt"),
        )
        injector = Injector([AppModule(config)])
        assert injector.get(RunUsecase)
        assert injector.get(GcUsecase)
        assert injector.get(ProcessCardUsecase)
        # transport が DI で誤注入されると全リクエストが失敗するため、None を確認する
        client = injector.get(TrelloClient)
        assert client._client._transport.__class__.__name__ == "HTTPTransport"
