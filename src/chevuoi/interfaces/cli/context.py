from __future__ import annotations

from collections.abc import Callable

import typer
from injector import Injector

InjectorFactory = Callable[[], Injector]


def get_injector(ctx: typer.Context) -> Injector:
    """ルート callback が ctx.obj に仕込んだファクトリを呼び、Injector を構築する。

    設定ファイルの読み込みや Injector 構築をここまで遅らせることで、
    `--help` や引数エラーのときに設定ファイルを要求しない。
    """
    factory: InjectorFactory = ctx.obj
    return factory()
