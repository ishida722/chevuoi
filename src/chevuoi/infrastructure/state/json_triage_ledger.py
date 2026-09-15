from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from injector import inject

from chevuoi.domain.ports.triage_ledger import TriageLedger, TriageLedgerEntry
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)


class JsonTriageLedger(TriageLedger):
    """1 つの JSON ファイルに全エントリを持つ台帳。

    真実源ではないので、読めない・書けない場合も例外にせず「空の台帳」として続ける
    （台帳を失っても再判定のコストが増えるだけで、結果は変わらない）。
    """

    @inject
    def __init__(self, config: AppConfig) -> None:
        self._path = Path(config.triage.ledger_path).expanduser()
        self._entries: dict[str, TriageLedgerEntry] | None = None

    def load(self) -> dict[str, TriageLedgerEntry]:
        if self._entries is None:
            self._entries = self._read()
        return dict(self._entries)

    def record(self, entry: TriageLedgerEntry) -> None:
        entries = self.load()
        entries[str(entry.card_id)] = entry
        self._entries = entries
        self._write(entries)

    def _read(self) -> dict[str, TriageLedgerEntry]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {
                key: TriageLedgerEntry.model_validate(value) for key, value in data.items()
            }
        except (OSError, ValueError) as e:
            logger.warning("台帳を読めないため空として続行: %s (%s)", self._path, e)
            return {}

    def _write(self, entries: dict[str, TriageLedgerEntry]) -> None:
        payload = {key: json.loads(e.model_dump_json()) for key, e in entries.items()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 途中でクラッシュしても既存の台帳を壊さないよう、書いてから置き換える
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as e:
            logger.warning("台帳を書けませんでした: %s (%s)", self._path, e)
