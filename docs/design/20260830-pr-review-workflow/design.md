# PR レビューワークフロー設計

## 背景・目的（何を解決する設計か）

「この PR をレビューして」というカードを、ワークフローで処理できるようにする。レビュー結果は GitHub の PR にインラインコメント（ファイル・行に紐づくコメント）として残し、カードには要約だけを返す。

現状、PR レビューのカードは汎用の `task` ワークフロー（1 プロンプトで作業して結果をコメントで返す）が引き受けている。これには次の問題がある。

1. **レビューの観点が毎回ぶれる。** プロンプト 1 本に「レビューして」と書くだけなので、何を見るか（設計・実装・テスト・セキュリティ）が LLM 任せになる。
2. **PR コメントの投稿がプロンプト遵守頼み。** `gh` で行コメントを書くかどうか・どの形式で書くかを LLM が決めており、投稿されないことも、diff にない行を指して失敗することもある。
3. **所見を機械で検算できない。** 所見が自由文で返るため、仕様 {doc}`../../spec/gate-review` が求める「変更していない行への指摘を破棄する」といった機械検証が置けない。

本設計は 2 段階で進める。**第 1 段階**は Claude Code 組み込みの `/code-review --comment` に PR コメントの投稿まで委ねる簡易ワークフロー。**第 2 段階**は PR コメントの投稿をホストのサービスとして引き取り、レビューを粒度（設計 / コード / テスト）ごとのノードに分け、各ノードに観点を与える形に発展させる。

## スコープ（対象 / 対象外）

対象:

- 第 1 段階: `pr_review` ワークフロー（ユーザー定義、`~/.config/vuoi/workflows/pr_review/`）。PR 番号の抽出と checkout を決定的に行い、レビューと投稿は `/code-review <PR> <level> --comment` に委ねる
- 第 2 段階（ホスト）: PR コメント投稿ポート `PullRequestCommenter` と `gh api` 実装、所見の機械検証・重複排除・冪等投稿
- 第 2 段階（SDK）: ワークフローが所見を申告する口 `ctx.report_finding()`（`ctx.propose()` と同じ ContextVar 収集方式）
- 第 2 段階（ワークフロー）: 粒度ごとのレビューノード（design / code / test）と、ノードごとの観点リスト

対象外:

- レビュー結果による PR の approve / request changes（`event` は常に `COMMENT`。承認は人間の判断）
- 所見に基づく自動修正（レビュー対象は他人の PR であり、修正は `dev` ワークフローの責務）
- GitHub 以外のホスティング（ポートで抽象化するが実装は `gh` のみ）
- 仕様 gate-review の「レビューループ」（凍結・発振検知）。本設計は 1 回のレビューで終わる読み取り専用フローであり、ループは `dev` ワークフロー側の話

## 現状分析（既存コードの構造・問題点）

### 構造

- ワークフローとホストの接点は `vuoi_sdk.WorkflowContext`。実行ごとの値（`workdir` / `project`）とワークフローからホストへの申告（`propose`）は ContextVar で束縛・収集され、`LangGraphExecutor` が `ExecutionResult` に写し取る（{doc}`../20260829-card-issuing/design`）。
- `dev` ワークフローは実装後に `ctx.runner.run("/code-review <level>", session_id=None)` で公式スキルを**新規セッション**で走らせ、出力（自由文）を `apply_review` ノードで実装セッションに渡して振り分けさせている。レベルを明示する（省略すると前回入力に依存して非決定的）、`--fix` は使わない（スコープ外まで直す）という運用知見が既にある。
- ホストの GitHub 操作は `PullRequestPublisher` ポート → `GhPullRequestPublisher`（`gh pr create`）のみ。PR にコメントする経路はない。
- `/code-review` スキルは `--comment` で所見をインライン PR コメントとして投稿できる。PR 番号を対象に指定できる（`/code-review 123 medium --comment`）。

### 問題点

1. **`task` ワークフローは PR を checkout しない。** worktree はカードのブランチ（`chevuoi/...`）で作られるため、レビュー対象のコードが手元にない。`gh pr diff` で差分は読めるが、周辺コードを辿れない。
2. **所見の構造化データが存在しない。** `dev` の `code_review` ノードも出力を自由文のまま state に入れている。ホストが投稿・検証するには、ファイル・行・重大度を持つ値が要る。
3. **投稿の冪等性がない。** 同じカードを再実行（クラッシュ後の再開）すると同じコメントが二重に付く。INV-3（intent → 実行 → 記録）を守るには、ホスト側でキーを持って重複を弾く必要がある。

## 設計方針（採用する原則と、その理由）

1. **段階を踏む。第 1 段階は組み込みスキルに乗る。** `/code-review --comment` は診断・検証・投稿を一通り備えており、まず動くものを最小で作り、レビューの質と投稿の失敗パターンを実地で集めてから第 2 段階に進む。第 1 段階のワークフローは第 2 段階でも「ノードの 1 つ」として残す。
2. **決定的にできる部分は Python で行う（INV-1）。** PR 番号の抽出、`gh pr checkout`、レベルの明示、所見の機械検証、投稿の可否・件数はすべてホストまたはワークフローの Python コードが決める。LLM は所見を出すだけで、投稿するか・どこに・何件かは決めない。
3. **第 2 段階では投稿をホストに引き取る。** `propose` と同じ形（申告 → ホストが歯止め → 外部作用）で、所見の収集 `ctx.report_finding()`、検証・重複排除 `ReviewFindingPolicy`、投稿 `PullRequestCommenter` に分ける。ワークフロー作者はレビュー観点だけを書けばよく、`gh api` の呼び方や 422 の回避を知らなくてよい。
4. **レビューは粒度ごとに fresh context で走らせる（INV-4）。** 設計レビュー・コードレビュー・テストレビューを別ノード・別セッション（`session_id=None`）にする。同じセッションで観点を切り替えると前の観点の結論に引きずられる。各ノードの入力は「PR 本文 + diff + そのノードの観点リスト」だけにする。
5. **観点はデータとして持つ。** 観点リストは `workflow.toml` の `[settings]` に置き、プロンプトはそれを埋め込むだけにする。プロジェクトごとに観点を足し引きするときにコードを触らずに済む。
6. **失敗は値で返し、本流を止めない。** 投稿に失敗した所見は `ReviewReport.skipped` に理由付きで残し、カードのコメントに載せる。ワークフローの終端は `outcome = comment` で変えない。
7. **PR 本文に「レビューの要約」を 1 件だけ残す。** 行コメントは個々の所見、要約は `gh pr comment`（通常コメント）で 1 件。要約コメントも冪等キーを持つ。

## 第 1 段階: 簡易ワークフロー `pr_review`

### 配置と設定

```
~/.config/vuoi/workflows/pr_review/
├── workflow.toml
└── workflow.py
```

```toml
api_version = 1
summary = "PR レビュー: カードで指定された PR を checkout し、/code-review でレビューして PR にインラインコメントを投稿する"
version = "0.1.0"
when_to_use = "カード本文に PR 番号や PR URL があり、レビュー（指摘のみ）を求めるタスク。コードを直して PR を作るなら dev、PR をマージするなら merge を使う"
tags = ["review", "pr"]
intents = ["card.pr_review"]
outcome = "comment"

[settings]
# /code-review のレベル。必ず明示する（省略時の「前回入力」依存を避ける）
review_level = "high"
# 1 カードで扱う PR の上限（超過分はコメントに理由を残して処理しない）
max_prs = 3
```

### グラフ

```
START → parse_prs → checkout → review → advance ─┬→ checkout（次の PR）
            │            │                       └→ summarize → END
         blocked      blocked
```

parse_prs（決定的）
: `merge` ワークフローと同じ `PR_PATTERN`（`#123` / `PR 123` / PR URL）で本文から PR 番号を出現順に抽出する。0 件なら `blocked = "PR 番号が見つからない"`。`max_prs` 超過分は `skipped` に残す。抽出部は `merge` から `_shared/pr_numbers.py` に切り出して両方から import する（ワークフローディレクトリ内の相対 import は既存機構で可）。

checkout（決定的）
: `gh pr checkout <n> --detach` を worktree で実行する。PR の head ブランチが別 worktree でチェックアウト済みでも動くよう detached HEAD にする（`merge` と同じ判断）。あわせて `gh pr view <n> --json title,body,baseRefName,headRefOid` を取り state に入れる。失敗なら `blocked`。

review（LLM）
: `ctx.runner.run(f"/code-review {n} {review_level} --comment", cwd=ctx.workdir, session_id=None)`。出力（所見の一覧）を `reviews[n]` に保存する。`r.ok` が偽ならその PR は `skipped` に落とし、ワークフローは止めない（`dev` の `code_review` と同じ扱い）。`--fix` は付けない。
: 投稿の実務（行の特定・`gh` 呼び出し）はスキルに委ねる。ここで **ワークフローは投稿の成否を検証しない**（第 2 段階で解決する問題として割り切る）。

advance（決定的）
: 次の PR があれば `checkout` へ、無ければ `summarize` へ。

summarize（LLM、軽量）
: `reviews` をまとめて「PR ごとの所見件数・重大度別の内訳・主要な指摘」をカードコメント向けに要約させ `result` に入れる。`ctx.llm` があればそちら（`runner` より安い）を使い、無ければ `runner` で行う。

### 第 1 段階で確かめること

- `--comment` が diff 外の行を指したときにどう失敗するか（スキル内でリトライされるか、無言で落ちるか）
- 同じ PR に 2 回走らせたときの重複コメントの実態
- レベル別（medium / high）の所見数と誤検知率

これらは第 2 段階の `ReviewFindingPolicy` の閾値と冪等キーの設計に使う。

## 第 2 段階: ホスト側 PR コメントサービスと粒度別レビュー

### レイヤー構成とディレクトリ構造

追加・変更するファイルのみ示す（`★` 新規、`*` 変更）。

```
src/
├── vuoi_sdk/
│   └── __init__.py                         * ReviewFinding / bind_findings / ctx.report_finding / FINDING_PROMPT
│
└── chevuoi/
    ├── domain/
    │   ├── entities/
    │   │   ├── review_finding.py           ★ ReviewFinding（Pydantic）/ Severity / key
    │   │   └── review_report.py            ★ ReviewReport（posted / skipped / to_comment）
    │   ├── services/
    │   │   └── review_finding_policy.py    ★ 純粋関数: 機械検証（diff 内か）・重複排除・上限
    │   └── ports/
    │       ├── pull_request_commenter.py   ★ PullRequestCommenter（ABC）/ PullRequestDiff
    │       └── graph_executor.py           * ExecutionResult.findings を追加
    │
    ├── application/
    │   └── usecases/
    │       ├── post_review_usecase.py      ★ PostReviewUsecase（検証 → 冪等投稿 → 報告）
    │       └── process_card_usecase.py     * finalize から PostReviewUsecase を呼ぶ
    │
    ├── infrastructure/
    │   ├── git/
    │   │   └── gh_pull_request_commenter.py ★ gh api（diff 取得・既存コメント取得・reviews 投稿）
    │   ├── workflows/
    │   │   └── langgraph_executor.py       * bind_findings で収集し ExecutionResult.findings へ
    │   └── config/
    │       └── settings.py                 * ReviewConfig（max_findings / severities_to_post）
    │
    └── interface/
        └── di_modules.py                   * PullRequestCommenter → GhPullRequestCommenter
```

### 主要コンポーネント

#### SDK: `ReviewFinding` と `ctx.report_finding()`

`Proposal` / `propose` と同じ流儀。素の dataclass、ContextVar で収集、束縛外では警告して捨てる。

```python
@dataclass(frozen=True)
class ReviewFinding:
    path: str                     # リポジトリルートからの相対パス
    line: int                     # 変更後ファイルの行番号（side=RIGHT）
    body: str                     # 指摘内容と根拠
    severity: Literal["blocker", "major", "minor"] = "minor"
    start_line: int | None = None # 範囲指摘の開始行
    aspect: str = ""              # どの観点（perspective）からの指摘か（"design" / "code" / "test" など）
    pr: int | None = None         # 対象 PR（ワークフローが複数 PR を扱うとき）

FINDING_PROMPT = """\
所見は次の形式で、1 件につき 1 ブロックで報告してください。ファイルと行は
必ず今回の diff に含まれる変更後の行を指してください。変更していない行への
指摘は投稿されません。

```vuoi-finding
{"path": "src/foo.py", "line": 42, "severity": "blocker|major|minor", "body": "..."}
```
"""
```

`ctx.report_finding(...)` と、runner 出力から `vuoi-finding` ブロックを抜き出す `ctx.report_findings_from_output(text, *, aspect, pr) -> int` を提供する。抽出の壊れ方（JSON 不正・`path` 欠落・`line` が整数でない）は `propose_from_output` と同じく警告して読み飛ばす。

#### ドメイン: `ReviewFinding`（Pydantic）と冪等キー

SDK の dataclass を `LangGraphExecutor` が写し取る。冪等キーは `sha1(f"{pr}:{path}:{line}:{normalize(body)[:80]}")[:12]`。同じ行への同趣旨の指摘は同一キーになる（本文の細かな言い換えは弾けない。未決事項参照）。

#### ドメイン: `review_finding_policy.py`（純粋関数）

```python
def select_findings(
    findings: Sequence[ReviewFinding],
    diff: PullRequestDiff,          # path → 変更後の行番号集合
    existing_keys: AbstractSet[str],
    config: ReviewConfig,
) -> tuple[list[ReviewFinding], list[Rejected]]:
```

破棄の規則（仕様 gate-review「所見の機械検証」に対応）:

- `path` が diff に含まれない → `not_in_diff`
- `line`（および `start_line`）が変更後の行集合に含まれない → `line_not_changed`
- `key` が `existing_keys` にある → `duplicate`（再実行時の二重投稿防止）
- `severity` が `config.severities_to_post` に無い → `below_threshold`（既定は 3 段階すべて投稿）
- 件数が `config.max_findings`（既定 30）を超えた分 → `over_limit`。重大度の高い順・ファイルパス順・行番号順に安定ソートしてから切る

#### ポート: `PullRequestCommenter`

```python
class PullRequestDiff(BaseModel):
    head_sha: str
    changed_lines: dict[str, frozenset[int]]   # path → 変更後の行番号

class PullRequestCommenter(ABC):
    @abstractmethod
    def fetch_diff(self, pr: int) -> PullRequestDiff: ...
    @abstractmethod
    def existing_keys(self, pr: int) -> frozenset[str]: ...
    @abstractmethod
    def post_review(self, pr: int, *, head_sha: str, summary: str, findings: Sequence[ReviewFinding]) -> str:
        """1 回のレビュー（event=COMMENT）として投稿し、レビュー URL を返す。"""
```

#### インフラ: `GhPullRequestCommenter`

すべて `gh` 経由（`GhPullRequestPublisher` と同じ）。

- `fetch_diff`: `gh pr diff <n>` を unified diff として解析し、hunk ヘッダ `@@ -a,b +c,d @@` から変更後の行番号集合を得る。`head_sha` は `gh pr view <n> --json headRefOid`。
- `existing_keys`: `gh api repos/{owner}/{repo}/pulls/<n>/comments --paginate` の各 `body` から `<!-- vuoi:finding:<key> -->` マーカーを正規表現で拾う。
- `post_review`: `gh api repos/{owner}/{repo}/pulls/<n>/reviews --input -` に次の JSON を渡す。

```json
{
  "commit_id": "<head_sha>",
  "event": "COMMENT",
  "body": "<summary>\n<!-- vuoi:review:<pr>:<head_sha> -->",
  "comments": [
    {"path": "src/foo.py", "line": 42, "side": "RIGHT", "start_line": 40, "start_side": "RIGHT",
     "body": "**[major] design**: ...\n<!-- vuoi:finding:<key> -->"}
  ]
}
```

1 リクエストで全所見を投稿する（所見ごとの単発 POST は N 回の外部作用になり、途中クラッシュ時の整合が取りにくい）。API が 422 を返したら（検証をすり抜けた行など）レスポンスの `errors` を `ReviewReport.skipped` に載せ、`CommentPostError` として上位に返す。例外は投げない。

#### アプリケーション: `PostReviewUsecase`

```
ExecutionResult.findings を pr ごとに group
  → commenter.fetch_diff(pr) / existing_keys(pr)          （読み取り）
  → select_findings(...)                                   （純粋）
  → card.add_comment(intent: "PR #n に k 件投稿します")    （INV-3: intent を先に記録）
  → commenter.post_review(...)                             （外部作用）
  → ReviewReport に posted / skipped を積む
```

`ProcessCardUsecase.finalize` は `IssueProposalsUsecase` と同じ位置で呼び、`blocked` でも所見があれば投稿する。失敗は親カードの終端処理を妨げない。`ReviewReport.to_comment()` をカードコメントに追記する。

#### ワークフロー: 粒度別レビューノード

第 1 段階の `pr_review` を次の形に改める。`/code-review --comment` は使わず、各ノードが所見を申告し投稿はホストに任せる。

```
START → parse_prs → checkout → fan-out ┬→ review_design ┐
                                       ├→ review_code   ├→ join → advance → … → summarize → END
                                       └→ review_test   ┘
```

3 ノードは LangGraph の並列分岐で走らせる（ContextVar は分岐ごとにコピーされるので収集先は共有される。`propose` で確認済みの前提）。各ノードは `session_id=None` で fresh context、入力は PR メタ情報・diff・観点リストのみ（INV-4）。

観点は `workflow.toml` に持つ。

```toml
[settings.perspectives]
design = [
  "変更が PR 本文で述べた目的に対して過不足ないか",
  "レイヤー境界（依存方向）を破っていないか",
  "既存の抽象（ポート・ユースケース）で表せるものを新しく作っていないか",
  "公開 API・設定・ファイル形式の互換性を壊していないか",
]
code = [
  "エラー処理と境界条件（None・空・上限）",
  "冪等性・再実行時の安全性",
  "同名・類似の既存実装との重複",
  "秘密情報・危険なシェル呼び出し",
]
test = [
  "変更した振る舞いに対応するテストがあるか",
  "テストが実装の詳細ではなく契約を検証しているか",
  "失敗系（例外・不正入力）のテストがあるか",
]
```

ノードの実装は 1 つの関数を観点名でパラメタ化する。

```python
def make_review_node(aspect: str):
    def node(state: State) -> dict:
        n = state["current_pr"]
        prompt = REVIEW_PROMPT.format(
            aspect=aspect,
            perspectives="\n".join(f"- {p}" for p in perspectives[aspect]),
            title=state["pr_meta"][n]["title"], body=state["pr_meta"][n]["body"],
            finding_prompt=FINDING_PROMPT,
        )
        r = ctx.runner.run(prompt, cwd=ctx.workdir, session_id=None,
                           allowed_tools=("Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(gh pr diff:*)"))
        if r.ok:
            ctx.report_findings_from_output(r.output, aspect=aspect, pr=n)
        return {"reviews": {**state.get("reviews", {}), (n, aspect): r.output if r.ok else f"（失敗: {r.output[:300]}）"}}
    return node
```

`allowed_tools` を読み取り系に絞るのは、レビューノードが対象 PR のコードを書き換えたり `gh` で勝手に投稿したりしないことを**ツール権限で**保証するため（プロンプト遵守に頼らない）。

`code` 観点のノードは、自前プロンプトの代わりに `/code-review <n> <level>`（`--comment` なし）を呼んで出力を `FINDING_PROMPT` 形式に整形させる 2 段構えにしてもよい。第 1 段階の結果で `/code-review` の所見の質が十分なら、`code` ノードはそのまま組み込みスキルに委ねる。

### 設定

```python
class ReviewConfig(BaseModel):
    max_findings: int = 30
    severities_to_post: tuple[str, ...] = ("blocker", "major", "minor")

class AppConfig(BaseModel):
    ...
    review: ReviewConfig = ReviewConfig()
```

### DI

```python
binder.bind(PullRequestCommenter, to=GhPullRequestCommenter, scope=singleton)
```

## 依存関係

- 依存の方向は既存どおり Interface → Infrastructure → Application → Domain のみ。
- `vuoi_sdk` を import してよいのはインフラ層と interface 層のみ。ドメインの `ReviewFinding` は SDK の `ReviewFinding` を知らない（`TaskProposal` / `Proposal` と同じ関係）。
- 新規外部ライブラリは追加しない。GitHub 通信は `gh`（subprocess）のみ。unified diff の解析は標準ライブラリで書く（hunk ヘッダの正規表現だけで足りる）。
- 検算: `grep -rn 'vuoi_sdk\|langgraph\|subprocess' src/chevuoi/{domain,application}` が空であること。

## 実装手順（dev ワークフローにそのまま渡せる粒度）

各ステップが 1 PR。1 は単独で完結し、2 → 3 → 4 → 5 の順に依存する。

1. **第 1 段階ワークフロー** — `~/.config/vuoi/workflows/pr_review/`（`workflow.toml` / `workflow.py`）と `_shared/pr_numbers.py`（`merge` から抽出部を移す）。ワークフローはこのリポジトリ外だが、`docs/spec/workflow-engine.md` の利用例に `gh pr checkout --detach` + `/code-review --comment` の組み合わせを追記する。動作確認: 実 PR 1 件に対して `vuoi workflow run pr_review "#<n>"` を実行し、インラインコメントが付くこと・2 回目に何が起きるかを記録する。
2. **SDK と収集経路** — `vuoi_sdk` に `ReviewFinding` / `bind_findings` / `report_finding` / `report_findings_from_output` / `FINDING_PROMPT`。ドメインに `ReviewFinding`（Pydantic、`key`）と `ExecutionResult.findings`。`LangGraphExecutor` で収集。`vuoi workflow run` が所見を表示する。テスト: 束縛外での捨て動作、並列ノードでの分離、抽出の 3 ケース（正常・壊れた JSON・`line` 非整数）。
3. **ポートと gh 実装** — `PullRequestCommenter` / `PullRequestDiff` / `CommentPostError`、`GhPullRequestCommenter`（diff 解析・マーカー抽出・reviews 投稿）、DI。テスト: unified diff のフィクスチャから `changed_lines` が正しく出ること（追加行・削除のみの hunk・複数ファイル・リネーム）、既存コメントからのキー抽出、`gh` をフェイクした投稿 JSON のスナップショット。
4. **歯止めとパイプライン接続** — `review_finding_policy.select_findings`、`ReviewConfig`、`ReviewReport`、`PostReviewUsecase`、`ProcessCardUsecase.finalize` への接続。テスト: 破棄規則 5 種、上限の安定ソート、`FakePullRequestCommenter` で「2 回実行しても 2 回目は投稿されない」、`CommentPostError` が終端処理を妨げないこと。
5. **粒度別ワークフローと文書** — `pr_review` を fan-out 構成に改め `[settings.perspectives]` を導入。`docs/index.md` の toctree に本設計を登録し、`docs/spec/gate-review.md` に「所見の機械検証はホストの `PostReviewUsecase` が担う」旨と `docs/spec/workflow-engine.md` §4 に `report_finding` の契約を追記。

## 検討した代替案と却下理由

| 案 | 内容 | 却下理由 |
|---|---|---|
| 第 1 段階のまま運用する | `/code-review --comment` に投稿まで任せ続ける | 投稿の成否・冪等性・機械検証がホストから見えず、INV-3（外部作用の記録）を満たせない。診断の質は借りつつ、投稿は引き取る |
| `gh pr review --comment -b` で本文コメントのみ | 行コメントを諦め、要約 1 件だけ投稿する | レビューの価値の大半は行に紐づく指摘にある。行コメントは `gh api` で問題なく投稿できる |
| 所見ごとに `POST /pulls/{n}/comments` | 単発の行コメント API を N 回呼ぶ | 外部作用が N 回になり、途中クラッシュで半端な状態が残る。reviews API なら 1 回で原子的に投稿できる |
| state キー方式で所見を集める | `BaseState` に reducer 付き `findings` を足す | `propose` と同じ理由（ノードごとに戻り値へ積む手間・ヘルパーの奥から申告できない）。収集は ContextVar に統一する |
| 1 セッションで観点を順に切り替える | design → code → test を同じ `session_id` で続ける | 前の観点の結論に引きずられ、fresh context の意味がなくなる（INV-4） |
| 観点をプロンプト文字列にハードコード | `workflow.py` 内に埋める | プロジェクトごとの観点の足し引きにコード変更が要る。`[settings]` に置けば `workflow_defaults` でホスト既定と上書きマージできる |
| 行番号の検証を LLM に任せる | 「diff 内の行だけ指摘して」と指示する | 仕様 gate-review が機械検証を求めており、数少ない「LLM 出力を検算できる箇所」。プロンプトに頼らない |

## 未決事項・リスク

- **`/code-review --comment` の実際の失敗挙動。** diff 外の行を指したときの扱いと、再実行時の重複の有無は第 1 段階で実測する。この結果次第で、第 2 段階の `code` ノードを組み込みスキル + 整形にするか自前プロンプトにするかを決める。
- **冪等キーの粒度。** `path:line:body 先頭 80 文字` では、本文が言い換えられただけの同趣旨の指摘を弾けない。同一 `path:line` への既存コメントがあれば重大度にかかわらず投稿しない、という粗い規則にするかは運用で判断する（初期は粗い規則を採り、`ReviewConfig.dedupe_by = "line" | "key"` で切り替えられるようにする）。
- **LEFT 側（削除行）への指摘。** `ReviewFinding` は `side=RIGHT` 固定。削除された行に対する指摘（「この削除でこの呼び出しが壊れる」）は変更後の周辺行に付けるようプロンプトで誘導する。必要になれば `side` を追加する。
- **PR の head が動いたとき。** `fetch_diff` から `post_review` の間に push があると `commit_id` が古くなり、コメントは outdated 表示になる（エラーにはならない）。許容する。
- **観点ノードの並列実行と `Runner` の同時実行数。** 3 ノードを並列に走らせると `claude -p` が 3 本同時に動く。`RunUsecase` の `ThreadPoolExecutor` との掛け算でホストの同時実行数が読めなくなる。初期は `[settings] parallel = false` で直列を既定にし、`Runner` 側にセマフォを置くかは別途決める。
- **レビュー対象 PR がこのリポジトリ以外の場合。** `gh api repos/{owner}/{repo}` の自動展開は worktree のリモートに依存する。カードのプロジェクトタグから解決した `Project.path` の worktree で実行すれば一致するが、PR URL が別リポジトリを指していた場合は `parse_prs` で弾く（URL の owner/repo とプロジェクトのリモートを照合する）。
- **`aspect` の表示。** 行コメント本文の先頭に `[major] design` のように載せるが、GitHub 上でラベル化はできない。集計は `existing_keys` と同様にマーカー `<!-- vuoi:finding:<key>:<aspect> -->` から拾えるようにしておく。
- **`docs/index.md` の toctree** には本設計を登録済み（本タスクは設計書の作成のみ）。
