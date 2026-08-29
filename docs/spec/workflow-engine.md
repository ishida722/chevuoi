# ワークフロー機構（ユーザー定義グラフ）

ユーザーが LangGraph のグラフを自分で定義し、vuoi（ホストアプリケーション）がそれを読み込んで実行するための仕組みの仕様です。ソフト本体のコードとユーザーのグラフ定義を完全に分離します。

- 対象バージョン: `api_version = 1`
- 状態: MVP（最小実装）

本章は「何をするか」の契約を定めます。レイヤー配置・クラス設計は {doc}`../design/workflow-engine` で扱います。

## 1. 設計原則

| 原則 | 内容 |
|---|---|
| **compile はホストの責務** | ユーザーは未コンパイルの `StateGraph` を返す。checkpointer / store / interrupt はインフラ関心事なのでホストが握る |
| **メタデータはコード外** | `workflow.toml` に置く。一覧・検索・無効化にコード実行を要さない |
| **単一の真実源** | 名前 = ディレクトリ名。`api_version` は TOML のみ。二重管理を作らない |
| **失敗の隔離** | 1 つのワークフローの破損が他とホストに波及しない |
| **決定的な選択** | 選択結果はファイル走査順に依存しない。曖昧なら例外を投げる |
| **依存性注入** | ユーザーは LLM・設定・ロガーを `ctx` から受け取る。自前で作らない |

## 2. ディレクトリ構造

### 探索場所

`$XDG_CONFIG_HOME/vuoi/workflows`（未設定時は `~/.config/vuoi/workflows`）**のみ**。

MVP では探索パスを 1 つに固定します。複数パス・プロジェクトローカル探索は将来拡張です。

### レイアウト

```
~/.config/vuoi/workflows/
├── research/
│   ├── workflow.toml        # 必須：メタデータ
│   ├── workflow.py          # 必須：build() の定義
│   └── prompts.py           # 任意：相対 import で参照可
├── summarize/
│   ├── workflow.toml
│   └── workflow.py
└── _draft/                  # "_" 始まりは無視される
    └── ...
```

### 規則

| 項目 | 規則 |
|---|---|
| ワークフローの単位 | 直下のディレクトリ 1 つ（**ファイル単体は不可**） |
| 名前 | ディレクトリ名。`^[a-z][a-z0-9_]*$` |
| メタデータ | 直下の `workflow.toml`（必須） |
| エントリ | 直下の `workflow.py`（`entry` フィールドで変更可） |
| 無視対象 | `_` または `.` で始まるディレクトリ、ディレクトリでないもの |
| `__init__.py` | **不要**。`submodule_search_locations` で相対 import が有効になる |

### フォルダ必須にする理由

- 探索・import の分岐が消えて実装が単純になる（`foo.py` と `foo/` の優先順位を決めずに済む）
- メタデータ・補助モジュール・プロンプトの置き場所が自然に確保される
- ワークフロー 1 つの削除・複製・共有が `rm -r` / `cp -r` / zip で完結する
- 後から単一ファイルを許可するのは非破壊的。逆は既存ユーザーを壊す

## 3. `workflow.toml` 仕様

### 完全な例

```toml
api_version = 1

# --- 同一性 ---
version = "0.2.0"
enabled = true

# --- 説明（人間・LLM 向け）---
summary = "ウェブを検索して調査レポートを作成する"
when_to_use = """
未知のトピックについて外部情報を集めて要約する必要があるとき。
手元にある文書を要約するだけなら summarize を使うこと。
"""

# --- 決定的ディスパッチ用 ---
tags     = ["research", "web", "long-running"]
intents  = ["research.web", "research.deep"]
priority = 50

# --- 実行特性 ---
[capabilities]
requires_network  = true
streaming         = true
estimated_seconds = 90

# --- ワークフロー固有設定（ctx.settings に注入される）---
[settings]
max_depth   = 3
max_results = 10
```

### フィールド定義

| フィールド | 型 | 必須 | 既定値 | 説明 |
|---|---|---|---|---|
| `api_version` | int | ✅ | — | 契約バージョン。ホストと不一致なら読み込み拒否 |
| `summary` | str | ✅ | — | 1 行の説明。空文字は不可 |
| `version` | str | | `"0.0.0"` | ワークフロー自身のバージョン |
| `enabled` | bool | | `true` | 無効化フラグ |
| `when_to_use` | str | | `""` | LLM ルーター向けの詳細な使用条件 |
| `tags` | list[str] | | `[]` | 分類ラベル。`^[a-z0-9][a-z0-9_-]*$`。多対多 |
| `intents` | list[str] | | `[]` | 直接指名キー。`^[a-z0-9][a-z0-9_.-]*$`。**全体で一意** |
| `priority` | int | | `50` | 同点時の tie-break。大きいほど優先 |
| `outcome` | str | | `"pr"` | 終端処理の宣言。`pr`: 差分があれば PR を作る / `comment`: 結果をカードにコメントするだけ |
| `capabilities` | table | | `{}` | 実行特性。呼び出し側の事前判断に使う |
| `settings` | table | | `{}` | 任意の設定値。`ctx.settings` にマージされる |
| `entry` | str | | `"workflow.py"` | エントリファイル名 |

`capabilities` のキー: `requires_network` (bool), `streaming` (bool), `estimated_seconds` (int)。

### `intents` と `tags` の役割分担

この 2 つを分離することが決定性の基礎になります。

| | `intents` | `tags` |
|---|---|---|
| 一意性 | **全体で一意（強制）** | 重複可 |
| 一致方法 | 完全一致 | 集合フィルタ |
| 結果件数 | 0 or 1 | 0 以上 |
| 用途 | 「このワークフローを名指しで呼ぶ」 | 「条件に合うものを絞り込む」 |

1 つのフィールドで兼ねると「タグ指定したら 2 件返った、どちらを使う？」という非決定性が必ず発生します。

### `when_to_use` の書き方

LLM ルーターに渡す前提のフィールドです。**「〜のときは使わない、代わりに X を使う」という否定条件**を含めると精度が大きく改善します。

### 検証ルール

- **未知のフィールドはエラー**。typo（`when_to_used` 等）が黙って無視されるのを防ぐ
- `api_version` がホストと不一致ならエラー
- `summary` が空ならエラー
- `tags` / `intents` が正規表現に合わなければエラー
- `capabilities` に未知キーがあればエラー
- ディレクトリ名が名前規則に合わなければエラー

## 4. SDK（契約）

ユーザーが import する唯一の公開インターフェースです。パッケージ名は `vuoi_sdk`。

```python
# vuoi_sdk/__init__.py
API_VERSION = 1


class BaseState(TypedDict):
    """ホストが読み書きを保証するキー。ユーザーはこれを継承して拡張する"""
    messages: Annotated[list[BaseMessage], add_messages]


@dataclass(frozen=True)
class RunResult:
    """Runner による Claude Code 1 回の実行結果。失敗も例外ではなくこの型で返る"""
    ok: bool
    output: str
    session_id: str | None = None
    cost_usd: float | None = None


class Runner(ABC):
    """Claude Code を非対話で 1 回実行するポート。実装はホスト側"""
    @abstractmethod
    def run(self, prompt: str, *, cwd: Path | None = None,
            session_id: str | None = None,
            allowed_tools: Sequence[str] | None = None,
            model: str | None = None) -> RunResult: ...


@dataclass(frozen=True)
class ProjectInfo:
    """実行対象プロジェクト（ホストの config.toml [projects.<tag>] から供給）"""
    name: str
    path: Path
    test_commands: tuple[str, ...] = ()   # テストゲートの中身


@dataclass(frozen=True)
class Proposal:
    """ワークフローが申告する追加タスク。起票するかどうかはホストが決める"""
    title: str
    body: str = ""
    kind: Literal["bug", "chore", "spike", "debt"] = "chore"
    evidence: tuple[str, ...] = ()   # 例: ("src/foo.py:142",)


PROPOSAL_PROMPT: str   # LLM に ```vuoi-proposal``` ブロックで報告させるプロンプト断片


@dataclass(frozen=True)
class WorkflowContext:
    """依存性注入。ユーザーは自前で LLM や接続を作らない"""
    llm: BaseChatModel | None
    settings: Mapping[str, Any]
    logger: Any
    runner: Runner

    @property
    def workdir(self) -> Path: ...   # この実行の作業ディレクトリ（ホストが束縛）

    @property
    def project(self) -> ProjectInfo | None: ...   # 対象プロジェクト（ホストが束縛）

    def propose(self, title: str, *, body: str = "", kind: str = "chore",
                evidence: Sequence[str] = ()) -> None: ...   # 追加タスクを申告

    def propose_from_output(self, text: str) -> int: ...   # 出力中の vuoi-proposal ブロックを申告


__all__ = ["API_VERSION", "PROPOSAL_PROMPT", "BaseState", "ProjectInfo", "Proposal",
           "RunResult", "Runner", "WorkflowContext", "bind_project", "bind_proposals",
           "bind_workdir", "StateGraph", "START", "END"]
```

`WorkflowContext` は dataclass なので、フィールドの**追加**は既存ワークフローを壊しません。

- **`runner`**: ノードの主作業（ツールを使うエージェント実行）に使う。前回の `RunResult.session_id` を `session_id` に渡すと文脈を継続できる。タイムアウト・ログ・コスト記録はホスト側の実装が担う。`model` に Claude Code のエイリアス（`"haiku"` / `"sonnet"` 等）または完全名（例: `"claude-haiku-4-5-20251001"`）を渡すと、その呼び出しだけモデルを切り替えられる（`None` なら Claude Code の既定）。分類は軽く・実装は重く、といった使い分けをノード単位でできる。`Runner` を自前実装する場合もこの引数を受け取ること
- **`llm`**: 軽い 1 発呼び出し（分類・要約・構造化出力）向け。設定に `[llm]` が無ければ `None` になり、runner だけで完結するワークフローは `[llm]` なしで動く
- **`workdir`**: この実行の作業ディレクトリ。`vuoi run` ではカードの worktree、`vuoi workflow run` では実行ディレクトリ。`ctx.runner.run(cwd=ctx.workdir)` や subprocess の `cwd` に渡す。ホストが実行ごとに束縛する（ContextVar）ので、並列実行でも混ざらず、コンパイル済みグラフのキャッシュも保てる
- **`project`**: 対象プロジェクトの情報。`vuoi run` ではカードのタグで解決したプロジェクト、`vuoi workflow run` など対象が無い実行では `None`。**ゲートの中身（`test_commands`）はプロジェクトが持ち、ゲートを置くか・何回試すかはワークフローが決める**（{doc}`routes`）。ゲート有りのワークフローは未設定時に通過扱いにせず `blocked` で止める
- **`propose(title, *, body, kind, evidence)`**: 作業中に見つけた範囲外の問題を追加タスクとして申告する（{doc}`proposals`）。任意のノード・ヘルパー関数から呼べる。起票するか・どこへ・何件まではホストが決め、`vuoi run` では終端状態に関わらずラン終了時に Inbox へ起票して結果を親カードにコメントする。`vuoi workflow run` では起票せず申告内容を表示するだけ。ホストの束縛外（自前のスレッドプール内など）で呼ぶと警告ログを出して捨てる
- **`propose_from_output(text)`**: runner の出力から ```` ```vuoi-proposal ```` ブロック（JSON: `title` 必須、`kind` / `evidence` / `body` 任意）を抜き出して `propose` する。申告した件数を返す。壊れた JSON や `title` の無いブロックは警告して読み飛ばす。プロンプトには `PROPOSAL_PROMPT` を連結して LLM に形式を指示する
- **推奨 state キー**: ホストの終端処理は最終 state の `blocked`（空でなければ撤退理由）と `result`（人間向け要約。PR 本文・カードコメントに使われる）を読む
- `tools` は将来拡張

## 5. ユーザーが書くコード

### 契約

```
build(ctx: WorkflowContext) -> StateGraph
```

- `build` という名前の呼び出し可能オブジェクトが `workflow.py` のトップレベルに必要
- 戻り値は**未コンパイルの** `StateGraph`。`compile()` を呼んではいけない
- `WORKFLOW_API_VERSION` などのモジュール定数は**不要**（TOML が単一の真実源）

### 最小例

```python
# ~/.config/vuoi/workflows/hello/workflow.py
from vuoi_sdk import BaseState, WorkflowContext, StateGraph, START, END


def build(ctx: WorkflowContext) -> StateGraph:
    g = StateGraph(BaseState)

    def chat(state: BaseState):
        return {"messages": [ctx.llm.invoke(state["messages"])]}

    g.add_node("chat", chat)
    g.add_edge(START, "chat")
    g.add_edge("chat", END)
    return g          # compile はホストが行う
```

### runner を使う例（Claude Code をノードにする）

```python
from vuoi_sdk import PROPOSAL_PROMPT


class State(BaseState):
    session_id: str | None


def build(ctx: WorkflowContext) -> StateGraph:
    g = StateGraph(State)

    def implement(state: State):
        r = ctx.runner.run(
            "テストを通す実装をして\n\n" + PROPOSAL_PROMPT,   # 範囲外の問題は直さず報告させる
            cwd=ctx.workdir, session_id=state.get("session_id"),
        )
        if not r.ok:
            ctx.logger.error("実行失敗: %s", r.output)
        ctx.propose_from_output(r.output)   # 報告された問題をホストに申告（起票はホストが判断）
        return {"session_id": r.session_id}

    g.add_node("implement", implement)
    g.add_edge(START, "implement")
    g.add_edge("implement", END)
    return g
```

### 状態の拡張と設定の利用

```python
class State(BaseState):
    depth: int


def build(ctx: WorkflowContext) -> StateGraph:
    max_depth = ctx.settings["max_depth"]     # TOML の [settings] から
    g = StateGraph(State)
    ...
```

## 6. ロード処理

### 二段階ロード

| 段階 | 処理 | コード実行 | タイミング |
|---|---|---|---|
| **スキャン** | 全ディレクトリの `workflow.toml` を解析 | なし | 起動時 |
| **ロード** | `workflow.py` を import → `build()` → `compile()` | あり | 初回使用時（遅延） |

スキャンでコードを実行しないことで、以下が成立します。

- 壊れたワークフローも一覧に名前と状態を表示できる
- 無効化されたワークフローは一切 import されない
- 依存パッケージ未導入のワークフローも一覧に出る
- ワークフローが 100 個あっても起動が遅くならない

### import の実装要件

| 要件 | 理由 |
|---|---|
| `sys.path` に追加**しない** | ユーザーが `types.py` / `logging.py` を作ると標準ライブラリを影から上書きする |
| `spec_from_file_location` でパス直指定 | 上記の汚染を回避する唯一の方法 |
| モジュール名を `vuoi_workflows.<name>` に名前空間化 | `sys.modules` はグローバル。プレフィックスで衝突を原理的に防ぐ |
| `submodule_search_locations=[str(dir)]` を渡す | これがないと `from . import prompts` が `ImportError` になる |
| `exec_module` の**前**に `sys.modules` へ登録 | dataclass / pickle / `get_type_hints()` がモジュール解決に依存する |
| 失敗時は `sys.modules` から purge | サブモジュールも含めて除去しないと、次回リトライで壊れた状態が再利用される |
| `sys.path` を finally で巻き戻す | ユーザーコードがトップレベルで汚染する可能性がある |

### エラー隔離の方針

- **`except BaseException`**: `SystemExit`（ユーザーが `sys.exit()` を書く）を拾うため。`except Exception` では素通りする
- **`KeyboardInterrupt` のみ再送出**: 握り潰すと Ctrl-C が効かなくなる
- **失敗しても他は続行**: ロード関数は例外を投げず、失敗を戻り値で表現する

### 隔離できないもの（MVP では許容）

同一プロセス内である限り以下は防げません。ユーザー自身がワークフローを書く前提なので MVP では許容します。

- 無限ループ・ハング（例外ではないので `except` に来ない）
- セグメンテーション違反（C 拡張のバグ）
- `os._exit()`
- メモリ枯渇
- トップレベルで起動されたデーモンスレッドの残留

第三者からワークフローを受け取る運用になった時点で、サブプロセス検証（別プロセスで import を試し、タイムアウトと異常終了を検出）を追加します。

## 7. 選択とディスパッチ

### 全順序

すべての一覧・選択は `(-priority, name)` でソートします。ファイルシステムの走査順に依存させません。

### API

```python
reg.list(include_disabled=False) -> list[WorkflowMeta]
reg.by_intent(intent: str)       -> WorkflowMeta          # 0 or 1 件。無ければ例外
reg.by_tags(require=, exclude=, capabilities=) -> list[WorkflowMeta]
reg.resolve_one(**kw)            -> WorkflowMeta          # 1 件に定まらなければ例外
reg.get(name: str)               -> CompiledStateGraph    # 遅延ロード + キャッシュ
```

### 曖昧さの扱い

`resolve_one` は候補が 0 件なら `WorkflowNotFound`、最高 priority が同点で複数なら `AmbiguousSelection` を投げます。

**silent fallback を入れない。** 決定性の要件は「常に同じ結果が返る」だけでなく「決まらないときはそう言う」を含みます。勝手に 1 つ選ぶのは、決定性を装った非決定性になります。

### intent 衝突の扱い

スキャン時に intent の一意性を検証し、重複していたら**関係する全ワークフローを無効化**して `invalid` に入れます。

片方を勝たせるとディレクトリ走査順に依存してしまうため、エラーとして表面化させユーザーに修正させます。

### LLM ルーティングとの接続

`summary` / `when_to_use` をそのままプロンプトに渡し、**LLM には名前だけを出力させて `reg.get(name)` で引きます**。これで非決定性が「どれを選ぶか」の一点に封じ込められます。

カードからの選択は 3 層で行います（{doc}`triage` と同じ構造）:

1. **決定的**: タイトルに `[<intent>]` マーカーがあれば `by_intent`（LLM 不使用）
2. **LLM 分類**: 有効なワークフロー全件の `name` / `summary` / `when_to_use` とカードのタイトル・本文を渡し、名前 1 つ・確信度（high / low）・理由を JSON で返させる。読み取り系ツールのみ許可
3. **棄権**: 返った名前が候補に無い、解析不能、確信度が high でない → 選択なし（`needs_human`）。必ずどれかを選ばされるルーターは必ず間違えるため、棄権パスは外さない

ワークフローを増やしてもルーター側の変更は不要で、精度は各ワークフローの `when_to_use`（特に「〜なら X を使う」という除外条件）で上げます。`vuoi workflow select <title> [desc]` で判断を手元で確認できます。

ルーターは候補から名前を 1 つ選ぶだけなので、`config.toml` の `[router]` で軽量モデルを指定することを推奨します（未指定なら Claude Code の既定モデル）:

```toml
[router]
model = "haiku"   # エイリアス。挙動を固定したければ完全名（例: "claude-haiku-4-5-20251001"）
```

`[router] model` は `claude --model` に渡す値で、`[llm] model`（`ctx.llm` 用の langchain モデル ID）とは別物です。ルーターは `ctx.llm` を使わないため、`[llm]` の有無や値は選択に影響しません。

### ワークフローが返すもの・ホストが行う終端処理

ワークフローは **成否（`blocked` 理由の有無）と人間向け要約** を返すだけで、コミット・PR 作成・カード移動は行いません。ホストは `outcome` × 差分の有無 × `blocked` の有無で決定的に終端処理を決めます:

| `outcome` | 実行後 | ホストの処理 |
|---|---|---|
| `pr` | 差分あり・blocked なし | コミット・push・PR 作成 → URL をコメント → In review |
| `pr` | 差分なし・blocked なし | PR は作らず、要約を `🤖 変更なし:` コメント → In review（`no_change`） |
| `comment` | blocked なし | 要約を `🤖 完了:` コメント → In review |
| いずれも | blocked あり | `🤖 blocked: <理由>` コメント、差分は worktree に残す（`failed_gate` / `needs_human`） |

「PR を作るべきタスクだったが結果的に変更が不要だった」は差分の有無という事実で決まり、LLM の申告には依らない。

## 8. 有効化・無効化

優先順位（上が強い）:

1. **環境変数** `VUOI_WORKFLOWS="research,summarize"` — 指定されたものだけ有効。テスト時に便利
2. **`workflow.toml` の `enabled`** — 既定の状態

ディレクトリ名を `_` 始まりにリネームすれば、スキャン対象からも外れます（完全な無効化）。

## 9. 起動時レポート

失敗を黙って握り潰すと「動かないのに理由が分からない」という最悪の体験になります。MVP でもレポートは必須です。

```
✓ ワークフロー 3 件
● research         v0.2.0   p50   #long-running #research #web
    ウェブを検索して調査レポートを作成する
    intents: research.web, research.deep
● summarize        v0.1.0   p50   #summarize
    文書を要約する
○ experiment       v0.0.1   p10   #wip
    実験用（無効）

✗ 1 件が読み込めません
✗ broken_flow      workflow.toml: 未知のフィールド ['when_to_used']
```

この出力が**コードを 1 行も実行せずに得られる**のが、メタデータを TOML に置く最大の利点です。

## 10. MVP のスコープ

### 含むもの

- `~/.config/vuoi/workflows` のみの単一探索パス
- フォルダ必須（`workflow.toml` + `workflow.py`）
- `build(ctx) -> StateGraph` 契約、compile はホスト
- TOML メタデータ（`summary` / `tags` / `intents` / `priority` / `capabilities` / `settings`）
- 二段階ロード（スキャン → 遅延コンパイル）
- 名前空間化・`sys.modules` purge・`sys.path` 巻き戻し
- ワークフロー単位のエラー隔離
- intent 一意性の検証と衝突時の無効化
- 決定的な選択 API（曖昧なら例外）
- `enabled` フラグ + 環境変数による絞り込み
- 起動時レポート

### 含まないもの（将来拡張）

| 項目 | 理由 |
|---|---|
| 複数探索パス（プロジェクトローカル等） | 単一パスで足りる |
| 単一ファイル形式 | 後から追加可能（非破壊的） |
| ホットリロード | 再起動で対応 |
| サブプロセス検証 | ユーザー自身が書く前提なら不要 |
| `overrides.toml` | 環境変数で代用可 |
| ロギング設定・シグナルハンドラの巻き戻し | 実害が出てから |
| パーミッションチェック | ローカルの `~/.config` 前提 |
| `ctx.tools`（ツール注入） | `llm` のみで開始 |
| `intents` のワイルドカード | 完全一致のみ |
| `[inputs]` 入力スキーマ | 必要になってから |
| `depends_on`（ワークフロー間依存） | 複雑さの割に出番がない |
| トレースバック整形 | 生の traceback を出す |
| `vuoi workflow init` | あると便利。優先度は高い |

## 11. 後から変えにくい決定事項

以下は互換性を壊さずに変更するのが困難なので、実装開始時点で確定させます。

1. **`api_version` は TOML のみ**。コード側にバージョン定数を置かない（二重管理は必ず矛盾する）
2. **名前 = ディレクトリ名**。TOML に `name` フィールドを作らない
3. **`build()` は未コンパイルの `StateGraph` を返す**。compile 引数をユーザーに渡さない
4. **`intents` は一意、`tags` は多対多**。この分離は後から入れ替えが効かない
5. **選択順序は `(-priority, name)` の全順序**
6. **曖昧なら例外**。緩めると「なぜかたまに違うワークフローが動く」というデバッグ困難な問題になる
7. **モジュール名前空間は `vuoi_workflows.*`**。後から変えると既存ワークフローの相対 import が壊れる
