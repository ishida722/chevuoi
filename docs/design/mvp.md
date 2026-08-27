# MVP（v1.0.0）設計

{doc}`../spec/mvp` で定めた範囲を、クリーンアーキテクチャで実装するための設計です。仕様が「何をするか」を定めるのに対し、本章は「どう作るか」（レイヤー構成・クラス設計・依存性注入）を定めます。

## 設計方針

1. **依存の方向は外側 → 内側のみ。** Interface → Infrastructure → Application → Domain の順に依存し、ドメイン層は外部ライブラリに依存しません（Pydantic のみ例外として許可）。
2. **フロー制御はアプリケーション層の決定的なコードで行う。** LLM が関与するのは処理ノードの内部だけで、ユースケースの分岐に LLM を使いません（{doc}`../spec/mvp` の原則 1）。
3. **カードは抽象データ型として設計する。** ドメイン層の `Card` は抽象基底クラスであり、各カードは「自分がどのサービスのどの ID か」を知っていて、**自分自身に対してコメント・リスト移動などの操作ができます**。外部サービスとの接続は `Card` の具体実装（インフラ層の `TrelloCard` など）が担います。これにより Trello と GitHub Issues のようにソースが増えても、アプリケーション層は同じ `Card` 抽象だけを扱えます（MVP で実装する具体カードは `TrelloCard` のみ）。
4. **リポジトリは永続化のためだけに使う。** リポジトリの責務はエンティティを JSON 等で保存・復元することであり、外部サービスとの接続にリポジトリは使いません。Trello はリポジトリのバックエンドではなく、カードの取得はポート（後述）として、取得後のカード操作はカード自身の振る舞いとして実装します。MVP には保存すべき状態が無いため、リポジトリは定義しません。
5. **DI は Injector の `binder.bind()` で宣言的に行う。** ファクトリの手書きや手動ワイヤリングは避けます。

方針 3 は、仕様の「タスクソースは Trello のみ・抽象化なし」と矛盾しません。仕様が禁じているのは複数ソースを見据えた**汎用のタスクソース層**（ソース種別のディスパッチや設定での切り替え機構）を MVP で作り込むことであり、ここでの `Card` 抽象は責務の置き場所（外部接続はカードの具体実装に閉じる）を正しくするためのものです。実装するソースは Trello だけです。

パッケージ名はリポジトリの src レイアウトに従い `chevuoi`、CLI コマンド名は仕様どおり `vuoi` とします（`pyproject.toml` の `[project.scripts]` で `vuoi = "chevuoi.interfaces.cli.main:main"` を定義）。

## ディレクトリ構造

```
src/chevuoi/
├── domain/
│   ├── entities/
│   │   ├── card.py            # Card（抽象データ型・ABC）
│   │   ├── project.py         # Project
│   │   ├── worktree.py        # Worktree
│   │   └── node_result.py     # NodeResult / NodeStatus
│   ├── value_objects/
│   │   ├── card_id.py         # CardId（ソース修飾つき ID。例: trello:<shortLink>）
│   │   ├── project_tag.py     # ProjectTag
│   │   └── branch_name.py     # BranchName（CardId から決定的に導出）
│   ├── ports/
│   │   ├── card_provider.py       # CardProvider（ABC）: カード取得の入力ポート
│   │   ├── worktree_manager.py    # WorktreeManager（ABC）: git worktree 操作
│   │   └── node_runner.py         # NodeRunner（ABC）: 処理ノード実行
│   └── exceptions/
│       └── __init__.py        # ClaimError / ProjectNotFoundError など
│
├── application/
│   └── usecases/
│       ├── run_usecase.py           # RunUsecase（vuoi run の1巡）
│       ├── process_card_usecase.py  # ProcessCardUsecase（カード1枚の処理）
│       └── gc_usecase.py            # GcUsecase（vuoi gc）
│
├── infrastructure/
│   ├── trello/
│   │   ├── client.py                  # TrelloClient（httpx で REST を叩く薄い層）
│   │   ├── trello_card.py             # TrelloCard（Card の具体実装）
│   │   └── trello_card_provider.py    # TrelloCardProvider（CardProvider の実装）
│   ├── git/
│   │   └── git_worktree_manager.py    # subprocess で git worktree 操作
│   ├── claude/
│   │   └── claude_node_runner.py      # subprocess で claude -p を1回実行
│   └── config/
│       └── settings.py                # 設定の読み込み（Pydantic）
│
├── interface/
│   └── di_modules.py          # AppModule（Injector）
│
└── interfaces/
    └── cli/
        └── main.py            # vuoi run / vuoi gc のエントリポイント
```

## ドメイン層

### Card — 抽象データ型としてのカード

`Card` は「読み取り属性」と「自分自身への操作」を持つ抽象基底クラスです。外部サービスとの通信は具体実装だけが知っています。

```python
class Card(ABC):
    """処理対象カードの抽象データ型。

    各具体カードは自分がどのサービスのどの ID かを知っており、
    自分自身に対する操作（クレーム・コメント・移動）を実装する。
    """

    # --- 読み取り属性 ---
    @property
    @abstractmethod
    def id(self) -> CardId: ...        # 例: trello:2xdeTSjW

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def desc(self) -> str: ...

    @property
    @abstractmethod
    def url(self) -> str: ...

    # --- 自分自身への操作（実装が外部サービスと通信する） ---
    @abstractmethod
    def claim(self) -> bool:
        """着手宣言する（Trello なら In Progress への移動）。

        冪等: 既にクレーム済みなら成功として扱い、
        それ以外の状態なら失敗（False）を返す。
        """

    @abstractmethod
    def add_comment(self, text: str) -> None: ...

    @abstractmethod
    def move_to_review(self) -> None: ...

    # --- 純粋ロジック（外部依存なし・基底クラスで実装） ---
    @property
    def project_tag(self) -> ProjectTag | None:
        """タイトル先頭のタグ（例: "MIRAI ログイン修正" → MIRAI）。無ければ None。"""
```

将来 GitHub Issues を扱う場合は `GithubIssueCard` を追加するだけで、アプリケーション層は変更不要です。Trello のカードと GitHub の Issue を混在させたキューも、複数の `CardProvider` の結果を連結すれば同じ `list[Card]` として扱えます（実装は v1.0.0 以降）。

### その他のエンティティ

```python
class Project(BaseModel):
    """タグに紐付くプロジェクトフォルダ。"""
    tag: ProjectTag
    repo_path: Path            # 対応表から引いたリポジトリのパス

    @property
    def is_null(self) -> bool:
        return False


class NullProject(Project):
    """解決できなかったことを表す Null Object。処理側は is_null で判定する。"""


class Worktree(BaseModel):
    """カード処理用に構築された作業環境。"""
    path: Path
    branch: BranchName         # CardId から chevuoi/trello-<shortLink> を決定的に導出
    repo_path: Path


class NodeStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"          # MVP の終端状態は2値（spec/mvp の対応表）


class NodeResult(BaseModel):
    """処理ノード（claude -p 1回）の実行結果。"""
    status: NodeStatus
    output: str                # ノードの最終出力（PR URL 等を含むテキスト）
```

タイトルからのタグ抽出やブランチ名の導出は、外部依存のない純粋なロジックとしてエンティティ／値オブジェクトに置きます。これによりプロジェクト分配の決定性を単体テストで直接検証できます。

### ポート

外部境界のうち「カード自身の操作」以外は、ドメイン層の ABC（ポート）として切り出し、インフラ層のアダプタが実装します。

```python
class CardProvider(ABC):
    """外部サービスから処理対象カードを取得する入力ポート。

    取得できた時点でカードの ID が確定し、以後の操作は
    返された Card 自身が行う。
    """

    @abstractmethod
    def fetch_ready_cards(self) -> list[Card]: ...


class WorktreeManager(ABC):
    """git worktree の構築・列挙・削除。実装はインフラ層（subprocess）。"""

    @abstractmethod
    def create(self, project: Project, card: Card) -> Worktree:
        """ブランチ名をカード ID から決定的に導出して worktree を作る。

        冪等: 同名ブランチの worktree が既にあればそれを返す（再実行対応）。
        """

    @abstractmethod
    def list_stale(self, older_than_days: int) -> list[Worktree]:
        """指定日数を経過した worktree を列挙する（経過日数ベース。終端判定はしない）。"""

    @abstractmethod
    def remove(self, worktree: Worktree) -> None: ...


class NodeRunner(ABC):
    """処理ノードの実行。実装はインフラ層（claude -p の subprocess 呼び出し）。

    プロンプトは呼び出し側（ユースケース・オーケストレーター）が決めて注入する。
    ランナーはそれを実行するだけで、内容には関与しない。
    """

    @abstractmethod
    def run(self, worktree: Worktree, prompt: str) -> NodeResult: ...
```

git worktree の操作もカード永続化ではなく外部境界（ファイルシステム・git）への作用なので、リポジトリではなくポートとして扱います。

### カード取得は「サービス」か「ポート」か

カード取得の置き場所には、ドメインサービスとポートの2つの候補があります。本設計では**ポート**とします。

- **カードのメソッドにはできない。** 取得の時点ではカードがまだ存在せず、「自分自身への操作」として表現できません。取得はカードの外側の責務です。
- **ドメインサービスにもしない。** ドメインサービスは複数エンティティにまたがる**純粋なドメインロジック**（外部依存なし）の置き場所です。カード取得は Trello への I/O そのものであり、ドメイン層に実装を置けません。
- したがって、ドメイン層には `CardProvider` という **ABC（ポート）だけ**を置き、実装（アダプタ）はインフラ層の `TrelloCardProvider` が担います。ヘキサゴナルアーキテクチャで言う driven port に相当し、依存方向（ドメインは外側を知らない）も保たれます。

取得と操作の境界は「ID の確定」です。`CardProvider` がカードを取得できた時点で ID が確定し、返された `Card` は自分のソース（Trello 上の実体）を自分で編集できるようになります。

## アプリケーション層

### RunUsecase（`vuoi run` の1巡）

仕様のフローをそのまま逐次コードに写します。コンストラクタインジェクションで依存を受け取ります。

```python
class RunUsecase:
    @inject
    def __init__(
        self,
        provider: CardProvider,
        process_card: ProcessCardUsecase,
        config: AppConfig,
    ): ...

    def execute(self) -> None:
        for card in self.provider.fetch_ready_cards():
            self.process_card.execute(card)   # カード間は独立。例外はカード単位で握る
```

### ProcessCardUsecase（カード1枚の処理）

カードへの操作はすべてカード自身のメソッド呼び出しです。ユースケースはカードがどのサービス由来かを知りません。

```python
def execute(self, card: Card) -> None:
    if not card.claim():
        logger.info("claim failed, skip: %s", card.id)
        return

    project = self.resolve_project(card)      # タグ → 対応表。決定的。解決不能なら NullProject
    if project.is_null:
        logger.warning("project not found, skip: %s", card.name)
        return

    worktree = self.worktrees.create(project, card)
    result = self.runner.run(worktree, self.build_prompt(card))  # プロンプトはユースケースが決める

    if result.status is NodeStatus.DONE:
        card.add_comment(result.output)
    else:
        card.add_comment(f"エラー: {result.output}")
    # エラーでも動作が終わったらレビューを要求する（In Progress に残すと
    # エラーなのか作業中なのか分からないため）
    card.move_to_review()
```

設計上のポイント:

- **後ろ向きの遷移を持たない。** リトライ・レビューループは実装しません。フローは常にこの一直線です。
- **例外はカード境界で止める。** ノードの異常終了や API エラーで1枚が失敗しても、`RunUsecase` は次のカードへ進みます。
- **冪等性は各具体実装の責務。** ユースケースは「クレームに失敗したらスキップ」という決定的なルールだけを持ちます。

### GcUsecase（`vuoi gc`）

`WorktreeManager.list_stale()` で指定日数を経過した worktree を列挙し、`remove()` で削除するだけの薄いユースケースです。MVP は実行状態を永続化しないため「終端済みか」の判定は行わず、経過日数だけを基準にします。

## インフラ層

TrelloClient
: Trello REST API を `httpx` で呼ぶ薄い HTTP クライアントです（仕様どおり MCP は使いません）。認証情報・リスト ID は設定から受け取ります。`TrelloCard` と `TrelloCardProvider` が共有します。

TrelloCard（`Card` の具体実装）
: Trello 上のカード ID・現在のリストを保持し、`claim()` / `add_comment()` / `move_to_review()` を `TrelloClient` 経由の REST 呼び出しで実装します。クレームは「現在のリストを確認 → In Progress へ移動」で行い、既に In Progress の場合は成功、その他のリストの場合は失敗を返して冪等性を保ちます。

TrelloCardProvider（`CardProvider` の実装）
: Ready 相当リストのカード一覧を取得し、1件ずつ `TrelloCard` を構築して返します。ここでカードの ID が確定し、以後の操作は各 `TrelloCard` 自身が行います。

GitWorktreeManager
: `git worktree add` / `list` / `remove` を `subprocess` で実行します。ブランチ名 `chevuoi/trello-<shortLink>` が既に存在する場合は既存の worktree を返し、再実行に耐えます。作成先は設定の worktree ルート配下です。

ClaudeNodeRunner
: worktree を作業ディレクトリとして `claude -p <プロンプト>` を1回実行する汎用ランナーです。プロンプトは外部（ユースケース）から注入されたものをそのまま渡し、内容には関与しません。MVP では `ProcessCardUsecase` がカードのタイトル・本文・URL と作業手順（自己レビュー・テストゲート・PR 作成で停止）を埋め込んだプロンプトを組み立てます。計画・実装・テスト・PR 作成はすべてノード内（プロンプト）に委ねます。終了コードで `done` / `failed` を判定し、標準出力を `NodeResult.output` に入れます。タイムアウトを設定から与え、超過時は `failed` とします。

### 設定（infrastructure/config/settings.py）

Pydantic でスキーマを定義し、設定ファイル（TOML）から読み込みます。仕様の「最小限の設定」をそのまま写します。

```python
class TrelloConfig(BaseModel):
    api_key: str               # 環境変数から（TRELLO_KEY / TRELLO_TOKEN）
    api_token: str
    ready_list_id: str
    in_progress_list_id: str
    in_review_list_id: str

class AppConfig(BaseModel):
    trello: TrelloConfig
    projects: dict[str, Path]  # タグ → リポジトリパスの対応表
    worktree_root: Path
    node_timeout_sec: int = 3600
```

## インターフェース層

### DI モジュール（interface/di_modules.py）

```python
class AppModule(Module):
    def __init__(self, config: AppConfig):
        self._config = config

    def configure(self, binder: Binder) -> None:
        binder.bind(AppConfig, to=self._config, scope=singleton)
        binder.bind(TrelloClient, scope=singleton)
        binder.bind(CardProvider, to=TrelloCardProvider, scope=singleton)
        binder.bind(WorktreeManager, to=GitWorktreeManager, scope=singleton)
        binder.bind(NodeRunner, to=ClaudeNodeRunner, scope=singleton)
        # ユースケースは Injector の自動解決に任せる（明示的な bind 不要）
```

`Card` 自体は DI で解決しません。エンティティはリクエストごとに `CardProvider` が構築するオブジェクトであり、コンテナで管理するのはポートの実装（アダプタ）だけです。MVP では実装の切り替え（環境別バックエンドや Strategy）が不要なため、`bind` は上記が中心です。Strategy パターンの導入は、v1.0.0 以降でノードが複数種類（計画・実装・レビュー）に分かれた時点で `NodeRunner` の解決に適用します。

### CLI（interfaces/cli/main.py）

`argparse` のサブコマンドで `run` / `gc` を提供します（依存を増やさないため CLI フレームワークは使いません）。

```python
def main() -> int:
    args = parse_args()                       # run | gc --older-than N
    config = load_config(args.config)
    injector = Injector([AppModule(config)])
    match args.command:
        case "run":
            injector.get(RunUsecase).execute()
        case "gc":
            injector.get(GcUsecase).execute(older_than_days=args.older_than)
    return 0
```

`vuoi run` は1巡で終了し、常駐は外部のスーパーバイザに任せます（{doc}`../spec/mvp`）。

## テスト戦略

- **ドメイン層**: タグ抽出・ブランチ名導出などの純粋ロジックを外部依存なしで単体テストします。
- **アプリケーション層**: `Card` のインメモリ実装（`FakeCard`: 操作を記録するだけ）と `CardProvider` / `WorktreeManager` / `NodeRunner` のモックを Injector で注入し、フローの分岐（クレーム失敗・タグ不明・ノード失敗）を網羅します。カードが抽象データ型なので、外部サービスを一切立てずにカード操作の呼び出し履歴を検証できます。
- **インフラ層**: Trello はモックサーバ（`httpx` のトランスポート差し替え）で `TrelloCard` / `TrelloCardProvider` を結合テストし、git は一時リポジトリで検証します。`claude -p` の実体呼び出しはテスト対象外とし、コマンド組み立てのみ検証します。

## 依存パッケージ

```toml
[project]
dependencies = [
    "injector>=0.22.0",
    "pydantic>=2.0",
    "httpx>=0.27",
]

[project.scripts]
vuoi = "chevuoi.interfaces.cli.main:main"
```
