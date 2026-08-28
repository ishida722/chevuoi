class ChevuoiError(Exception):
    """chevuoi のドメイン例外の基底。"""


class ClaimError(ChevuoiError):
    """カードのクレーム操作に失敗した。"""


class ProjectNotFoundError(ChevuoiError):
    """タグに対応するプロジェクトが対応表に無い。"""


class WorktreeError(ChevuoiError):
    """git worktree の操作に失敗した。"""


class WorkflowError(ChevuoiError):
    """ワークフローのロード・設定に失敗した。"""


class WorkflowNotFound(WorkflowError):
    """条件に合うワークフローが存在しない。"""


class AmbiguousSelection(WorkflowError):
    """候補が 1 件に定まらない。silent fallback はしない（仕様 §7）。"""
