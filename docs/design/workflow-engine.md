# ワークフロー機構設計

{doc}`../spec/workflow-engine` で定めたワークフロー機構（ユーザー定義 LangGraph グラフの読み込み・選択・実行）を、{doc}`mvp` のクリーンアーキテクチャに載せるための設計です。仕様が「何をするか」（契約・検証ルール・決定性の要件）を定めるのに対し、本章は「どこに何を置き、どう作るか」を定めます。

## 設計方針

1. **レイヤー構成は {doc}`mvp` を踏襲する。** 依存の方向は Interface → Infrastructure → Application → Domain のみ。ドメイン層が依存してよい外部ライブラリは Pydantic だけ、という制約も維持します。
2. **LangGraph はインフラの語彙として扱う。** `StateGraph` / `CompiledStateGraph` はドメイン層・アプリケーション層の型シグネチャに現れません。コンパイル済みグラフはドメインでは不透明なハンドル（後述の `LoadedWorkflow`）として扱い、実体の型を知るのはインフラ層と SDK だけです。これにより「compile はホストの責務」という仕様の原則が、そのままレイヤー境界に一致します。
3. **メタデータの検証は純粋ロジックとしてドメインに置く。** `workflow.toml` の読み取り（I/O）はインフラ層、読み取った dict の検証（未知フィールド・正規表現・`api_version`）は Pydantic モデルのバリデーションとしてドメイン層に置きます。intent 衝突検証と決定的選択も外部依存のない純粋関数であり、単体テストで決定性を直接検証できます。
4. **SDK はホスト本体から独立したパッケージにする。** ユーザーコードが import するのは `vuoi_sdk` のみで、`chevuoi.*` を一切知りません。逆に `chevuoi` は `vuoi_sdk` の型（`WorkflowContext`）をインフラ層でのみ参照します。
5. **失敗は値で表現する。** スキャン・ロードの失敗は例外ではなく `ScanResult.invalid` / `LoadFailure` という値として上位へ返します。例外を投げるのは「呼び出し側の要求が満たせない」選択 API（`WorkflowNotFound` / `AmbiguousSelection`）だけです。

## ディレクトリ構造

追加・変更されるファイルのみ示します（既存構成は {doc}`mvp` のとおり）。

```
src/
├── vuoi_sdk/                          # ★ 新規トップレベルパッケージ（ユーザー契約）
│   └── __init__.py                    #   API_VERSION / BaseState / WorkflowContext / re-export
│
└── chevuoi/
    ├── domain/
    │   ├── entities/
    │   │   └── workflow_meta.py       # WorkflowMeta / Capabilities / ScanResult
    │   ├── value_objects/
    │   │   └── workflow_name.py       # WorkflowName / Tag / Intent（正規表現検証）
    │   ├── services/
    │   │   └── workflow_selection.py  # 純粋関数: 全順序ソート・intent 衝突検証・resolve
    │   ├── ports/
    │   │   ├── workflow_scanner.py    # WorkflowScanner（ABC）
    │   │   └── workflow_loader.py     # WorkflowLoader（ABC）/ LoadedWorkflow / LoadFailure
    │   └── exceptions/
    │       └── __init__.py            # WorkflowError / WorkflowNotFound / AmbiguousSelection を追加
    │
    ├── application/
    │   └── usecases/
    │       ├── workflow_registry.py   # WorkflowRegistry（一覧・選択・遅延ロード + キャッシュ）
    │       └── workflow_report_usecase.py  # 起動時レポート（vuoi workflow list）
    │
    ├── infrastructure/
    │   └── workflows/
    │       ├── fs_workflow_scanner.py     # ディレクトリ走査 + TOML 解析（コード実行なし）
    │       └── python_workflow_loader.py  # import 機構 + build() + compile()（エラー隔離）
    │
    ├── interface/
    │   └── di_modules.py              # バインディング追加
    └── interfaces/
        └── cli/
            └── main.py                # vuoi workflow list サブコマンド追加
```

`vuoi_sdk` を `chevuoi` の外に置くのは、ユーザーのワークフローがホストの内部構造に依存しないための処置です。同一ディストリビューションに両パッケージを含めます（`pyproject.toml` の `[tool.uv.build-backend]` で `module-name = ["chevuoi", "vuoi_sdk"]` を指定）。

## SDK（src/vuoi_sdk）

仕様 §4 をそのまま実装します。ホスト側の import 方向は一方通行です。

- `vuoi_sdk` は `langchain_core` / `langgraph` / `typing_extensions` にのみ依存する（`chevuoi` を import しない）
- `chevuoi` 側で `vuoi_sdk` を import してよいのは **インフラ層のみ**（`python_workflow_loader.py` が `WorkflowContext` を組み立てて渡す）

`API_VERSION = 1` は「SDK がどの契約を提供しているか」の宣言であり、個々のワークフローのバージョン判定には使いません（判定は TOML の `api_version` とホスト定数の比較。仕様 §11-1）。ホスト側の比較対象定数は `fs_workflow_scanner.py` ではなくドメインの `workflow_meta.py` に `SUPPORTED_API_VERSION = 1` として置き、検証ロジックと同居させます。

## ドメイン層

### WorkflowMeta（entities/workflow_meta.py）

`workflow.toml` の内容 + ディレクトリ情報を持つ Pydantic モデルです。検証ルール（仕様 §3）はすべてこのモデルのバリデーションで表現します。

```python
class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 未知キーはエラー
    requires_network: bool = False
    streaming: bool = False
    estimated_seconds: int | None = None


class WorkflowMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")   # 未知フィールドはエラー（typo 検出）

    # --- ディレクトリ由来（TOML には書かない。単一の真実源） ---
    name: WorkflowName          # ディレクトリ名。^[a-z][a-z0-9_]*$
    path: Path                  # ワークフローディレクトリの絶対パス
    entry_path: Path            # path / entry

    # --- TOML 由来 ---
    api_version: int            # SUPPORTED_API_VERSION と不一致なら ValidationError
    summary: str                # min_length=1
    version: str = "0.0.0"
    enabled: bool = True
    when_to_use: str = ""
    tags: list[Tag] = []
    intents: list[Intent] = []
    priority: int = 50
    capabilities: Capabilities = Capabilities()
    settings: dict[str, Any] = {}
    entry: str = "workflow.py"
```

`WorkflowName` / `Tag` / `Intent` は正規表現つきの値オブジェクト（`Annotated[str, StringConstraints(pattern=...)]` ベース）で、{doc}`mvp` の `ProjectTag` と同じ流儀です。

`name` / `path` / `entry_path` はスキャナが TOML の外から与えるフィールドです。TOML 側に `name` が書かれていた場合は `extra="forbid"` により未知フィールドとしてエラーになり、仕様 §11-2（名前 = ディレクトリ名、二重管理禁止）が型レベルで強制されます。

### ScanResult

```python
class ScanResult(BaseModel):
    metas: dict[str, WorkflowMeta]    # name → meta（有効・無効を問わず検証を通ったもの）
    invalid: dict[str, str]           # name → 人間可読なエラー理由
```

「TOML が壊れている」「検証エラー」「intent 衝突」はすべて `invalid` に入り、一覧表示可能なまま隔離されます。

### 選択ロジック（services/workflow_selection.py）

外部依存のない純粋関数群です。`ScanResult` と条件を受け取り、`WorkflowMeta` を返します。

```python
def sort_key(meta: WorkflowMeta) -> tuple[int, str]:
    return (-meta.priority, meta.name)          # 仕様 §7 の全順序

def check_intent_conflicts(result: ScanResult) -> ScanResult:
    """intent 重複を検出し、関係する全ワークフローを invalid へ移した新しい ScanResult を返す。
    検出順は name のソート順で決定的。"""

def list_metas(result, *, enabled: Callable[[WorkflowMeta], bool],
               include_disabled: bool = False) -> list[WorkflowMeta]: ...

def by_intent(result, intent: str, *, enabled) -> WorkflowMeta:
    """0 件なら WorkflowNotFound。intent は一意なので 2 件以上はあり得ない
    （check_intent_conflicts 通過後の ScanResult が前提）。"""

def by_tags(result, *, require=frozenset(), exclude=frozenset(),
            capabilities=None, enabled) -> list[WorkflowMeta]: ...

def resolve_one(result, *, enabled, **criteria) -> WorkflowMeta:
    """候補 0 件なら WorkflowNotFound、最高 priority 同点で複数なら AmbiguousSelection。
    silent fallback はしない（仕様 §7）。"""
```

有効判定 `enabled` を述語として注入するのは、環境変数 `VUOI_WORKFLOWS` の読み取り（I/O）をドメインから追い出すためです。述語の実体はアプリケーション層（`WorkflowRegistry`）が組み立てます。

### ポート

```python
class LoadedWorkflow(BaseModel):
    """コンパイル済みグラフの不透明ハンドル。実体（CompiledStateGraph）の型は
    インフラ層だけが知る。ドメイン・アプリケーションは graph を素通しするだけ。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: WorkflowName
    graph: Any


class LoadFailure(BaseModel):
    name: WorkflowName
    traceback: str              # 生の traceback（整形しない。仕様 §10）


class WorkflowScanner(ABC):
    """探索ディレクトリを走査し workflow.toml を解析する。コードは実行しない。"""

    @abstractmethod
    def scan(self) -> ScanResult: ...


class WorkflowLoader(ABC):
    """1 つのワークフローを import → build → compile する。例外を投げない。"""

    @abstractmethod
    def load(self, meta: WorkflowMeta) -> LoadedWorkflow | LoadFailure: ...
```

`LoadedWorkflow.graph: Any` は方針 2 の帰結です。ドメインに `CompiledStateGraph` 型を持ち込まない代償として型検査が緩みますが、コアがグラフに対して行う操作は「実行器へ渡す」だけであり、属性アクセスをしないため実害はありません。将来グラフ実行（`invoke` / `stream`）をコアから扱う必要が出た時点で、`GraphExecutor` ポートを追加して操作を抽象化します（MVP のスコープ外）。

`KeyboardInterrupt` だけは `load()` から再送出されます（Ctrl-C を殺さない。仕様 §6）。

## アプリケーション層

### WorkflowRegistry（usecases/workflow_registry.py）

仕様 §7 の API を提供する中心クラスです。スキャン結果の保持・遅延ロード・キャッシュを担い、選択ロジック自体はドメインの純粋関数へ委譲します。

```python
class WorkflowRegistry:
    @inject
    def __init__(self, scanner: WorkflowScanner, loader: WorkflowLoader,
                 config: AppConfig): ...

    def scan(self) -> ScanResult:
        """スキャン + intent 衝突検証。結果を保持する。起動時に 1 回呼ぶ。"""
        self._result = check_intent_conflicts(self._scanner.scan())
        return self._result

    # --- 選択（ドメイン関数への委譲） ---
    def list(self, include_disabled: bool = False) -> list[WorkflowMeta]: ...
    def by_intent(self, intent: str) -> WorkflowMeta: ...
    def by_tags(self, *, require=(), exclude=(), capabilities=None) -> list[WorkflowMeta]: ...
    def resolve_one(self, **criteria) -> WorkflowMeta: ...

    # --- 遅延ロード + キャッシュ ---
    def get(self, name: str) -> LoadedWorkflow:
        """初回のみ loader.load()。成功はキャッシュ、失敗（LoadFailure）は
        キャッシュせず WorkflowError として送出する（loader 側の purge により
        再試行可能なため）。無効・invalid のワークフローは WorkflowNotFound。"""
```

有効判定の述語はここで組み立てます（仕様 §8 の優先順位）:

```python
def _is_enabled(self, meta: WorkflowMeta) -> bool:
    env = os.environ.get("VUOI_WORKFLOWS")
    if env is not None:
        return meta.name in {s.strip() for s in env.split(",") if s.strip()}
    return meta.enabled
```

失敗の表現は境界で使い分けます。ポート境界（`WorkflowLoader.load`）は値（`LoadFailure`）で返し、`Registry.get()` は呼び出し側の要求が満たせないので例外（`WorkflowError`、traceback をメッセージに含む）へ変換します。「1 つの破損が他へ波及しない」隔離は、`get()` の例外をワークフロー単位で握る呼び出し側（カード処理・レポート）の責務です。

### WorkflowReportUsecase（起動時レポート）

`scan()` の結果を仕様 §9 の書式で標準出力へ整形するだけの薄いユースケースです。`registry.list(include_disabled=True)` と `ScanResult.invalid` を `(-priority, name)` 順に並べ、コードを一切実行せずに出力します。

## インフラ層

### FsWorkflowScanner（infrastructure/workflows/fs_workflow_scanner.py）

`AppConfig.workflows_dir` 直下を走査します。処理は各ディレクトリについて独立で、1 件の失敗は `invalid` に入れて続行します。

1. ディレクトリでないもの、`_` / `.` 始まりはスキップ（`invalid` にも入れない）
2. ディレクトリ名が `WorkflowName` の規則に合わなければ `invalid`
3. `workflow.toml` が無ければ `invalid`。`tomllib.load` の失敗も `invalid`
4. TOML の dict + `name` / `path` / `entry_path` で `WorkflowMeta` を構築。`ValidationError` は人間可読に要約して `invalid`（どのフィールドが未知か・どの正規表現に落ちたかを含める）
5. `entry_path` が存在しなければ `invalid`

走査順は `sorted(dir.iterdir())` で固定し、`invalid` の内容もファイルシステム順に依存させません。

### PythonWorkflowLoader（infrastructure/workflows/python_workflow_loader.py）

仕様 §6 の import 実装要件をすべてこのクラスに閉じ込めます。モジュール名前空間は `vuoi_workflows.<name>`（仕様 §11-7）。

```python
NAMESPACE = "vuoi_workflows"

class PythonWorkflowLoader(WorkflowLoader):
    @inject
    def __init__(self, config: AppConfig, llm_factory: LlmFactory): ...

    def load(self, meta: WorkflowMeta) -> LoadedWorkflow | LoadFailure:
        # 1. sys.path を退避
        # 2. spec_from_file_location(fq, meta.entry_path,
        #        submodule_search_locations=[str(meta.path)])
        # 3. sys.modules[fq] = module を exec_module の前に登録
        # 4. exec_module → build(ctx) → StateGraph 型検査 → builder.compile(name=meta.name)
        # 5. except KeyboardInterrupt: purge して再送出
        #    except BaseException: purge して LoadFailure(traceback.format_exc())
        #    finally: sys.path[:] = saved
```

`ctx` の組み立てもここで行います。`WorkflowContext`（`vuoi_sdk`）の各フィールドは:

llm
: `LlmFactory` ポート（ドメイン層に新設する小さな ABC）経由で取得します。MVP の実装は `AppConfig.llm` の設定（モデル名・API キー環境変数名）から `BaseChatModel` を 1 つ構築する `LangchainLlmFactory` です。`BaseChatModel` 型はインフラ層と SDK にしか現れません。

settings
: `{**config.workflow_defaults, **meta.settings}`。ホスト既定にワークフロー固有設定を上書きマージします。

logger
: `logging.getLogger("vuoi.workflows").getChild(meta.name)`。

checkpointer / store は MVP では `None`（compile 時に渡す口だけ用意）です。

### 設定の追加（infrastructure/config/settings.py）

```python
class LlmConfig(BaseModel):
    model: str                          # 例: "claude-sonnet-5"
    # 認証はプロバイダ既定の環境変数に委ねる

class AppConfig(BaseModel):
    ...  # 既存フィールド
    workflows_dir: Path | None = None   # None なら $XDG_CONFIG_HOME/vuoi/workflows
    llm: LlmConfig | None = None        # 未設定でもスキャン・一覧は動く
    workflow_defaults: dict[str, Any] = {}
```

`workflows_dir` の既定値解決（`XDG_CONFIG_HOME` → `~/.config/vuoi/workflows`）は設定ロード時に行い、以降のコードは常に絶対パスを受け取ります。`llm` が未設定の場合、スキャン・一覧・選択は動作し、`Registry.get()`（ロード）だけが設定エラーになります。二段階ロードの利点（コード実行なしで一覧できる）を設定面でも保つためです。

## インターフェース層

### DI（interface/di_modules.py）

```python
binder.bind(WorkflowScanner, to=FsWorkflowScanner, scope=singleton)
binder.bind(WorkflowLoader, to=PythonWorkflowLoader, scope=singleton)
binder.bind(LlmFactory, to=LangchainLlmFactory, scope=singleton)
binder.bind(WorkflowRegistry, scope=singleton)   # キャッシュを持つため singleton 必須
```

### CLI（interfaces/cli/main.py）

`vuoi workflow list` サブコマンドを追加します。`WorkflowRegistry.scan()` → `WorkflowReportUsecase.execute()` を呼ぶだけです。仕様 §12 の実装順序どおり、ここまで（スキャン + レポート）で一度動作確認できます。

`vuoi workflow init <name>`（テンプレート生成）は仕様どおり MVP 外ですが、追加時は同じサブコマンド系列に置きます。

### カード処理パイプラインとの関係

本機構は MVP では**カード処理（`ProcessCardUsecase`）と接続しません**。`vuoi workflow list` と `Registry` API の提供までが本チケットの範囲です。将来、triage・ノード実行をユーザー定義ワークフローへ委ねる場合は、`ProcessCardUsecase` が `resolve_one` / `by_intent` で `WorkflowMeta` を引き、`Registry.get()` のグラフを新設の `GraphExecutor` ポートで実行する形になります。LLM ルーティング（仕様 §7）を導入する場合も、LLM の出力は名前だけに限定し `reg.get(name)` で引くことで、chevuoi 本体の不変条件 INV-1（遷移判断に LLM を使わない）と両立させます。

## テスト戦略

- **ドメイン層（単体・外部依存なし）**: `WorkflowMeta` の検証ルール全件（未知フィールド・`api_version` 不一致・空 `summary`・正規表現違反・`capabilities` 未知キー）。`workflow_selection` の決定性 — 同一入力に対する順序安定性、intent 衝突時の全員 invalid 化、`resolve_one` の `WorkflowNotFound` / `AmbiguousSelection`、priority tie-break。
- **アプリケーション層**: `WorkflowScanner` / `WorkflowLoader` のフェイク（固定の `ScanResult` / `LoadedWorkflow` / `LoadFailure` を返す）を Injector で注入し、遅延ロード（`get` 前に `load` が呼ばれない）、キャッシュ（2 回目は `load` されない）、失敗の非キャッシュ、`VUOI_WORKFLOWS` の優先順位を検証します（`monkeypatch.setenv`）。
- **インフラ層（結合）**: `tmp_path` に実ファイルでワークフローディレクトリを構築して検証します。スキャナ — 正常系・TOML 破損・`_` 始まりスキップ。ローダ — 相対 import（`prompts.py`）の成功、`build` 欠如、compile 済みグラフを返した場合のエラー、`sys.exit()` を書いたモジュール（`BaseException` 捕捉）、失敗後の `sys.modules` purge と再ロード成功、`sys.path` 非汚染の検証。LLM はフェイクの `BaseChatModel` を注入し、実 API は呼びません。
- **CLI**: レポート書式のスナップショット的検証（有効・無効・invalid 混在の 1 ケース）。

ローダのテストはグローバル状態（`sys.modules` / `sys.path`）に触れるため、fixture で前後のスナップショットを取り差分ゼロを表明します。

## 依存パッケージ

```toml
dependencies = [
    # 既存: injector / pydantic / httpx
    "langgraph>=0.2",
    "langchain-core>=0.3",
]
```

LangGraph 系はインフラ層と `vuoi_sdk` のみが import します（ドメイン・アプリケーション層から import しないことを {doc}`mvp` の依存方向チェックと同様に grep で検算できます: `grep -rn 'langgraph\|langchain' src/chevuoi/{domain,application}` が空であること）。

## 実装順序

仕様 §12 をレイヤーに割り付けたものです。4 の時点で「認識して一覧表示する」まで到達し、手触りを確認できます。

1. `vuoi_sdk`（`BaseState` / `WorkflowContext` / re-export）+ pyproject のマルチモジュール化
2. ドメイン: `WorkflowMeta` / 値オブジェクト / 例外 / `workflow_selection`（TDD しやすい純粋部分）
3. インフラ: `FsWorkflowScanner` + 設定追加
4. アプリケーション + CLI: `WorkflowRegistry.scan` / `list` + `vuoi workflow list` レポート
5. インフラ: `PythonWorkflowLoader` + `LlmFactory`
6. アプリケーション: `Registry.get`（遅延ロード + キャッシュ）
7. 選択 API（`by_intent` / `by_tags` / `resolve_one`）の Registry 公開
