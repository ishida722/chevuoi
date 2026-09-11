from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from chevuoi.domain.exceptions import ProjectNotResolvedError
from chevuoi.domain.value_objects.project_tag import ProjectTag


class Project(BaseModel):
    """タグに紐付くプロジェクトフォルダ。

    リポジトリのパスは repo_path プロパティ経由でしか読めない。未解決のときに
    Path("") のような値を返すと、pathlib の正規化で Path(".") と同値になり、
    git 系の処理が実行場所のリポジトリを触ってしまうため、未解決なら例外にする。

    is_null は「repo_path を読めるか」の唯一の答えになる（is_null が False なら
    repo_path は必ず読める）。判定はこれ 1 つで済む。

    構築時は repo_path= を必ず渡す。リポジトリを持たないプロジェクト（タグしか
    要らない起票など）は repo_path=None を明示する。渡し忘れを黙って未解決に
    しないため、既定値は置かない。
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    tag: ProjectTag
    # 読み出しは下の repo_path プロパティへ集約する。構築・直列化のキーは repo_path のまま
    resolved_repo_path: Path | None = Field(alias="repo_path", serialization_alias="repo_path")
    test_commands: list[str] = []  # テストゲートの中身。有無・回数はワークフローが決める
    # 差分・鮮度の基準にするブランチ。空なら実装側が origin/HEAD を解決する
    base_ref: str = ""

    @field_validator("resolved_repo_path")
    @classmethod
    def _reject_cwd_relative_path(cls, value: Path | None) -> Path | None:
        """相対パスは受け付けない。

        Path("") は Path(".") へ正規化され、「未解決」と「実行場所のリポジトリ」を
        区別できなくなる。git 系の処理は cwd で動くため、相対パスを許すと
        どこから vuoi を実行したかで対象リポジトリが変わってしまう。
        """
        if value is not None and not value.is_absolute():
            raise ValueError(
                f"リポジトリのパスは絶対パスで指定してください（実行場所に依存するため）: {value}"
            )
        return value

    @property
    def repo_path(self) -> Path:
        """リポジトリのパス。未解決なら例外（カレントディレクトリへ落とさない）。"""
        if self.resolved_repo_path is None:
            raise ProjectNotResolvedError(
                f"リポジトリが未解決のプロジェクト（tag={self.tag.value!r}）の "
                "repo_path を参照しました。使う前に is_null で判定してください"
            )
        return self.resolved_repo_path

    @property
    def is_null(self) -> bool:
        """リポジトリが未解決（repo_path を読めない）なら True。"""
        return self.resolved_repo_path is None


class NullProject(Project):
    """解決できなかったことを表す Null Object。処理側は is_null で判定する。

    引けなかったタグは保持する（起票のようにタグだけは使う処理があるため）。
    """

    def __init__(self, tag: ProjectTag | None = None) -> None:
        super().__init__(tag=tag or ProjectTag(value=""), repo_path=None)
