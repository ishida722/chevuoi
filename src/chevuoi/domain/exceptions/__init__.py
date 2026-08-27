class ChevuoiError(Exception):
    """chevuoi のドメイン例外の基底。"""


class ClaimError(ChevuoiError):
    """カードのクレーム操作に失敗した。"""


class ProjectNotFoundError(ChevuoiError):
    """タグに対応するプロジェクトが対応表に無い。"""


class WorktreeError(ChevuoiError):
    """git worktree の操作に失敗した。"""
