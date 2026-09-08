from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from chevuoi.domain.ports.repository_locator import RepositoryLocator
from chevuoi.domain.services.repository_name import normalize_repo

logger = logging.getLogger(__name__)

TIMEOUT_SEC = 15


def parse_repo_slug(url: str) -> str | None:
    """git のリモート URL から "owner/name" を取り出す。

    GitHub 以外のホストは None を返す（gh で扱えず、別ホストの同名リポジトリを
    取り違える危険もあるため）。解釈は設定の repo と同じ normalize_repo に委ねる。
    """
    if "github.com" not in url:
        return None
    return normalize_repo(url)


class GitRepositoryLocator(RepositoryLocator):
    """origin のリモート URL からリポジトリを引く。設定のプロジェクト数だけ
    git を起動するので、結果はプロセス内でキャッシュする。
    """

    def __init__(self) -> None:
        self._cache: dict[Path, str | None] = {}

    def locate(self, path: Path) -> str | None:
        if path not in self._cache:
            self._cache[path] = parse_repo_slug(self._origin_url(path))
        return self._cache[path]

    @staticmethod
    def _origin_url(path: Path) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(path), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=TIMEOUT_SEC,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning("origin の取得に失敗: %s (%s)", path, e)
            return ""
        if proc.returncode != 0:
            logger.warning("origin の取得に失敗: %s (%s)", path, proc.stderr.strip())
            return ""
        return proc.stdout.strip()
