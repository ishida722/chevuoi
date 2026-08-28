from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from injector import inject

from chevuoi.application.usecases.process_card_usecase import ProcessCardUsecase
from chevuoi.domain.entities.card import Card
from chevuoi.domain.ports.card_provider import CardProvider
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)


class RunUsecase:
    """vuoi run の1巡。Ready のカードを最大 max_parallel 並列で処理する。"""

    @inject
    def __init__(
        self,
        provider: CardProvider,
        process_card: ProcessCardUsecase,
        config: AppConfig,
    ) -> None:
        self.provider = provider
        self.process_card = process_card
        self.max_parallel = config.max_parallel

    def execute(self) -> None:
        cards = self.provider.fetch_ready_cards()
        if not cards:
            logger.info("ready なカードはありません")
            return
        logger.info(
            "ready なカード %d 枚を最大 %d 並列で処理します", len(cards), self.max_parallel
        )
        with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
            for card in cards:
                executor.submit(self._process_one, card)
        logger.info("1巡終了")

    def _process_one(self, card: Card) -> None:
        logger.info("処理開始: %s (%s)", card.name, card.id)
        try:
            self.process_card.execute(card)
        except Exception:
            logger.exception("card processing failed: %s", card.id)
        logger.info("処理終了: %s (%s)", card.name, card.id)
