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


class QueueProvider(CardProvider):
    """呼び出しごとにあらかじめ用意した結果を順に返す CardProvider。"""

    def __init__(self, batches: list[list[Card]]) -> None:
        self.batches = batches
        self.fetch_count = 0

    def fetch_ready_cards(self) -> list[Card]:
        batch = self.batches[self.fetch_count] if self.fetch_count < len(self.batches) else []
        self.fetch_count += 1
        return batch


class RecordingProcessCard:
    def __init__(self) -> None:
        self.processed: list[Card] = []

    def execute(self, card: Card) -> None:
        self.processed.append(card)


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
        cards = [FakeCard("A 1"), FakeCard("A 2")]
        process = ExplodingProcessCard()
        RunUsecase(FakeProvider(cards), process).execute()  # type: ignore[arg-type]
        assert process.processed == [cards[1]]

    def test_repeats_until_no_ready_cards(self):
        first = [FakeCard("A 1", external_id="c1"), FakeCard("A 2", external_id="c2")]
        second = [FakeCard("A 3", external_id="c3")]
        provider = QueueProvider([first, second, []])
        process = RecordingProcessCard()
        RunUsecase(provider, process).execute()  # type: ignore[arg-type]
        assert process.processed == first + second
        assert provider.fetch_count == 3

    def test_stops_when_no_progress(self):
        stuck = [FakeCard("A 1", external_id="c1")]
        provider = QueueProvider([stuck, stuck, stuck])
        process = RecordingProcessCard()
        RunUsecase(provider, process).execute()  # type: ignore[arg-type]
        # 同じカード集合が続いたら打ち切る（2回目の巡回はしない）
        assert process.processed == stuck
        assert provider.fetch_count == 2

    def test_no_ready_cards_at_start(self):
        provider = QueueProvider([[]])
        process = RecordingProcessCard()
        RunUsecase(provider, process).execute()  # type: ignore[arg-type]
        assert process.processed == []


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
