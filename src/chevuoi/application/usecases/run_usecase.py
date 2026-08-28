from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from injector import inject

from chevuoi.application.usecases.process_card_usecase import ProcessCardUsecase
from chevuoi.domain.entities.card import Card
from chevuoi.domain.ports.card_provider import CardProvider
from chevuoi.domain.value_objects.card_id import CardId
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)


class RunUsecase:
    """vuoi run の実行。Ready のカードがゼロになるまで巡回を繰り返し、
    各巡回では最大 max_parallel 並列で処理する。"""

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
        previous_ids: set[CardId] | None = None
        pass_count = 0
        while True:
            cards = self.provider.fetch_ready_cards()
            if not cards:
                if pass_count == 0:
                    logger.info("ready なカードはありません")
                else:
                    logger.info("ready なカードがなくなりました（%d 巡で終了）", pass_count)
                return
            card_ids = {card.id for card in cards}
            # 全カードが失敗し続けると同じ集合を巡回し続けるため、進捗がなければ打ち切る
            if card_ids == previous_ids:
                logger.warning(
                    "前の巡回からカードが減っていないため打ち切ります（残り %d 枚）", len(cards)
                )
                return
            pass_count += 1
            logger.info(
                "%d 巡目: ready なカード %d 枚を最大 %d 並列で処理します",
                pass_count,
                len(cards),
                self.max_parallel,
            )
            with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
                for card in cards:
                    executor.submit(self._process_one, card)
            logger.info("%d 巡目終了", pass_count)
            previous_ids = card_ids

    def _process_one(self, card: Card) -> None:
        logger.info("処理開始: %s (%s)", card.name, card.id)
        try:
            self.process_card.execute(card)
        except Exception:
            logger.exception("card processing failed: %s", card.id)
        logger.info("処理終了: %s (%s)", card.name, card.id)
