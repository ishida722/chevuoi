# カード発行機能（ワークフローからの副次タスク起票）設計

## 背景・目的（何を解決する設計か）

ワークフローの実行中に見つかった範囲外の作業（無関係なバグ・技術的負債・先に必要な準備）を、新しいカードとして起票する仕組みを作る。仕様 {doc}`../../spec/proposals` が定める「副次タスクの起票（Proposals）」の実装設計にあたる。

解決したいことは 2 つある。

1. **カード発行サービスがない。** 現在のホストはカードを「取得・クレーム・コメント・移動」できるが、新規作成はできない。現在取り組んでいるプロジェクトのタグを付けてカードを発行するサービスが要る。
2. **ワークフローとの接続が難しい。** ワークフローはユーザーが書く。追加タスクを受け取る口をユーザー側に重い契約として押しつけると、誰も使わない。ユーザーは「見つけた」と一言告げるだけで、起票の実務（重複排除・上限・冪等性・Inbox への配置）はすべてホストが担う形にする。

## スコープ（対象 / 対象外）

対象:

- SDK（`vuoi_sdk`）に、ワークフローから追加タスクを申告する口を追加する
- ホスト側で申告を収集し、仕様の歯止め（Inbox 起票・世代深度・1 ラン上限・重複排除・冪等性）をかけて Trello にカードを発行する
- 発行したカードをプロジェクトタグ付きのタイトルにし、親カードへリンクする
- 手動でカードを発行する CLI（`vuoi card issue`）— 発行サービスの単体動作確認用

対象外:

- Trello 以外のタスクソースへの発行（ポートで抽象化するが実装は Trello のみ）
- ワークフロー内で LLM に proposal を書かせるプロンプト設計そのもの（SDK にプロンプト断片と抽出ヘルパーは用意するが、使うかどうかはワークフローの自由）
- 起票候補の evidence が指すファイルの実在検証（仕様にはあるが、MVP では未決事項に回す。理由は後述）
- 類似カードの意味的検索（タイトル完全一致 + 冪等キーのみ）

## 現状分析（既存コードの構造・問題点）

### 構造

ワークフローとホストの接点は `vuoi_sdk.WorkflowContext` に集約されている。

- `ctx.runner` / `ctx.llm` / `ctx.settings` / `ctx.logger` はロード時（`PythonWorkflowLoader._build_context`）に固定される。コンパイル済みグラフは `WorkflowRegistry` にキャッシュされるため、`ctx` は**実行をまたいで共有**される。
- 実行ごとに変わる `workdir` / `project` は `ContextVar` で束縛する（`bind_workdir` / `bind_project`）。`LangGraphExecutor.execute` が `ExitStack` で束縛し、ワークフローは `ctx.workdir` / `ctx.project` のプロパティで読む。並列実行（`RunUsecase` の `ThreadPoolExecutor`）でも混ざらない。
- ワークフローがホストへ返すものは最終 state の `blocked` / `result` だけで、`ExecutionResult` に写し取られ、`ProcessCardUsecase.finalize` が決定的に終端処理を決める。

カード側は `Card`（抽象データ型）が自分自身への操作を持ち、取得だけが `CardProvider` ポートに切り出されている（{doc}`../mvp` 「カード取得は『サービス』か『ポート』か」）。Trello 実装は `TrelloClient`（httpx）→ `TrelloCard` / `TrelloCardProvider`。

### 問題点

1. **カードを作る経路がない。** `TrelloClient.post` は存在するが、`POST /cards` を呼ぶ実装もポートもない。`TrelloConfig` にも Inbox リストの ID がない。
2. **ワークフローから「値」を返す経路は state キーだけ。** `blocked` / `result` は最終 state の文字列で十分だったが、proposal は「複数ノードから随時追加される複数件のリスト」で、state キー方式だと、ユーザーは自分の `State` TypedDict に reducer 付きキーを足し、各ノードの戻り値に積む必要がある。これは負担が大きく、また `ProcessCardUsecase` 以外に `ExecutionResult` の消費者が増えるほど、state を覗く箇所が散らばる。
3. **`Card` に世代深度・親参照がない。** 仕様 {doc}`../../spec/task-sources` は「世代深度」「親タスクへの参照」をチケット情報に含めるが、`Card` ABC と `TrelloCard` は持っていない。深度 2 以上からの起票禁止を実装できない。
4. **設計指針との対応。** このプロジェクトは指針の `domain/repositories` の代わりに `domain/ports` を使う（既存設計 {doc}`../mvp` の判断）。本設計もそれに従う。

## 設計方針（採用する原則と、その理由）

1. **申告の口は `ctx.propose()` 1 つにする（ContextVar 収集）。** ユーザーは任意のノード・任意のヘルパー関数から `ctx.propose("タイトル", body="...")` を呼ぶだけでよい。state の拡張も戻り値の加工も不要で、既存の `workdir` / `project` と同じ「実行ごとにホストが束縛する」流儀に揃う。LangGraph の並列ノード実行は `contextvars` をコピーして走るため（`workdir` が既に依存している前提。設計時に langgraph 1.2.11 で START から 3 ノードへ分岐するグラフで確認済み）、同じ実行の申告は同じ収集先に集まる。
2. **判断はホスト、申告はワークフロー。** 何を起票するかはワークフロー（とその中の LLM）が決めるが、起票するか否か・どこへ・何件までは、ホストが決定的に決める（INV-1: 遷移判断に LLM を使わない、を踏襲）。歯止め（Inbox・深度・上限・重複・冪等）は `IssueProposalsUsecase` に集約し、ワークフローからは見えない。
3. **失敗は値で返し、本流を止めない。** 起票の失敗は親カードの終端処理を妨げない。起票結果（作成 / 既存 / 破棄）は `IssueReport` として返し、親カードのコメントに載せる。
4. **発行はポート、Card ADT は変えない。** 「まだ存在しないカードを作る」操作は `CardProvider` と同じ理由でカードのメソッドにできないので、`CardIssuer` ポートにする。戻り値は `IssuedCard`（ID と URL だけの値オブジェクト）とし、`Card` は返さない。発行直後のカードにホストが行う操作は無く（コメントは親カードに付ける）、`Card` を返すには `TrelloCard` の構築に必要な `idList` 等を余分に取り回すことになるため。既存カードへの「再発を確認した」コメント（仕様の重複排除）は MVP では行わない（未決事項参照）。
5. **SDK はホストを知らない。** `vuoi_sdk` 側の `Proposal` は素の dataclass、ホスト側の `TaskProposal` は Pydantic モデルとし、`LangGraphExecutor` が写し取る（`_to_info` と同じ流儀）。
6. **Repository パターンは採らない。** 指針の Repository は永続化 CRUD の抽象だが、本機能に永続化はない（冪等キーはカード本文に埋め込み、Trello 自体を真実源とする）。Strategy パターンも、切り替えたいアルゴリズムが今のところ「発行先（Trello）」だけであり、それは既に `CardIssuer` ポートの bind で切り替えられるため、別途ファクトリは置かない。
7. **Injector の `bind` だけで配線する。** `CardIssuer → TrelloCardIssuer` を `binder.bind` で宣言し、ユースケースは `@inject` の自動解決に任せる。`@provider` は不要。

## レイヤー構成とディレクトリ構造

追加・変更するファイルのみ示す（`★` 新規、`*` 変更）。

```
src/
├── vuoi_sdk/
│   └── __init__.py                          * Proposal / bind_proposals / ctx.propose /
│                                              PROPOSAL_PROMPT / ctx.propose_from_output
└── chevuoi/
    ├── domain/
    │   ├── entities/
    │   │   ├── card.py                      * generation / parent_id プロパティ（既定値付き）
    │   │   ├── task_proposal.py             ★ TaskProposal / ProposalKind / proposal_key
    │   │   └── issue_report.py              ★ IssueReport / IssuedCard
    │   ├── ports/
    │   │   ├── card_issuer.py               ★ CardIssuer（ABC）/ CardIssueRequest
    │   │   └── graph_executor.py            * ExecutionResult.proposals
    │   ├── services/
    │   │   └── proposal_policy.py           ★ 純粋関数: 上限・深度・重複の選別
    │   └── exceptions/__init__.py           * CardIssueError
    ├── application/
    │   └── usecases/
    │       ├── issue_card_usecase.py        ★ 1 枚発行（CLI とパイプラインの共通口）
    │       ├── issue_proposals_usecase.py   ★ 申告リスト → 歯止め → 発行 → IssueReport
    │       └── process_card_usecase.py      * 実行後に IssueProposalsUsecase を呼ぶ
    │           （run_workflow_usecase.py は変更不要。ExecutionResult.proposals をそのまま返す）
    ├── infrastructure/
    │   ├── config/settings.py               * TrelloConfig.inbox_list_id / ProposalsConfig
    │   ├── trello/
    │   │   ├── trello_card.py               * 本文フッターから generation / parent_id を読む
    │   │   └── trello_card_issuer.py        ★ POST /cards + 冪等キー検索
    │   └── workflows/langgraph_executor.py  * bind_proposals で収集し ExecutionResult へ
    ├── interface/di_modules.py              * CardIssuer の bind
    └── interfaces/cli/main.py               * vuoi card issue / workflow run の申告表示
```

## 主要コンポーネント

### SDK（vuoi_sdk）— ユーザーが触る唯一の面

````python
@dataclass(frozen=True)
class Proposal:
    """ワークフローが申告する追加タスク。起票するかどうかはホストが決める。"""

    title: str
    body: str = ""
    kind: Literal["bug", "chore", "spike", "debt"] = "chore"
    evidence: tuple[str, ...] = ()   # 例: ("src/foo.py:142",)


_proposals: ContextVar[list[Proposal] | None] = ContextVar("vuoi_proposals", default=None)


@contextmanager
def bind_proposals(sink: list[Proposal]) -> Iterator[None]:
    """ホストが 1 回の実行に申告の収集先を束縛する。"""
    token = _proposals.set(sink)
    try:
        yield
    finally:
        _proposals.reset(token)


PROPOSAL_PROMPT = """\
作業中に範囲外の問題（無関係なバグ・技術的負債・先に必要な準備）を見つけたら、
その場で直さずに次の形式で報告し、本来の作業を続けてください:

```vuoi-proposal
{"title": "...", "kind": "bug|chore|spike|debt", "evidence": ["path:line"], "body": "..."}
```
"""


@dataclass(frozen=True)
class WorkflowContext:
    ...  # 既存フィールド

    def propose(
        self,
        title: str,
        *,
        body: str = "",
        kind: str = "chore",
        evidence: Sequence[str] = (),
    ) -> None:
        """追加タスクを申告する。収集先が無い実行（束縛外）ではログに残して捨てる。"""
        sink = _proposals.get()
        proposal = Proposal(title=title.strip(), body=body, kind=kind, evidence=tuple(evidence))
        if sink is None:
            # ctx.logger は Any（テストでは None を渡している）ので、無ければ SDK のロガーに落とす
            log = self.logger or logging.getLogger("vuoi_sdk")
            log.warning("proposal は収集先がないため捨てます: %s", proposal.title)
            return
        sink.append(proposal)

    def propose_from_output(self, text: str) -> int:
        """runner の出力から ```vuoi-proposal``` ブロックを抜き出して申告する。件数を返す。
        JSON として壊れたブロックは警告して読み飛ばす。"""
````

ユーザーの負担は次の 2 段階から選べる。

- **最小**: `ctx.propose("flaky なテストがある", evidence=["tests/test_x.py:10"])` の 1 行。
- **LLM 任せ**: プロンプトに `PROPOSAL_PROMPT` を連結し、`ctx.propose_from_output(r.output)` を 1 行呼ぶ。

`WorkflowContext` は dataclass なのでメソッド追加は既存ワークフローを壊さない（仕様 §4）。

### ドメイン層

エンティティ:

```python
# domain/entities/task_proposal.py
ProposalKind = Literal["bug", "chore", "spike", "debt"]


class TaskProposal(BaseModel):
    """ワークフローから受け取った起票候補（ホスト側表現）。"""

    model_config = {"frozen": True}

    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    kind: ProposalKind = "chore"
    evidence: tuple[str, ...] = ()

    def key(self, parent: CardId | None) -> str:
        """冪等キー。親カード ID + 正規化タイトルの sha1 先頭 12 桁。決定的。"""
```

```python
# domain/entities/issue_report.py
class IssuedCard(BaseModel):
    id: CardId
    url: str
    created: bool          # False なら既存カードを再利用した


class IssueReport(BaseModel):
    issued: list[IssuedCard] = []
    skipped: list[str] = []      # 破棄理由つき（"上限超過: <title>" など）
    summary_card: IssuedCard | None = None   # 上限超過時の要約カード

    def to_comment(self) -> str: ...   # 親カードへのコメント文
```

`Card` ABC への追加（既定値付きで、既存実装を壊さない）:

```python
class Card(ABC):
    ...
    @property
    def generation(self) -> int:
        """世代深度。人間起票 = 0。自動起票されるたびに +1。"""
        return 0

    @property
    def parent_id(self) -> CardId | None:
        return None
```

ポート:

```python
# domain/ports/card_issuer.py
class CardIssueRequest(BaseModel):
    title: str                       # タグ付与前のタイトル
    body: str
    project_tag: ProjectTag
    idempotency_key: str             # 本文に埋め込む。同キーがあれば再利用
    generation: int = 0
    parent: CardId | None = None
    parent_url: str = ""


class CardIssuer(ABC):
    """タスクソースに新規カードを作る出力ポート。取得と同じく、
    まだ存在しないカードの操作なので Card のメソッドにはできない。"""

    @abstractmethod
    def find_by_key(self, key: str) -> IssuedCard | None:
        """冪等キーを本文に持つ既存カードを探す。"""

    @abstractmethod
    def issue(self, request: CardIssueRequest) -> IssuedCard:
        """Inbox 相当のリストにカードを作る。既に同キーがあればそれを返す（冪等）。"""
```

`ExecutionResult` への追加:

```python
class ExecutionResult(BaseModel):
    ...
    proposals: list[TaskProposal] = []
```

ドメインサービス（純粋関数。外部依存なし、単体テスト対象）:

```python
# domain/services/proposal_policy.py
class PolicyResult(BaseModel):
    accepted: list[TaskProposal]
    rejected: list[tuple[TaskProposal, str]]
    overflow: int            # 上限で切り捨てた件数（要約カードの根拠）


def select_proposals(
    proposals: list[TaskProposal], *, parent_generation: int, max_per_run: int, max_generation: int
) -> PolicyResult:
    """仕様 proposals の歯止めを適用する。
    - parent_generation >= max_generation なら全件 rejected（深度制限）
    - 同一タイトル（casefold・空白正規化）は先勝ちで 1 件にまとめる
    - max_per_run を超えた分は overflow に数える
    """
```

### アプリケーション層

```python
class IssueCardUsecase:
    """カード 1 枚の発行。CLI（vuoi card issue）と IssueProposalsUsecase が共用する。
    タイトルに "<TAG> " を前置し、本文末尾に機械可読フッターを付けて CardIssuer へ渡す。"""

    @inject
    def __init__(self, issuer: CardIssuer) -> None: ...

    def execute(
        self, proposal: TaskProposal, project: Project, *, parent: Card | None = None
    ) -> IssuedCard: ...
```

```python
class IssueProposalsUsecase:
    """ラン終了時の起票。終端状態に関わらず呼ばれる（blocked でも起票する。仕様）。
    失敗は IssueReport.skipped に落とし、例外を外へ出さない。"""

    @inject
    def __init__(self, issue_card: IssueCardUsecase, config: AppConfig) -> None: ...

    def execute(
        self, proposals: list[TaskProposal], project: Project, parent: Card
    ) -> IssueReport:
        policy = select_proposals(
            proposals,
            parent_generation=parent.generation,
            max_per_run=self._config.proposals.max_per_run,
            max_generation=self._config.proposals.max_generation,
        )
        # accepted を順に issue_card.execute。overflow > 0 なら
        # 「N 件の問題を検出」要約カードを 1 枚作る（本文に切り捨てた title 一覧）。
        # 要約カードも冪等にするため、キーは TaskProposal(title="<summary>").key(parent) で
        # 親ごとに固定する（再実行で要約カードが増えない）
```

`ProcessCardUsecase.execute` の変更点は、コンストラクタに `proposals: IssueProposalsUsecase` を足すこと（`@inject` の自動解決。`tests/unit/test_usecases.py` の手動構築箇所も引数が増える）と、次の差し込みだけにとどめる。起票は `finalize`（PR 作成を含む）より**前**に行い、`finalize` が例外を投げても起票結果を失わないよう `report` は `try` の外で初期化する。

```python
report = IssueReport()                                                     # ★ 追加（try の外）
try:
    ...
    result = self.executor.execute(workflow, ..., workdir=worktree.path, project=project)
    report = self.proposals.execute(result.proposals, project, parent=card)   # ★ 追加（例外を出さない）
    comment = self.finalize(card, meta.outcome, worktree, result)
except Exception as e:
    ...
    comment = f"🤖 エラー: {e}"
if report.issued or report.summary_card or report.skipped:
    comment += "\n\n" + report.to_comment()                                 # ★ 追加（正常・エラー両経路）
```

`_truncate_comment` は末尾を優先して残すので、コメント末尾に置いた起票結果は切り詰めでも消えない。

`RunWorkflowUsecase`（`vuoi workflow run`）はプロジェクトが無いため起票せず、`ExecutionResult.proposals` をそのまま CLI に返して表示する。ワークフロー作者が手元で申告内容を確認できる。

### インフラ層

`TrelloCardIssuer`:

- `issue`: `find_by_key` で既存を探し、無ければ `POST /cards`（`idList=inbox_list_id`, `name`, `desc`）。戻り値の `shortLink` / `url` から `IssuedCard` を作る。
- `find_by_key`: Inbox リストのカード一覧（`GET /lists/{inbox}/cards?fields=desc,shortLink,url`）を取り、本文フッターの `key=` を照合する。1 ランで最大 4 枚（上限 3 + 要約 1）しか発行しないので、毎回一覧を取り直す素朴な実装で足りる（一覧のキャッシュは置かない）。Trello の検索 API は索引更新が遅延し、テストでも再現しにくいため使わない。Inbox 以外（人間が Ready へ動かした後）は探さないので、その場合は二重起票になりうる（未決事項参照）。
- 本文フッター（機械可読・人間可読を兼ねる）:

  ```
  ---
  vuoi: key=3f9a1c2b7d4e parent=trello:VsK3d4Jp generation=1 kind=bug
  親カード: https://trello.com/c/VsK3d4Jp
  ```

`TrelloCard` は本文末尾の `vuoi:` 行を正規表現で読み、`generation` / `parent_id` を返す。行が無ければ既定値（0 / None）。

設定:

```python
class TrelloConfig(BaseModel):
    ...
    inbox_list_id: str | None = None   # 未設定なら起票を無効化して警告（既存設定を壊さない）


class ProposalsConfig(BaseModel):
    max_per_run: int = 3
    max_generation: int = 2   # この深度以上の親からは起票しない（仕様: 深度 2 以上禁止。
                              # 人間起票=0 → 自動起票=1 → その自動起票=2 で止まる）


class AppConfig(BaseModel):
    ...
    proposals: ProposalsConfig = ProposalsConfig()
```

`LangGraphExecutor.execute` は `bind_workdir` / `bind_project` と並べて `bind_proposals(sink)` を常に束縛し、`invoke` 後に `sink` を `TaskProposal` へ写して `ExecutionResult.proposals` に入れる。写す際に Pydantic の検証に落ちた申告（空タイトルなど）は警告して落とす。

### DI モジュール

```python
binder.bind(CardIssuer, to=TrelloCardIssuer, scope=singleton)  # type: ignore[type-abstract]
# IssueCardUsecase / IssueProposalsUsecase は @inject の自動解決に任せる（bind 不要）
```

`inbox_list_id` 未設定時は `TrelloCardIssuer.issue` が `CardIssueError` を投げ、`IssueProposalsUsecase` がそれを `skipped` に落とす。DI で `NullCardIssuer` に差し替える案より、設定不備をコメントで可視化できるこの形を採る。

### CLI

```
vuoi card issue <TAG> <title> [--body TEXT] [--kind bug|chore|spike|debt]
```

`IssueCardUsecase` を親カードなし（generation=0）で呼ぶ。発行サービスを単体で確認する用途で、`vuoi workflow select` と同じ位置づけ。

## 依存関係

- 依存の方向は既存どおり Interface → Infrastructure → Application → Domain のみ。
- `vuoi_sdk` を import してよいのはインフラ層と interface 層だけ（現状も `langgraph_executor.py` / `claude_cli_runner.py` / `di_modules.py` が使っている。本設計では `langgraph_executor.py` が `bind_proposals` と `Proposal` を使う）。ドメインの `TaskProposal` は SDK の `Proposal` を知らない。
- ドメイン層の外部依存は Pydantic のみ。`proposal_policy.py` と `TaskProposal.key` は標準ライブラリ（`hashlib` / `re`）だけで書く。
- 新規外部ライブラリは追加しない。Trello 通信は既存の `TrelloClient`（httpx）を使う。
- 検算: `grep -rn 'vuoi_sdk\|langgraph\|httpx' src/chevuoi/{domain,application}` が空であること（現状は空。設計時に確認済み）。

## 実装手順（dev ワークフローにそのまま渡せる粒度）

各ステップが 1 PR。1 → 2 → 3 の順に依存し、4・5 は 3 の後なら独立して進められる。

1. **SDK と収集経路** — `vuoi_sdk` に `Proposal` / `bind_proposals` / `WorkflowContext.propose` を追加。ドメインに `TaskProposal` と `ExecutionResult.proposals` を追加。`LangGraphExecutor` が `bind_proposals` で収集して結果に写す。`vuoi workflow run` が申告を表示する。テスト: `test_sdk_context.py` に束縛外での捨て動作と並列実行での分離、`test_run_workflow_usecase.py` に `ctx.propose` を呼ぶグラフから `proposals` が得られること。
2. **発行ポートと Trello 実装** — `CardIssuer` / `CardIssueRequest` / `IssuedCard` / `CardIssueError`、`TrelloConfig.inbox_list_id`、`TrelloCardIssuer`（`POST /cards`、フッター生成、`find_by_key`）、`TrelloCard.generation` / `parent_id`、DI の bind、`IssueCardUsecase`、CLI `vuoi card issue`。テスト: `test_trello.py` の `MockTransport` に `/1/cards` POST と Inbox 一覧を足し、冪等（2 回目は POST しない）とフッターの往復（書いた `generation` を `TrelloCard` が読める）を確認。`test_settings.py` に `inbox_list_id` 省略時の既定値。
3. **歯止めとパイプライン接続** — `proposal_policy.select_proposals`（純粋関数）、`ProposalsConfig`、`IssueReport`、`IssueProposalsUsecase`、`ProcessCardUsecase` への接続（blocked でも呼ぶ・失敗を本流に出さない・コメント追記）。テスト: `test_domain.py` に上限・深度・重複の各ケース、`test_usecases.py` に `FakeCardIssuer` を `fakes.py` へ追加して、起票 URL がコメントに載ること、`CardIssueError` が親カードの終端処理を妨げないこと、上限超過で要約カードが 1 枚だけ作られること。
4. **LLM 出力からの抽出ヘルパー** — `PROPOSAL_PROMPT` と `WorkflowContext.propose_from_output`。テスト: 正常ブロック・壊れた JSON・ブロック無しの 3 ケース。docs/spec/workflow-engine.md §4 に `propose` / `propose_from_output` の契約を追記。
5. **ドキュメントと利用例** — `docs/index.md` の toctree に本設計を追加し、spec/proposals.md に「evidence の実在検証は未実装」を注記。`docs/spec/workflow-engine.md` の「runner を使う例」に `propose_from_output` を 2 行足した例を追加。

## 検討した代替案と却下理由

| 案 | 内容 | 却下理由 |
|---|---|---|
| state キー方式 | `BaseState` に `proposals: Annotated[list, operator.add]` を足し、各ノードが戻り値に積む | ユーザーが自分の `State` を継承していれば自動で入るが、ノードごとに戻り値へ積む手間があり、ヘルパー関数の奥からは申告できない。`ctx.propose` のほうが負担が小さい。`blocked` / `result` と流儀が揃わない点は、「単一の値（state）」と「随時追加されるリスト（収集）」で性質が違うと整理した |
| `ctx` に発行サービスを直接注入 | `ctx.issuer.issue(...)` でワークフローが直接カードを作る | ワークフロー（LLM）が起票の可否を握ることになり、仕様の歯止め（Inbox・上限・深度）をワークフローごとに再実装させることになる。INV-1 にも反する |
| Trello ラベルで project を表す | 発行時に project ラベルを付ける | 既存の運用と `resolve_project` はタイトル先頭のタグで解決しており、ラベルを増やすと真実源が 2 つになる。タイトル前置で統一する |
| Trello 検索 API で重複排除 | `GET /search?query=` で類似カードを探す | 索引の遅延で冪等性が保証できず、テストでも再現しにくい。Inbox 一覧のフッター照合で決定的に判定する |
| 発行先を `Strategy` + ファクトリで切り替える | 指針のパターン 4 | 発行先は `CardIssuer` の bind で十分切り替えられ、タスクソースはアプリ全体で 1 つ（`CardProvider` と同じ）。ファクトリは過剰 |
| `NullCardIssuer` を DI で差し替える | `inbox_list_id` 未設定時に無音で無効化 | 設定不備が見えなくなる。`CardIssueError` を `IssueReport.skipped` に落としてコメントに出すほうが運用で気づける |

## 未決事項・リスク

- **evidence の実在検証**（仕様 proposals「根拠が空、または指すファイルが実在しない候補は破棄」）。worktree のパス基準で `path:line` を検証する形になるが、`IssueProposalsUsecase` は worktree を受け取っていない。ステップ 3 で `worktree.path` を渡すか、`ProcessCardUsecase` 側で先に除外するかを実装時に決める。MVP では evidence を任意とし、検証しない。
- **Inbox 外へ移動したカードとの重複。** 人間が Inbox → Ready へ動かした後に同じ親カードを再実行すると、`find_by_key` は Inbox しか見ないため二重起票になる。再実行は例外的（クラッシュ後の再開）なので許容するが、必要なら Ready / In Progress も検索対象に足す。
- **LangGraph の並列ノードと ContextVar。** `bind_workdir` が既に同じ前提に立っているが、ユーザーが自前の `ThreadPoolExecutor` をノード内で使うと、そのスレッドには収集先が伝わらず申告が捨てられる（警告ログは出る）。SDK のドキュメントに注記する。
- **`Card` ABC のプロパティ追加。** `AdhocCard` / `FakeCard` は既定値で動くが、将来 `Card` を実装するタスクソース（Jira 等）はフッター相当の格納場所を各自決める必要がある。
- **要約カードの上限との関係。** 上限超過時の要約カード自体を上限の 1 枚に数えるかは仕様にない。数えない（accepted 3 枚 + 要約 1 枚）とする。
- **既存カードへの「再発を確認した」コメント。** 仕様の重複排除は「見つかったら既存チケットへコメント」を求めるが、`CardIssuer.issue` は `IssuedCard`（値）を返す設計にしたため、再利用したカードにコメントする経路が無い。MVP では `IssueReport.to_comment` の中で「既存: <url>」と親カード側に書くにとどめる。必要になれば `CardIssuer` に `comment(key, text)` を足すか、`issue` が `Card` を返す形に改める。
- **`abandoned` の扱い。** 仕様 proposals は「`abandoned` では親へのリンクを外して起票する」とするが、ホストの終端状態は現状 `pr` / `comment` × `blocked` だけで `abandoned` を判定する経路が無い（仕様 outcomes の `abandoned` 自体が未実装）。本設計では常に親にリンクする。`abandoned` を実装するタスクで、`IssueProposalsUsecase.execute(parent=None)` を許す形に広げる。
- **`vuoi card issue` の冪等性。** CLI 発行は親が無いので冪等キーが「タイトルのみ」に依存し、同じタイトルで 2 回叩くと 2 回目は既存カードを返す。動作確認用途では望ましい挙動だが、意図的に同名カードを複数作りたい場合は使えない。`--force` を足すかは実装時に判断する。
- **`propose_from_output` の抽出契約。** `vuoi-proposal` ブロックの JSON に `title` が無い・型が違うといった「JSON としては正しいが契約に合わない」出力の扱い（警告して読み飛ばす）は正常系と同じく警告ログのみとし、ワークフローには件数（戻り値）だけを返す。ワークフローが件数 0 を異常として扱うかは作者の判断に委ねる。
- **既存コードで見つけた改善点（本設計では直さない）**: `ProcessCardUsecase.execute` は `card.claim()` 失敗時と project 未解決時に何もコメントせず `return` するため、仕様 outcomes の `unmapped_tag`（needs_human）がカードに反映されない。別タスクで扱う。
- **`docs/index.md` の toctree** には本設計を登録していない（本タスクはソース非変更のため設計書のみ作成）。ステップ 5 で登録する。
