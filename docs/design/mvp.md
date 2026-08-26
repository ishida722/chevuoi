# MVP（v1.0.0）設計

{doc}`../spec/mvp` で定めた範囲を、クリーンアーキテクチャで実装するための設計です。仕様が「何をするか」を定めるのに対し、本章は「どう作るか」（レイヤー構成・クラス設計・依存性注入）を定めます。

## 設計方針

1. **依存の方向は外側 → 内側のみ。** Interface → Infrastructure → Application → Domain の順に依存し、ドメイン層は外部ライブラリに依存しません（Pydantic のみ例外として許可）。
2. **フロー制御はアプリケーション層の決定的なコードで行う。** LLM が関与するのは処理ノードの内部だけで、ユースケースの分岐に LLM を使いません（{doc}`../spec/mvp` の原則 1）。
3. **外部境界はすべてドメイン層のインターフェース（ABC）で抽象化する。** Trello・git・`claude -p` の3つの外部依存はそれぞれリポジトリ／サービスのインターフェースとして切り出し、テスト時にはモック実装を注入します。
4. **タスクソースの汎用抽象は作らない。** MVP の仕様どおり、複数タスクソースを見据えた抽象化レイヤーは設けません。カードリポジトリのインターフェースは Trello の概念（リスト・カード）をそのまま表現し、Jira 等への拡張は v1.0.0 以降に持ち越します。インターフェース化の目的は汎用化ではなく、依存方向の維持とテスト容易性です。
5. **DI は Injector の `binder.bind()` で宣言的に行う。** ファクトリの手書きや手動ワイヤリングは避けます。

パッケージ名はリポジトリの src レイアウトに従い `chevuoi`、CLI コマンド名は仕様どおり `vuoi` とします（`pyproject.toml` の `[project.scripts]` で `vuoi = "chevuoi.interfaces.cli.main:main"` を定義）。

## ディレクトリ構造

```
src/chevuoi/
├── domain/
│   ├── entities/
│   │   ├── card.py            # Card
│   │   ├── project.py         # Project
│   │   ├── worktree.py        # Worktree
│   │   └── node_result.py     # NodeResult / NodeStatus
│   ├── value_objects/
│   │   ├── card_id.py         # CardId（Trello shortLink）
│   │   ├── project_tag.py     # ProjectTag
│   │   └── branch_name.py     # BranchName（外部 ID から決定的に導出）
│   ├── repositories/
│   │   ├── card_repository.py      # CardRepository（ABC）
│   │   └── worktree_repository.py  # WorktreeRepository（ABC）
│   ├── services/
│   │   └── node_runner.py     # NodeRunner（ABC）
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
│   ├── repositories/
│   │   ├── trello/
│   │   │   └── trello_card_repository.py  # REST API 直接（MCP 不使用）
│   │   └── git/
│   │       └── git_worktree_repository.py # subprocess で git worktree 操作
│   ├── services/
│   │   └── claude_node_runner.py          # subprocess で claude -p を1回実行
│   └── config/
│       └── settings.py                    # 設定の読み込み（Pydantic）
│
├── interface/
│   └── di_modules.py          # AppModule（Injector）
│
└── interfaces/
    └── cli/
        └── main.py            # vuoi run / vuoi gc のエントリポイント
```

## ドメイン層

### エンティティ

すべて Pydantic `BaseModel` で定義します。

```python
class Card(BaseModel):
    """処理対象の Trello カード。"""
    id: CardId                 # shortLink
    name: str
    desc: str
    url: str

    @property
    def project_tag(self) -> ProjectTag | None:
        """タイトル先頭のタグ（例: "MIRAI: ログイン修正" → MIRAI）。無ければ None。"""


class Project(BaseModel):
    """タグに紐付くプロジェクトフォルダ。"""
    tag: ProjectTag
    repo_path: Path            # 対応表から引いたリポジトリのパス


class Worktree(BaseModel):
    """カード処理用に構築された作業環境。"""
    path: Path
    branch: BranchName         # chevuoi/trello-<shortLink> を決定的に導出
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

### リポジトリ・サービスのインターフェース

```python
class CardRepository(ABC):
    """Trello のカード操作。実装はインフラ層（REST 直接）。"""

    @abstractmethod
    def list_ready_cards(self) -> list[Card]: ...

    @abstractmethod
    def claim(self, card: Card) -> bool:
        """Ready → In Progress への移動でクレームする。

        冪等: カードが既に In Progress に居る場合は成功として扱い、
        それ以外のリストに居る場合は失敗（False）を返す。
        """

    @abstractmethod
    def move_to_review(self, card: Card) -> None: ...

    @abstractmethod
    def add_comment(self, card: Card, text: str) -> None: ...


class WorktreeRepository(ABC):
    """git worktree の構築・列挙・削除。実装はインフラ層（subprocess）。"""

    @abstractmethod
    def create(self, project: Project, card: Card) -> Worktree:
        """ブランチ名をカード ID から決定的に導出して worktree を作る。

        冪等: 同名ブランチの worktree が既にあればそれを返す（再実行対応）。
        """

    @abstractmethod
    def list_finished(self, older_than_days: int) -> list[Worktree]: ...

    @abstractmethod
    def remove(self, worktree: Worktree) -> None: ...


class NodeRunner(ABC):
    """処理ノードの実行。実装はインフラ層（claude -p の subprocess 呼び出し）。"""

    @abstractmethod
    def run(self, worktree: Worktree, card: Card) -> NodeResult: ...
```

## アプリケーション層

### RunUsecase（`vuoi run` の1巡）

仕様のフローをそのまま逐次コードに写します。コンストラクタインジェクションで依存を受け取ります。

```python
class RunUsecase:
    @inject
    def __init__(
        self,
        cards: CardRepository,
        process_card: ProcessCardUsecase,
        config: AppConfig,
    ): ...

    def execute(self) -> None:
        for card in self.cards.list_ready_cards():
            self.process_card.execute(card)   # カード間は独立。例外はカード単位で握る
```

### ProcessCardUsecase（カード1枚の処理）

```python
def execute(self, card: Card) -> None:
    if not self.cards.claim(card):
        logger.info("claim failed, skip: %s", card.id)
        return

    project = self.resolve_project(card)      # タグ → 対応表。決定的
    if project is None:
        logger.warning("project not found, skip: %s", card.name)
        return                                # カードは In Progress に残る

    worktree = self.worktrees.create(project, card)
    result = self.runner.run(worktree, card)

    if result.status is NodeStatus.DONE:
        self.cards.add_comment(card, result.output)
        self.cards.move_to_review(card)
    else:
        self.cards.add_comment(card, f"エラー: {result.output}")
        # 仕様どおりカードは移動せず In Progress に残す
```

設計上のポイント:

- **後ろ向きの遷移を持たない。** リトライ・レビューループは実装しません。フローは常にこの一直線です。
- **例外はカード境界で止める。** ノードの異常終了や API エラーで1枚が失敗しても、`RunUsecase` は次のカードへ進みます。
- **冪等性は各リポジトリ実装の責務。** ユースケースは「クレームに失敗したらスキップ」という決定的なルールだけを持ちます。

### GcUsecase（`vuoi gc`）

`WorktreeRepository.list_finished()` で終端済みかつ指定日数を経過した worktree を列挙し、`remove()` で削除するだけの薄いユースケースです。

## インフラ層

TrelloCardRepository
: Trello REST API を `httpx` で直接呼びます（仕様どおり MCP は使いません）。クレームは「現在のリストを確認 → In Progress へ移動」で実装し、既に In Progress の場合は成功、その他のリストの場合は失敗を返して冪等性を保ちます。認証情報・リスト ID は設定から受け取ります。

GitWorktreeRepository
: `git worktree add` / `list` / `remove` を `subprocess` で実行します。ブランチ名 `chevuoi/trello-<shortLink>` が既に存在する場合は既存の worktree を返し、再実行に耐えます。作成先は設定の worktree ルート配下です。

ClaudeNodeRunner
: worktree を作業ディレクトリとして `claude -p <プロンプト>` を1回実行します。プロンプトはカードのタイトル・本文・URL を埋め込むテンプレートで、計画・実装・テスト・PR 作成はすべてノード内（プロンプト）に委ねます。終了コードで `done` / `failed` を判定し、標準出力を `NodeResult.output` に入れます。タイムアウトを設定から与え、超過時は `failed` とします。

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
        binder.bind(CardRepository, to=TrelloCardRepository, scope=singleton)
        binder.bind(WorktreeRepository, to=GitWorktreeRepository, scope=singleton)
        binder.bind(NodeRunner, to=ClaudeNodeRunner, scope=singleton)
        # ユースケースは Injector の自動解決に任せる（明示的な bind 不要）
```

MVP では実装の切り替え（環境別バックエンドや Strategy）が不要なため、`bind` は上記の3行が中心です。Strategy パターンの導入は、v1.0.0 以降でノードが複数種類（計画・実装・レビュー）に分かれた時点で `NodeRunner` の解決に適用します。

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
- **アプリケーション層**: `CardRepository` / `WorktreeRepository` / `NodeRunner` のモック（インメモリ実装）を Injector で注入し、フローの分岐（クレーム失敗・タグ不明・ノード失敗）を網羅します。
- **インフラ層**: Trello はモックサーバ（`httpx` のトランスポート差し替え）、git は一時リポジトリで結合テストします。`claude -p` の実体呼び出しはテスト対象外とし、コマンド組み立てのみ検証します。

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
