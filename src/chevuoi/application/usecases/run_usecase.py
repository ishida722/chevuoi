from __future__ import annotations

import logging

from injector import inject

from chevuoi.application.usecases.process_card_usecase import ProcessCardUsecase
from chevuoi.domain.ports.card_provider import CardProvider

logger = logging.getLogger(__name__)


class RunUsecase:
    """vuoi run の1巡。Ready のカードを順に処理する。"""

    @inject
    def __init__(self, provider: CardProvider, process_card: ProcessCardUsecase) -> None:
        self.provider = provider
        self.process_card = process_card

    def execute(self) -> None:
        for card in self.provider.fetch_ready_cards():
            try:
                self.process_card.execute(card)
            except Exception:
                logger.exception("card processing failed: %s", card.id)
