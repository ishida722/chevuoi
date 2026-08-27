# 作業ルート定義（設定ファイル化）の設計

現在の実装では、作業の流れ（どのノードをどの順で実行するか）が `ProcessCardUsecase` のコードとプロンプト定数に直接書かれています。本章では、この「作業ルート」をソースコードから切り離し、設定ファイルでグラフ構造として定義するための概念設計と、設定ファイルの構造・読み込み時のソフトウェア設計を定めます。

{doc}`../spec/routes` が定める4経路（implement / trivial / investigate / design）を、コード変更なしに設定ファイルだけで表現・追加・調整できる状態がゴールです。

## 設計方針

1. **グラフの構造はデータ、実行はコード。** ルート定義（ノード・遷移・終了条件）は設定ファイルに置き、それを解釈して実行するエンジンはアプリケーション層の決定的なコードとして1つだけ実装します。ルートを増やしてもエンジンは変わりません。
2. **LLM に分岐させない。** 遷移の判定に使えるのは、ノードの構造化出力（終了コード・ステータス・所見の有無）だけです。LLM の自由文から次のノードを推測することはしません（{doc}`../spec/mvp` の原則を維持）。
3. **停止保証を定義側に強制する。** ループ（後ろ向きの遷移）を含むルートは、ノードごとの試行上限とラン全体の時間予算を必ず宣言しなければ読み込みエラーとします。「上限のないループ」は定義できません。
4. **プロンプトは設定の一部。** ノードごとのプロンプトはテンプレートファイルとしてルート定義の隣に置き、コードから排除します。

## 概念モデル

ルートは**有向グラフ**です。構成要素は次の4つです。

ノード（node）
: 1回の作業単位。種類（kind）を持ちます。
  - `llm` — `claude -p` を1回実行する。プロンプトテンプレート・許可ツール・ターン上限を持つ。
  - `gate` — 決定的なコマンド実行（テスト・lint 等）。LLM を使わない。コマンドはプロジェクト側の設定から与える。
  - `builtin` — エンジン組み込みの決定的処理（worktree 準備、PR 作成の確認など）。名前で参照する。

遷移（edge）
: ノードの**結果**から次のノードへの対応です。結果は列挙型（`success` / `failure` / `findings` など）であり、ノード種別ごとに取りうる値が決まっています。後ろ向きの遷移（レビュー → 実装）を書けるため、ループが表現できます。

終了条件（terminal）
: 遷移先として `end:<outcome>` を指定すると、ルートはその終端状態（{doc}`../spec/outcomes` の `done` / `failed` など）で終了します。また、試行上限・時間予算の超過は、遷移によらずルートを `budget_exceeded` 系の終端状態で打ち切ります。

予算（budget）
: ノードごとの試行回数上限（`max_attempts`）と、ラン全体の時間予算（`time_budget_min`）の2層です（{doc}`../spec/routes` の「停止条件は2層」）。

## 設定ファイルの構造

ルート定義は設定ディレクトリ配下に、ルートごとに1ファイルの TOML として置きます。プロンプトは別ファイルの Markdown です。

```
~/.config/vuoi/
├── config.toml              # 既存のアプリ設定（Trello・projects 等）
└── routes/
    ├── implement/
    │   ├── route.toml       # グラフ定義
    │   └── prompts/
    │       ├── plan.md
    │       ├── implement.md
    │       └── review.md
    ├── trivial/
    │   └── route.toml       # プロンプトは他ルートから相対パスで参照してもよい
    └── investigate/
        ├── route.toml
        └── prompts/ ...
```

### route.toml のスキーマ

`implement` ルートを例にした完全な定義です。

```toml
[route]
name = "implement"
description = "計画 → 実装 → ゲート → レビュー → PR 作成"
entry = "prepare"            # 開始ノード
time_budget_min = 45         # ラン全体の時間予算（ループを含むルートでは必須）

# --- ノード定義 ---

[nodes.prepare]
kind = "builtin"
action = "prepare_worktree"
[nodes.prepare.on]
success = "plan"
failure = "end:failed"

[nodes.plan]
kind = "llm"
prompt = "prompts/plan.md"
allowed_tools = ["read", "search", "git_log"]
max_turns = 10
max_attempts = 1
[nodes.plan.on]
success = "implement"
failure = "end:failed"

[nodes.implement]
kind = "llm"
prompt = "prompts/implement.md"
allowed_tools = ["read", "edit", "write", "bash"]
max_turns = 40
max_attempts = 3             # 後ろ向き遷移で戻ってこられる回数の上限
[nodes.implement.on]
success = "gate"
failure = "end:failed"

[nodes.gate]
kind = "gate"                # コマンドはプロジェクト設定の gate_command を使う
max_attempts = 3
[nodes.gate.on]
success = "review"
failure = "implement"        # ← 後ろ向きの遷移（ループ）

[nodes.review]
kind = "llm"
prompt = "prompts/review.md"
allowed_tools = ["read"]
max_turns = 10
max_attempts = 3             # レビューは3ラウンドまで
[nodes.review.on]
success = "create_pr"        # 所見なし
findings = "implement"       # 所見あり → 実装へ戻る
failure = "end:failed"

[nodes.create_pr]
kind = "builtin"
action = "create_pr"
[nodes.create_pr.on]
success = "end:done"
failure = "end:failed"
```

ループを持たない `investigate` は、`prepare` を含めず後ろ向き遷移を1本だけ持つ、より短い定義になります。ルートの追加・変更はこのファイルを書くだけで済みます。

### スキーマの規則

- `entry` は `nodes` に存在するノード ID でなければならない。
- 遷移先は、ノード ID か `end:<outcome>` のどちらか。`<outcome>` は {doc}`../spec/outcomes` の終端状態名。
- `kind = "llm"` のノードは `prompt` / `allowed_tools` / `max_turns` が必須。
- `kind = "gate"` のノードにプロンプトは書けない（ゲートの中身はプロジェクト設定が決める）。
- 後ろ向きの遷移（グラフ上のサイクル）を含むルートは、サイクル上の全ノードの `max_attempts` と、ルートの `time_budget_min` が必須。
- ノードの結果キー（`success` 等）は kind ごとの許容集合に含まれること。`findings` を持てるのは `llm` のみ。

### プロンプトテンプレート

プロンプトファイルはプレーンな Markdown で、`{name}` `{url}` `{desc}` などのプレースホルダを使えます。使えるプレースホルダはノードへの入力宣言（{doc}`../spec/routes` の「ノードへの入力の制限」）に対応させ、宣言していない変数（前段の会話全文など）は展開できません。テンプレートに未知のプレースホルダがあれば読み込み時にエラーとします。

## 読み込みと実行のソフトウェア設計

クリーンアーキテクチャの既存構成（{doc}`mvp`）に、次の要素を追加します。

```
domain/
├── entities/
│   ├── route.py             # RouteDefinition / NodeDefinition / Edge / NodeKind（純粋データ）
│   └── route_state.py       # RouteExecution（現在ノード・試行回数・経過時間の実行時状態）
└── ports/
    └── route_repository.py  # RouteRepository（ABC）: name → RouteDefinition

application/
└── services/
    └── route_engine.py      # RouteEngine: グラフを解釈して決定的に実行

infrastructure/
└── config/
    └── toml_route_repository.py  # routes/ ディレクトリの TOML を読み検証する実装
```

### ドメイン層 — ルート定義は検証済みの純粋データ

```python
class NodeKind(StrEnum):
    LLM = "llm"
    GATE = "gate"
    BUILTIN = "builtin"

class NodeDefinition(BaseModel):
    id: str
    kind: NodeKind
    prompt_template: str | None      # 読み込み時にファイル内容へ解決済み
    allowed_tools: tuple[str, ...]
    max_turns: int | None
    max_attempts: int
    edges: dict[str, str]            # 結果 → ノード ID または "end:<outcome>"

class RouteDefinition(BaseModel):
    name: str
    entry: str
    time_budget_min: int | None
    nodes: dict[str, NodeDefinition]
```

構造の検証（参照整合・サイクル検出と上限必須・kind ごとの必須項目）は `RouteDefinition` のバリデータとしてドメイン層に置きます。TOML という形式の知識はインフラ層に閉じ、ドメイン層は「正しいグラフとは何か」だけを知ります。**不正な定義は起動時（読み込み時）に全ルート分を検証してエラーにし、カード処理の途中で発覚させません。**

### アプリケーション層 — RouteEngine

`RouteEngine` は現在の `ProcessCardUsecase` のノード実行部分を置き換える、ルート定義のインタープリタです。

```python
class RouteEngine:
    def execute(self, route: RouteDefinition, ctx: RouteContext) -> RouteOutcome:
        state = RouteExecution(route)
        node = route.nodes[route.entry]
        while True:
            if state.over_budget(node):          # 試行上限 or 時間予算の超過
                return state.budget_outcome()
            result = self._run_node(node, ctx)   # kind で NodeRunner / GateRunner / builtin に分配
            nxt = node.edges[result.key]
            if nxt.startswith("end:"):
                return RouteOutcome.from_terminal(nxt)
            node = route.nodes[nxt]
```

- 分岐に使うのは `result.key`（構造化された結果キー）だけです。`llm` ノードの `findings` 判定は、レビューノードに構造化出力（JSON）を要求し、その解析で行います。
- `_run_node` は kind ごとの実行を Strategy として持ちます。`llm` は既存の `NodeRunner` ポート、`gate` はコマンド実行、`builtin` はエンジン付属の処理（worktree 準備等）です。`NodeRunner.run` には、プロンプトに加えて `allowed_tools` / `max_turns` を渡せるようシグネチャを拡張します（`claude -p --allowedTools ... --max-turns ...`）。
- `ProcessCardUsecase` は「クレーム → プロジェクト解決 → **triage でルート選択** → `RouteEngine.execute` → 結果反映」だけになり、作業内容を知らなくなります。

### インフラ層 — TomlRouteRepository

`routes/` ディレクトリを走査し、`route.toml` の解析・プロンプトファイルの読み込み・プレースホルダ検証を行って `RouteDefinition` を構築します。パス解決（プロンプトの相対参照）とファイル I/O はこの層に閉じます。`AppConfig` には `routes_dir: Path` を追加します。

### DI

```python
binder.bind(RouteRepository, to=TomlRouteRepository, scope=singleton)
# RouteEngine は Injector の自動解決（NodeRunner / GateRunner / AppConfig を注入）
```

## 移行計画

1. **v1.1**: ルート定義の読み込みと検証（`RouteDefinition` / `TomlRouteRepository`）＋ `vuoi routes lint` サブコマンド。実行は従来どおり単一ノード。
2. **v1.2**: `RouteEngine` を導入し、現在の単一プロンプト処理を「ノード1個・遷移2本」のルート定義 `default/route.toml` として外出しする（挙動は不変。プロンプト定数をファイルへ移すだけ）。
3. **v1.3**: triage によるルート選択を導入し、{doc}`../spec/routes` の4ルートを定義ファイルとして同梱する。

この順序なら、各段階でテスト可能な小さい差分に分かれ、v1.2 までは現行の動作と完全に一致することを既存テストで確認できます。

## 採用しなかった案

- **Python コードでのルート定義（DSL / デコレータ）**: 表現力は高いが「設定ファイルで定義したい」というそもそもの目的（コードとルートの分離、非開発者による調整）に反する。
- **YAML**: アプリ設定が既に TOML（`tomllib` は標準ライブラリ）であり、依存を増やさないため TOML に統一する。
- **汎用ワークフローエンジン（LangGraph 等）の採用**: 必要なのは「小さな有向グラフ＋2層の停止条件」だけで、依存とブラックボックスを増やすコストに見合わない。エンジンは 100 行規模の while ループで足りる。
