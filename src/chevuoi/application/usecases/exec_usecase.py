from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from injector import inject
from pydantic import BaseModel

from chevuoi.application.usecases.workflow_registry import WorkflowRegistry
from chevuoi.domain.entities.project import Project
from chevuoi.domain.entities.task_proposal import TaskProposal
from chevuoi.domain.ports.graph_executor import GraphExecutor
from chevuoi.domain.ports.workspace_inspector import Workspace, WorkspaceInspector
from chevuoi.domain.value_objects.project_tag import ProjectTag
from chevuoi.infrastructure.config.settings import AppConfig

logger = logging.getLogger(__name__)

ExecStatus = Literal["done", "blocked", "error"]


class ExecOutcome(BaseModel):
    """vuoi exec の結果。呼び出し元（対話中の Claude）が JSON で読む。"""

    status: ExecStatus
    workflow: str
    workdir: str = ""
    project: str | None = None  # 設定から引けたタグ。引けなければ None
    test_commands: list[str] = []
    summary: str = ""
    blocked: str = ""
    output: str = ""
    changed_files: list[str] = []  # 実行後の git status --porcelain
    proposals: list[TaskProposal] = []  # 副次タスクの申告（起票はしない）
    error: str = ""


class ExecUsecase:
    """今いる作業ツリーでワークフローを名指しで 1 回実行する（vuoi exec）。

    カード処理（ProcessCardUsecase）と違い、worktree の作成・コミット・PR 作成・
    起票は行わない。それらは呼び出し元が結果を見て決める。

    ワークフローの存在確認と作業ツリーの特定はワークフローを動かす前に行い、
    失敗したら WorkflowError / WorkspaceError を送出する（何も変更しない）。
    """

    @inject
    def __init__(
        self,
        registry: WorkflowRegistry,
        executor: GraphExecutor,
        inspector: WorkspaceInspector,
        config: AppConfig,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._inspector = inspector
        self._config = config

    def execute(
        self,
        name: str,
        brief: str,
        *,
        cwd: Path,
        test_commands: Sequence[str] = (),
    ) -> ExecOutcome:
        self._registry.scan()
        workflow = self._registry.get(name)
        workspace = self._inspector.locate(cwd)
        project = self.resolve_project(workspace, test_commands)
        logger.info(
            "ワークフロー実行開始: %s (workdir=%s, project=%s)",
            name,
            workspace.root,
            project.tag.value if project else None,
        )
        result = self._executor.execute(
            workflow,
            brief,
            workdir=workspace.root,
            project=project,
            # 開始前からの未コミットの変更も「変更あり」に数える。影響はレビューが
            # 1 回余分に走るだけなので許容する（issues/20261118-easier-loop-app）
            has_changes=lambda: bool(self._inspector.changed_files(workspace.root)),
        )
        status: ExecStatus = "blocked" if result.blocked else "done"
        logger.info("ワークフロー実行終了: %s (%s)", name, status)
        return ExecOutcome(
            status=status,
            workflow=name,
            workdir=str(workspace.root),
            project=self._matched_tag(workspace),
            test_commands=list(project.test_commands) if project else [],
            summary=result.summary,
            blocked=result.blocked,
            output=result.output,
            changed_files=self._inspector.changed_files(workspace.root),
            proposals=result.proposals,
        )

    def resolve_project(
        self, workspace: Workspace, test_commands: Sequence[str] = ()
    ) -> Project | None:
        """作業ツリーを設定のプロジェクトと照合し、ワークフローに渡す Project を作る。

        テストコマンドは引数で渡されたものを優先し、無ければ設定から引き継ぐ。
        設定に無く、テストコマンドも渡されなければ None（プロジェクト情報なし）。
        repo_path は常に今の作業ツリー（設定のパスではない）。
        """
        tag = self._matched_tag(workspace)
        entry = self._config.projects[tag] if tag is not None else None
        commands = list(test_commands) or (list(entry.test_commands) if entry else [])
        if entry is None and not commands:
            return None
        return Project(
            tag=ProjectTag(value=tag if tag is not None else workspace.main_root.name),
            repo_path=workspace.root,
            test_commands=commands,
            base_ref=entry.base_ref if entry else "",
        )

    def _matched_tag(self, workspace: Workspace) -> str | None:
        """作業ツリーのルートか本体のルートに一致するパスを持つタグ。

        作業ツリー自体の一致を優先する。同じパスのタグが複数あれば設定の先頭を使う。
        """
        for target in (workspace.root, workspace.main_root):
            for tag, entry in self._config.projects.items():
                if _same_path(entry.path, target):
                    return tag
        return None


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.expanduser().resolve() == b.resolve()
    except OSError:
        return False
