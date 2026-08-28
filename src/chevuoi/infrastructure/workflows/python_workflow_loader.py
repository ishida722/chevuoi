from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
import types

from injector import inject
from langgraph.graph import StateGraph

from vuoi_sdk import WorkflowContext

from chevuoi.domain.entities.workflow_meta import WorkflowMeta
from chevuoi.domain.ports.llm_factory import LlmFactory
from chevuoi.domain.ports.workflow_loader import (
    LoadedWorkflow,
    LoadFailure,
    WorkflowLoader,
)
from chevuoi.infrastructure.config.settings import AppConfig

NAMESPACE = "vuoi_workflows"


def _purge_modules(prefix: str) -> None:
    """失敗したモジュールをサブモジュールも含めて sys.modules から除去する。"""
    for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
        del sys.modules[key]


class PythonWorkflowLoader(WorkflowLoader):
    """workflow.py を import → build(ctx) → compile する（仕様 §6）。

    sys.path には追加せず spec_from_file_location でパスを直指定し、
    失敗時は sys.modules を purge して LoadFailure を返す。
    """

    @inject
    def __init__(self, config: AppConfig, llm_factory: LlmFactory) -> None:
        self._config = config
        self._llm_factory = llm_factory

    def load(self, meta: WorkflowMeta) -> LoadedWorkflow | LoadFailure:
        fq = f"{NAMESPACE}.{meta.name}"
        saved_path = list(sys.path)
        try:
            ctx = self._build_context(meta)
            spec = importlib.util.spec_from_file_location(
                fq,
                meta.entry_path,
                submodule_search_locations=[str(meta.path)],
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"モジュール spec を作成できません: {meta.entry_path}")
            # 相対 import（from . import prompts）は親パッケージの存在を要求する
            if NAMESPACE not in sys.modules:
                package = types.ModuleType(NAMESPACE)
                package.__path__ = []
                sys.modules[NAMESPACE] = package
            module = importlib.util.module_from_spec(spec)
            # exec_module の前に登録する（dataclass / get_type_hints がモジュール解決に依存）
            sys.modules[fq] = module
            spec.loader.exec_module(module)

            build = getattr(module, "build", None)
            if not callable(build):
                raise TypeError(
                    f"{meta.entry} にトップレベルの build(ctx) がありません"
                )
            builder = build(ctx)
            if not isinstance(builder, StateGraph):
                raise TypeError(
                    "build() は未コンパイルの StateGraph を返す必要があります"
                    f"（実際: {type(builder).__name__}）。compile はホストが行います"
                )
            graph = builder.compile(name=meta.name)
            return LoadedWorkflow(name=meta.name, graph=graph)
        except KeyboardInterrupt:
            _purge_modules(fq)
            raise
        except BaseException:  # SystemExit（ユーザーの sys.exit()）も拾う
            _purge_modules(fq)
            return LoadFailure(name=meta.name, traceback=traceback.format_exc())
        finally:
            sys.path[:] = saved_path

    def _build_context(self, meta: WorkflowMeta) -> WorkflowContext:
        return WorkflowContext(
            llm=self._llm_factory.create(),
            settings={**self._config.workflow_defaults, **meta.settings},
            logger=logging.getLogger("vuoi.workflows").getChild(meta.name),
        )
