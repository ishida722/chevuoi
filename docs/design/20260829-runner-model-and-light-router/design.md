# Runner のモデル指定とルーターの軽量モデル化

## 背景・目的（何を解決する設計か）

`ClaudeWorkflowRouter` はカードの内容から候補ワークフローを 1 つ選ぶだけの単純なタスクですが、`claude -p` にモデルを渡していないため、Claude Code の既定モデル（重いモデル）が毎回使われています。カード 1 枚ごとに必ず 1 回走る処理なので、コストとレイテンシの無駄が積み上がります。

一方で、ルーターが使う `Runner`（`ClaudeCliRunner`）にはそもそもモデルを指定する手段がありません。この設計は次の 2 点を解決します。

1. `Runner` にモデルを指定できるようにする（ルーター以外のワークフローからも使える汎用機能として）。
2. ルーターが設定で指定した軽量モデルで動くようにする。

## スコープ（対象 / 対象外）

対象:

- SDK の `Runner.run` 契約へのモデル指定の追加と、`ClaudeCliRunner` での `--model` 付与
- ルーター用モデルの設定項目（`config.toml`）と、`ClaudeWorkflowRouter` からの指定
- 上記に伴うテスト・`docs/spec/workflow-engine.md`・`docs/design/workflow-engine.md` の更新

対象外:

- `ctx.llm`（`LangchainLlmFactory` / `[llm]` 設定）のモデル切り替え。ルーターは `Runner` 経由で `claude -p` を使っており、`ctx.llm` は関係しない
- ワークフロー側ノードの既定モデルを設定で変える機能（`[runner] default_model` のような全体既定値）。必要になった時点で追加できる拡張点としてのみ記す
- `--fallback-model` の対応
- ルーターのプロンプト内容や判断ルールの変更

## 現状分析（既存コードの構造・問題点）

呼び出しの流れは次のとおりです。

```
ProcessCardUsecase
  └─ SelectWorkflowUsecase.execute(card, cwd=project.repo_path)
       └─ WorkflowRouter.route(card, candidates, cwd)        # domain/ports
            └─ ClaudeWorkflowRouter (infrastructure)
                 └─ Runner.run(prompt, cwd=..., allowed_tools=READ_ONLY_TOOLS)   # vuoi_sdk の ABC
                      └─ ClaudeCliRunner.build_command → ["claude", "-p", prompt, "--output-format", "json", ...]
```

関係するコードと現状:

- `src/vuoi_sdk/__init__.py` の `Runner.run(prompt, *, cwd, session_id, allowed_tools)`: モデルを渡す引数がない。ユーザーワークフローが import する公開契約なので、変更は後方互換でなければならない
- `src/chevuoi/infrastructure/workflows/claude_cli_runner.py`: `AppConfig` から `node_timeout_sec` だけ読む。`build_command` が CLI 引数を組み立てる唯一の場所
- `src/chevuoi/infrastructure/workflows/claude_workflow_router.py`: `Runner` だけを注入され、設定を持たない。`READ_ONLY_TOOLS` はモジュール定数
- `src/chevuoi/infrastructure/config/settings.py`: `LlmConfig.model` はあるが、これは `ctx.llm`（langchain）用で `Runner` とは無関係
- `src/chevuoi/interface/di_modules.py`: `Runner` を `ClaudeCliRunner` の singleton として 1 つだけ bind している

問題点:

- モデルを指定する経路が SDK 契約・CLI 引数組み立て・設定のどこにもない
- `Runner` が singleton 1 個のため、「ルーター用の別インスタンス」を DI で用意する方式は bind の二重化を招く
- `claude --model` はエイリアス（`sonnet` / `opus` / `fable` など）または完全名を受け付ける。エイリアスの解決先は Claude Code 側のバージョンで変わるため、chevuoi が既定値を決め打ちすると意図しないモデルに変わる可能性がある

## 設計方針（採用する原則と、その理由）

採用する原則:

1. **モデルは呼び出しごとの引数にする（`Runner.run(model=...)`）。** モデル選択は「どのプロンプトに何を使うか」という呼び出し側の関心事であり、ルーターだけでなくワークフローのノードでも「分類は軽く、実装は重く」と使い分けたくなる。Runner インスタンスの属性にすると singleton の `Runner` を複製する必要が生じ、DI 構成が複雑になる
2. **既定値は「指定しない」（`None` → `--model` を付けない）。** 既存の挙動を一切変えず、SDK 契約は省略可能なキーワード引数の追加にとどめる（`API_VERSION` は据え置き）
3. **ルーターのモデルは設定ファイルで決め、コードに決め打ちしない。** `[router] model = "haiku"` のように利用者が選ぶ。Claude Code のエイリアス解決や利用可能モデルは環境依存のため、既定値をコードに埋めるのは避ける。`docs/spec/workflow-engine.md` で軽量モデルの指定を推奨する（README.md は現状空ファイルのため、設定の説明は `[llm]` の説明がある spec 側に置く）
4. **設定の読み方は既存の流儀に揃える。** `ClaudeWorkflowRouter` に `AppConfig` を注入する（`ClaudeCliRunner` / `LangchainLlmFactory` と同じ）。設計指針の `StrategySettings` のような専用設定クラスは、`AppConfig` の 1 セクション（`RouterConfig`）として表現する

設計指針から取捨選択したもの:

- Repository パターン・Strategy パターンは新設しない。今回の変更は既存ポート（`Runner` / `WorkflowRouter`）の引数追加と設定追加であり、新たなアルゴリズムの切り替え軸はない。「モデル」は Strategy ではなく Runner に渡すパラメータとして扱う
- 新規レイヤー・ディレクトリは追加しない。既存の `domain / application / infrastructure / interface / interfaces` に収まる
- DI モジュールの変更は、`ClaudeWorkflowRouter` の依存が増えても `bind(WorkflowRouter, to=ClaudeWorkflowRouter)` のまま Injector が自動解決するので不要（設計指針の「bind の活用」に沿う）

## レイヤー構成とディレクトリ構造

新規ファイルは作りません。変更対象のみ示します。

```
src/
├── vuoi_sdk/__init__.py                                  # Runner.run に model 引数を追加（SDK 契約）
└── chevuoi/
    ├── infrastructure/
    │   ├── config/settings.py                            # RouterConfig 追加、AppConfig.router 追加
    │   └── workflows/
    │       ├── claude_cli_runner.py                      # build_command / run で --model を付与
    │       └── claude_workflow_router.py                 # AppConfig を注入し model を渡す
    └── interface/di_modules.py                           # 変更なし（自動解決）
tests/unit/
    ├── test_claude_cli_runner.py                         # --model のテスト追加
    ├── test_workflow_router.py                           # ScriptedRunner に model を追加、渡されることを検証
    ├── test_sdk_context.py                               # NoopRunner のシグネチャに model を追加
    └── test_settings.py                                  # [router] のロード・既定値のテスト追加
tests/integration/test_workflow_infra.py                  # FakeRunner のシグネチャに model を追加
docs/spec/workflow-engine.md                              # Runner 契約の model 引数、[router] の設定例
docs/design/workflow-engine.md                            # Runner / Router の記述に追記
```

## 主要コンポーネント

### SDK: `Runner`（契約の拡張）

```python
# src/vuoi_sdk/__init__.py
class Runner(ABC):
    @abstractmethod
    def run(
        self,
        prompt: str,
        *,
        cwd: Path | None = None,
        session_id: str | None = None,
        allowed_tools: Sequence[str] | None = None,
        model: str | None = None,
    ) -> RunResult:
        """
        model: 使用するモデル。Claude Code のエイリアス（"haiku" / "sonnet" 等）または
               完全名。None なら Claude Code の既定に従う。
        """
```

`RunResult` は変更しません（モデル名は Claude Code の JSON 出力に含まれないため、記録は `ClaudeCliRunner` のログ行に任せる）。

### インフラ: `ClaudeCliRunner`

```python
def build_command(
    self,
    prompt: str,
    session_id: str | None,
    allowed_tools: Sequence[str] | None = None,
    model: str | None = None,
) -> list[str]:
    cmd = ["claude", "-p", prompt, "--output-format", "json"]
    if session_id is not None:
        cmd += ["--resume", session_id]
    if allowed_tools is not None:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if model is not None:
        cmd += ["--model", model]
    return cmd
```

`run` は `model` をそのまま `build_command` に渡し、`logger.info("claude 実行: ok=%s session=%s cost=%s model=%s", ...)` にモデルを含めます（コスト分析時にモデル別で集計できるようにする）。

### 設定: `RouterConfig`

```python
# src/chevuoi/infrastructure/config/settings.py
class RouterConfig(BaseModel):
    """[router] の内容。ワークフロー選択（ClaudeWorkflowRouter）専用の設定。"""

    model: str | None = None  # 例: "haiku"。None なら Claude Code の既定モデル


class AppConfig(BaseModel):
    ...
    router: RouterConfig = RouterConfig()  # セクション省略可
```

`config.toml` の例:

```toml
[router]
model = "haiku"   # ルーターは分類だけなので軽量モデルで十分
```

### インフラ: `ClaudeWorkflowRouter`

```python
class ClaudeWorkflowRouter(WorkflowRouter):
    @inject
    def __init__(self, runner: Runner, config: AppConfig) -> None:
        self._runner = runner
        self._model = config.router.model

    def route(self, card, candidates, *, cwd=None) -> RoutingDecision:
        ...
        result = self._runner.run(
            self.build_prompt(card, candidates),
            cwd=cwd,
            allowed_tools=READ_ONLY_TOOLS,
            model=self._model,
        )
        ...
```

`WorkflowRouter` ポート（ドメイン）と `SelectWorkflowUsecase` は変更しません。モデルは「どう Claude を呼ぶか」というインフラの関心事であり、ユースケースから見える契約に載せません。

### DI モジュール

変更なし。`ClaudeWorkflowRouter` のコンストラクタ引数に `AppConfig` が増えても、`AppConfig` はすでに singleton で bind されているため Injector が自動解決します。

## 依存関係

- 依存の方向は現状のまま: `interfaces → interface(DI) → infrastructure → application → domain`、および `infrastructure → vuoi_sdk`
- `vuoi_sdk` はホストに依存しない。`model` 引数は文字列のみで、chevuoi の設定型を持ち込まない
- ドメイン層（`WorkflowRouter` / `RoutingDecision`）は変更せず、モデルという実装詳細を持たせない
- 外部ライブラリの追加はない。`claude` CLI の `--model` オプションに依存する（現行の Claude Code で提供されているオプション）

## 実装手順（dev ワークフローにそのまま渡せる粒度のステップ）

1. **Runner にモデル指定を追加する**
   - `src/vuoi_sdk/__init__.py`: `Runner.run` に `model: str | None = None` を追加し docstring を更新
   - `src/chevuoi/infrastructure/workflows/claude_cli_runner.py`: `build_command` / `run` に `model` を通し、`--model` を付与。ログ行に model を含める
   - `tests/unit/test_claude_cli_runner.py`: `model` あり／なしで `--model` の有無を検証するテストを追加
   - `Runner` を実装するフェイク 3 つのシグネチャに `model=None` を追加する: `tests/unit/test_workflow_router.py` の `ScriptedRunner`、`tests/unit/test_sdk_context.py` の `NoopRunner`、`tests/integration/test_workflow_infra.py` の `FakeRunner`（ABC は引数の追加を強制しないので、追加しないと手順 3 で `ScriptedRunner` だけが `TypeError` になる）
   - `docs/design/workflow-engine.md` の runner 節と `docs/spec/workflow-engine.md` の `Runner` の説明に `model` を追記
   - 受け入れ条件: 既存テスト全通過。`model=None` のときコマンド列が現状と完全一致

2. **`[router]` 設定を追加する**
   - `src/chevuoi/infrastructure/config/settings.py`: `RouterConfig` と `AppConfig.router` を追加
   - `tests/unit/test_settings.py`: `[router]` 省略時に `router.model is None`、指定時に読み込まれることを検証
   - `docs/spec/workflow-engine.md`: `[llm]` の説明の近くに `[router] model = "haiku"` の設定例と、軽量モデル推奨の一文を追記（README.md は空なので対象外）
   - 受け入れ条件: `[router]` セクションのない既存の `config.toml` がそのままロードできる

3. **ルーターが設定のモデルで実行する**
   - `src/chevuoi/infrastructure/workflows/claude_workflow_router.py`: `AppConfig` を注入し `run(model=...)` に渡す
   - `tests/unit/test_workflow_router.py`: `ScriptedRunner.calls` に `model` を記録し、`router.model` が渡ること・未設定なら `None` が渡ることを検証。テストの `ClaudeWorkflowRouter(runner)` 呼び出しを `AppConfig` 付きに更新（`test_claude_cli_runner.py` の `make_config` を `tests/unit/fakes.py` などへ共通化してよい）
   - `docs/design/workflow-engine.md` のルーター節に「モデルは `[router] model` で指定」を追記
   - 動作確認: `vuoi workflow select "<title>" "<desc>"` を `[router] model = "haiku"` で実行し、ログの `model=haiku` と `cost_usd` の低下を確認
   - 受け入れ条件: 設定なしでの挙動が現状と同一

## 検討した代替案と却下理由

| 代替案 | 却下理由 |
|---|---|
| ルーター専用の `Runner` インスタンスを DI で別に bind する（例: `Annotated[Runner, "router"]` の名前付き bind や `RouterRunner` サブクラス） | `Runner` の bind が 2 系統になり DI 構成が煩雑になる。モデルは呼び出しごとに変わりうる関心事で、インスタンス単位で固定するとワークフロー内での使い分けに再度手を入れることになる |
| `ClaudeCliRunner` のコンストラクタで既定モデルを受け取り、`Runner` 契約は変えない | ルーターだけでなく各ノードも同じ singleton を使うため、「ルーターだけ軽量」が実現できない |
| `Runner.with_model(model) -> Runner` のような派生インスタンス生成 API | 契約が増えるわりに `run(model=...)` と表現力が同じ。フェイク実装の負担も増える |
| ルーターの既定モデルをコードで `"haiku"` に固定する | Claude Code のエイリアス解決先と利用可能モデルは環境・バージョン依存。chevuoi が黙って変えるより、利用者が設定で明示するほうが安全。README で推奨にとどめる |
| ルーターを `claude -p` ではなく `ctx.llm`（langchain）に置き換えて `[llm]` のモデルを使う | ルーターはリポジトリの読み取り（`Read` / `Grep` / `Glob`）を許可しており、ツールなしの単発 LLM 呼び出しでは同じ判断ができない。`[llm]` は任意設定でもある |
| `RoutingDecision` や `RunResult` に使用モデルを持たせる | Claude Code の JSON 出力にモデル名は含まれず、chevuoi が渡した値を書き戻すだけになる。ログ行で十分 |

## 未決事項・リスク

- **エイリアスの解決先**: `haiku` などのエイリアスは Claude Code 側で最新モデルに解決される。挙動を固定したい場合は完全名（例: `claude-haiku-4-5-20251001`）を設定する。`docs/spec/workflow-engine.md` に両方の書き方を載せる
- **軽量モデルでのルーティング精度**: 分類精度が落ちると棄権（`needs_human`）や誤選択が増える可能性がある。`SelectWorkflowUsecase` は判断をログに残しているので、導入後に経路 × 終端状態の混同行列で比較し、必要なら `sonnet` に戻す。判断基準の数値目標は未決
- **`--resume` と `--model` の併用**: セッション継続時に別モデルを指定した場合の Claude Code の挙動（途中でモデルが切り替わる）は未確認。ルーターはセッション継続を使わないため今回の範囲では問題ないが、ワークフローが両方を渡す場合の注意書きを SDK docstring に入れるかは実装時に判断
- **`Runner` を実装するユーザーコードへの影響**: ホストが `model` を渡すのはルーターだけなので、`model` を受け取らない外部 `Runner` 実装があっても現状のワークフロー実行は壊れない。ただし契約上は実装すべき引数となるため、SDK 仕様書に明記する
- **既存コードの気付き（本設計では直さない）**: `LlmConfig.model` のコメントに「例: claude-sonnet-5」とあり、`[llm]` と新設 `[router]` で「model」の意味（langchain のモデル ID か Claude Code の `--model` 値か）が異なる。`docs/spec/workflow-engine.md` で両者の違いを一文で区別しておくとよい
- **`ClaudeWorkflowRouter` に注入する型（判断が分かれる点）**: 本設計は既存の `ClaudeCliRunner` / `LangchainLlmFactory` に揃えて `AppConfig` 全体を注入する。設計指針の `StrategySettings` のように `RouterConfig` だけを注入する形にすれば依存が最小になるが、`di_modules.py` に `binder.bind(RouterConfig, to=config.router)` の 1 行が必要になる。設定セクションが増えて `AppConfig` 丸ごと注入が目立ってきた時点で、セクション単位の bind に切り替える
- **モデル設定の置き場（判断が分かれる点）**: `[router]` セクションを新設する代わりに、トップレベルの `router_model` や既存の `workflow_defaults` に載せる案もある。`workflow_defaults` はワークフローの `ctx.settings` に渡る利用者向け設定であり、ホスト内部のルーターの設定とは意味が違うため採らない。トップレベル項目にしないのは、将来ルーター関連の設定（確信度の扱い、候補の上限など）が増えたときにまとめられるようにするため
- **ワークフロー全体の既定モデル**: 将来 `[runner] model` のような全体既定値を置く場合は、`ClaudeCliRunner` が `config.runner.model` を既定とし、`run(model=...)` の明示指定が優先する形にできる。今回は要件がないため入れない
