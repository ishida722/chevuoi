from __future__ import annotations

import logging

from injector import inject

from chevuoi.domain.ports.worktree_manager import WorktreeManager

logger = logging.getLogger(__name__)


class GcUsecase:
    """vuoi gc。終端済み worktree の掃除。"""

    @inject
    def __init__(self, worktrees: WorktreeManager) -> None:
        self.worktrees = worktrees

    def execute(self, older_than_days: int) -> None:
        stale = list(self.worktrees.list_stale(older_than_days))
        if not stale:
            logger.info("掃除対象の worktree はありません")
            return
        logger.info("worktree %d 件を掃除します", len(stale))
        for worktree in stale:
            logger.info("removing worktree: %s", worktree.path)
            try:
                self.worktrees.remove(worktree)
            except Exception:
                logger.exception("failed to remove worktree: %s", worktree.path)
