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
        cards = self.provider.fetch_ready_cards()
        if not cards:
            logger.info("ready なカードはありません")
            return
        logger.info("ready なカード %d 枚を処理します", len(cards))
        for card in cards:
            logger.info("処理開始: %s (%s)", card.name, card.id)
            try:
                self.process_card.execute(card)
            except Exception:
                logger.exception("card processing failed: %s", card.id)
        logger.info("1巡終了")
