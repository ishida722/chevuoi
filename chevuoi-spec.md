# Che vuoi? 仕様書

タスク管理ツールのチケットを起点に、`claude -p` を実行ノードとするパイプラインを回し、
成果物（PR / 調査メモ / ADR）を生成してチケットを更新する常駐バッチ。

対象読者: 本リポジトリを実装する開発者および Claude Code。

**命名:** 正式名称は **Che vuoi?**。パッケージ／リポジトリ名は `chevuoi`、CLI コマンドは `vuoi`。
上位の定期実行層（複数ループを束ねる側）は別ソフトの `borro` であり、本仕様の対象外。
`borro` が `vuoi run` を周期的に起動する関係にある。

---

## 0. 不変条件（Invariants）

**この5つは設計の芯であり、実装上の都合で破ってはならない。**
迷ったらここに戻ること。レビュー時もここを最初に検算する。

### INV-1: 遷移判断に LLM を使わない

「次にどのノードへ行くか」「合格か不合格か」は、必ず Python 側のコードが決める。
LLM は所見・成果物・分類候補を**出力する**だけで、フロー制御には一切関与しない。

```python
# NG
next_node = ask_llm("状況はこれ。次に何をすべき？", state)
if llm_result["verdict"] == "pass": ...

# OK
next_node = route.steps[state.cursor + 1]
if subprocess.run(project.gate_cmd, shell=True).returncode == 0: ...
```

例外は入口の triage 1回のみ（§6）。それも「候補を1つ返す」だけで、
経路の中身・ゲート・上限はすべて静的な定義から引く。

### INV-2: state はシリアライズ可能

`RunState` は pydantic モデルであり、いつでも JSON へ往復できる。
API クライアント・ファイルハンドル・subprocess を保持しない。
毎遷移で永続化し、プロセスが落ちても `drive(load(run_id))` で再開できる。

### INV-3: 外部作用は intent → 実行 → 記録 の順

```
state に intent を書いて永続化 → 外部 API を叩く → state に結果を書いて永続化
```

逆順にすると、間でクラッシュしたときに「カードは動いたが state は古い」不整合が残る。
すべての外部作用は冪等に実装し、再開時はリプレイで正しくなるようにする。

### INV-4: 各ノードの入力は宣言されたものだけ

ノードに「これまでの全部」を渡さない。`NODE_INPUTS`（§7.2）で宣言した鍵だけを渡す。
特に review ノードには plan と impl の会話を渡さない。
プロセスを分けている唯一の理由が fresh context の確保なので、ここを緩めると設計全体が無意味になる。

### INV-5: ポートに実装の語彙を漏らさない

`"In Review"` `"Inbox"` といった Trello 固有の文字列が `adapters/trello.py` の外に出てはならない。
コアが知ってよいのは `Outcome.terminal`（§4.3）という抽象だけ。

**検算:** `grep -rn '"In Review"\|"Inbox"\|"Ready"' src/chevuoi --include='*.py' | grep -v adapters/trello.py` が空であること。

---

## 1. スコープ

### やること

- 複数のタスクソース（初期は Trello のみ）からチケットを取得
- チケット種別に応じた経路（implement / investigate / design / trivial）の選択
- git worktree による作業隔離
- `claude -p` をノードとするパイプライン実行
- 決定的ゲート（テスト・lint・型チェック）による合否判定
- レビューループ（収束保証つき）
- 終端状態に応じたチケット更新
- 作業中に発見した副次タスクの起票

### やらないこと（Non-goals）

- ノードの並列実行（1ラン内は逐次。ラン間の並列は将来検討）
- PR の自動マージ（PR 作成で必ず人間ゲート）
- 自動起票カードの自動実行（Inbox 止まり。§10）
- ワークフローフレームワーク（LangGraph / Temporal 等）の導入
- Web UI

---

## 2. 全体フロー

```
poll (cron/systemd timer)
  └─ for each source:
       fetch_ready() ──→ [Task]
         └─ claim()                       … 冪等。失敗したら次のタスクへ
              └─ identify_project()       … タグ→プロジェクト。決定的
                   └─ triage()            … 経路を1つ選ぶ。LLM可（棄権あり）
                        └─ drive()        … 経路を歩く。LLM不使用
                             │
                             ├─ setup_worktree
                             ├─ plan      (claude -p)
                             ├─ impl      (claude -p)   ←──┐
                             ├─ gate      (subprocess)  ───┤ 決定的
                             └─ review    (claude -p)   ───┘ 所見が残る限り戻る
                             │
                        commit_proposals()               … 起票（Inbox へ）
                        report(outcome)                  … カード更新
                        cleanup_worktree()
```

後ろ向きの辺（gate/review → impl）が存在するため一直線ではない。
パイプではなく state machine として実装する。

---

## 3. リポジトリ構成

```
src/chevuoi/
  __init__.py
  cli.py                  # vuoi run / vuoi resume / vuoi gc / vuoi status
  config.py               # PROJECTS, TAG_TO_PROJECT, 各種上限
  models.py               # Task, TaskRef, RunState, Outcome, Finding, Proposal
  routes.py               # ROUTES, NODE_INPUTS, ON_FAIL
  driver.py               # drive(), 遷移ロジック
  nodes/
    base.py               # run_claude_node(): subprocess ラッパ
    plan.py
    impl.py
    gate.py               # LLM 不使用
    review.py             # 所見の凍結・検証・発振検知
    research.py
    writeup.py
    draft_adr.py
  ports/
    task_source.py        # Protocol 定義
  adapters/
    trello.py             # REST API。MCP は使わない
  worktree.py
  proposals.py
  store.py                # state の永続化
  telemetry.py            # OTel

runs/<run_id>/
  state.json              # コントロールプレーン。数KB以内
  plan.md                 # データプレーン
  gate/attempt-N.log
  review/round-N.json
  proposals/*.json
  worktree/               # git worktree

tests/
prompts/
  plan.md  impl.md  review.md  triage.md  research.md  writeup.md  draft_adr.md
```

**言語・依存:** Python 3.12+ / pydantic v2 / httpx / 標準ライブラリの `subprocess`, `sqlite3`。
ワークフローフレームワークは入れない。

---

## 4. データモデル

### 4.1 Task

永続化されるのは同一性の情報のみ。振る舞いは持たない（INV-2）。

```python
class TaskRef(BaseModel):
    source: str            # "trello" | "jira" | "github"
    external_id: str
    url: str

class Task(BaseModel):
    ref: TaskRef
    title: str
    body: str
    tags: list[str]
    depth: int = 0                    # 人間起票 = 0。自動起票で +1
    origin: TaskRef | None = None     # 親タスク
    raw: dict = {}                    # 元データ。デバッグ用。プロンプトには載せない
```

振る舞いが必要な箇所では、レジストリ経由でアダプタを束ね直す。

```python
SOURCES: dict[str, TaskSource] = {"trello": TrelloSource(...)}

def rehydrate(state: RunState) -> tuple[Task, TaskSource]:
    return state.task, SOURCES[state.task.ref.source]
```

### 4.2 RunState

```python
class RunState(BaseModel):
    schema_version: int = 1           # 必須。in-flight なランがある状態でのスキーマ変更に備える
    run_id: str
    task: Task
    project: str                      # PROJECTS のキー
    route: str                        # ROUTES のキー
    cursor: int = 0
    attempt: dict[str, int] = {}      # ノード名 → 試行回数
    review_round: int = 0
    frozen_findings: list[Finding] = []
    diff_history: list[str] = []      # 発振検知用の diff ハッシュ
    sessions: dict[str, str] = {}     # ノード名 → claude session_id
    artifacts: dict[str, str] = {}    # 論理名 → **パス**（中身ではない）
    pending_intent: dict | None = None  # INV-3 用
    outcome: Outcome | None = None
    started_at: datetime
    trace_id: str
```

**diff やテストログの本文を state に入れてはならない。**
state はプロンプトに載る可能性があるため、無制限に育てるとプロセス分離で避けたコンテキスト汚染が
state 経由で戻ってくる。本文は `runs/<id>/` 配下のファイルに置き、`artifacts` にはパスだけを持つ。

### 4.3 Outcome（終端状態）

**すべての実行はこのいずれかで終わる。「なんとなく止まった」を作らない。**

| terminal | 意味 | worktree |
|---|---|---|
| `done` | 経路を歩き切った。PR 作成済み | 削除 |
| `needs_human` | 仕様が曖昧 / レビュー未収束 / 承認待ち | **残す** |
| `failed_gate` | 決定的ゲートが上限回数落ちた | **残す** |
| `oscillation` | 同一 diff に戻った（収束しない） | **残す** |
| `failed_budget` | 壁時計 / 総試行数の予算超過 | **残す** |
| `abandoned` | チケットが消えた / 外部で変更された | 削除 |

```python
class Outcome(BaseModel):
    terminal: Literal["done", "needs_human", "failed_gate",
                      "oscillation", "failed_budget", "abandoned"]
    reason: str | None = None
    pr_url: str | None = None
    unresolved_findings: list[Finding] = []
    worktree_path: str | None = None   # 残した場合に人間へ伝える
```

判定の優先順位: **決定的ゲート → 予算 → 経路の終端**。
LLM の自己申告（「完了しました」）は判定に一切使わない。

`needs_human` は `reason` でサブ分類する: `ambiguous_ticket`, `review_unconverged`,
`unmapped_tag`, `approval_required`。打ち手が異なるため区別が必要。

### 4.4 Finding / Proposal

```python
class Finding(BaseModel):
    id: str
    severity: Literal["blocker", "major", "minor"]
    file: str
    line: int
    criterion: str | None      # 受け入れ基準ID。None なら自動的に minor へ降格
    detail: str
    evidence: str              # 捏造検証の対象
    resolved: bool = False

class Proposal(BaseModel):
    proposal_id: str           # uuid。冪等性キー
    title: str
    body: str
    evidence: str              # "src/foo.py:142" 等。空 or 不在なら破棄
    suggested_tags: list[str]
    kind: Literal["bug", "chore", "spike", "debt"]
```

---

## 5. ポートとアダプタ

### 5.1 TaskSource Protocol

```python
class TaskSource(Protocol):
    name: str

    def fetch_ready(self, limit: int) -> list[Task]: ...
    def claim(self, task: Task) -> bool: ...
    def report(self, task: Task, outcome: Outcome) -> None: ...
    def comment(self, task: Task, body: str) -> None: ...
    def still_valid(self, task: Task) -> bool: ...
    def create(self, draft: Proposal, parent: TaskRef, depth: int) -> TaskRef: ...
    def find_similar(self, draft: Proposal) -> list[TaskRef]: ...
```

「どのチケットが対象か」のクエリはアダプタが持つ。
Trello なら「Ready リストの chevuoi ラベル付きカード」、Jira なら JQL。
コアは各バックエンドのクエリ言語を知らない。

### 5.2 Trello アダプタ

**REST API を直接叩く。MCP コネクタは使わない。**
MCP は Claude が使うためのものであり、パイプラインのアダプタが使うと
カード操作が非決定的になりリトライの冪等性が壊れる。

終端状態 → 操作の写像はアダプタ内に閉じる:

```python
_EFFECTS = {
    "done":          [_Move("In Review"),  _CommentPR],
    "needs_human":   [_Move("Blocked"),    _CommentFindings],
    "failed_gate":   [_Move("Blocked"),    _CommentLog],
    "oscillation":   [_Move("Blocked"),    _CommentDiffHistory],
    "failed_budget": [_Move("Blocked"),    _CommentReason],
    "abandoned":     [_NoOp],
}
```

写像できない終端状態がある場合は `comment` へフォールバックする。

#### claim() の冪等性

Trello に compare-and-swap は無い。cron の二重起動やクラッシュ残留に対して
**リスト移動そのものをクレームとして扱う**。

```python
def claim(self, task: Task) -> bool:
    card = self.api.get_card(task.ref.external_id)
    if card.list_id == self._in_progress_id:
        return self._lease_is_mine(task)   # 自分の中断ランの再開なら True
    if card.list_id != self._ready_id:
        return False                        # 既に誰かが取った / 動かされた
    self.api.move(card.id, self._in_progress_id)
    return True
```

`_lease_is_mine` は `runs/` 配下に該当 `external_id` の未終端 state があるかで判定する。

#### still_valid()

人間が実行中にカードをアーカイブ・移動する場合がある。
高コストなノードの前と `report` の直前に確認し、外れていたら `abandoned` で終了する
（カードには触らず worktree のみ掃除）。

---

## 6. プロジェクト割り当てと triage

### 6.1 プロジェクト割り当て（決定的）

```python
TAG_TO_PROJECT = {"MIRAI": "mirai", "SSC101": "ssc101"}

class Project(BaseModel):
    repo: str
    base_branch: str
    gate_cmd: str                  # ゲートの中身はプロジェクトが持つ
    worktree_root: Path
    pr_template: str | None = None
```

- タグが引けない → `needs_human(unmapped_tag)` で即返す
- 複数ヒット → 同様に `needs_human`

**LLM に推測させない。** 誤ったリポジトリで実装ランを1本溶かすコストのほうが、
人間がタグを1つ付けるコストより遥かに高い。

### 6.2 triage（入口で1回のみ）

3層で判断し、上位層で決まれば下位層は実行しない。

**第1層 — 決定的な事前チェック（LLM 不使用）**

```python
def pretriage(task: Task) -> str | None:
    if "spike" in task.tags:  return "investigate"
    if "adr" in task.tags:    return "design"
    if re.match(r"^(typo|bump|deps)", task.title, re.I): return "trivial"
    if not task.body.strip(): return None          # → needs_human へ
    return None                                     # 判断つかず → 第2層
```

人間がラベルを1つ付ける行為自体がルーティング決定である。最も安く最も正確。

**第2層 — LLM（残ったものだけ）**

```bash
claude -p "$(cat ticket.json)" --bare \
  --allowedTools "Read,Grep" --max-turns 5 --output-format json
```

返させるのは次のみ:

```json
{
  "route": "implement",
  "confidence": "high",
  "reason": "対象ファイルが特定でき、受け入れ基準が3件抽出できた",
  "target_files": ["src/mirai/cartridge/base.py"],
  "acceptance_criteria": [{"id": "AC-1", "text": "..."}]
}
```

判断の実質は「**受け入れ基準を書き下せたか**」「**対象ファイルを特定できたか**」の2点。
抽象的な種別分類より安定する。

**第3層 — 棄権パス（必須）**

```python
if result.confidence != "high" or not result.acceptance_criteria:
    return finish("needs_human", reason="ambiguous_ticket")
```

必ずどれかを選ばされるルーターは必ず間違える。誤ルーティングのコストは実行1本ぶん。

### 6.3 経路の途中での reroute

plan ノードが「これは実装ではなく調査だ」と気づく場合がある。
routine dispatch ではなく **escalation として、1ランにつき1回まで**許可する。

```python
if result.propose_reroute and not state.rerouted:
    state.route = result.propose_reroute
    state.cursor = 0
    state.rerouted = True
    persist(state)
else:
    return finish("needs_human", reason="reroute_exhausted")
```

フラグで縛ることで plan↔impl のピンポンが構造的に不可能になる。

---

## 7. 経路とノード

### 7.1 ROUTES（分岐はコードではなくデータで持つ）

タスク種別が増えてもドライバは1行も変わらないこと。増えるのは辞書のエントリとノード1本のみ。

```python
class Route(BaseModel):
    steps: list[str]
    has_gate: bool                     # ゲートが「存在するか」。中身は Project が持つ
    on_fail: dict[str, str]            # ノード → 戻り先
    max_attempt: dict[str, int]
    max_review_rounds: int = 3
    budget_minutes: int = 45

ROUTES = {
    "implement": Route(
        steps=["setup_worktree", "plan", "impl", "gate", "review", "open_pr"],
        has_gate=True,
        on_fail={"gate": "impl", "review": "impl"},
        max_attempt={"impl": 3, "gate": 3},
    ),
    "trivial": Route(
        steps=["setup_worktree", "impl", "gate", "open_pr"],
        has_gate=True,
        on_fail={"gate": "impl"},
        max_attempt={"impl": 2, "gate": 2},
        max_review_rounds=1,
    ),
    "investigate": Route(
        steps=["research", "writeup", "review"],
        has_gate=False,
        on_fail={"review": "research"},
        max_attempt={"research": 2},
    ),
    "design": Route(
        steps=["draft_adr", "review", "human_approve"],
        has_gate=False,
        on_fail={"review": "draft_adr"},
        max_attempt={"draft_adr": 2},
    ),
}
```

`setup_worktree` を、必要とする経路の steps 先頭に置く。
`investigate` / `design` は clone を必要としないため、無駄な clone と後始末が発生しない。
経路の定義がそのまま「何が必要か」の宣言になる。

### 7.2 NODE_INPUTS（INV-4 の実体）

```python
NODE_INPUTS = {
    "plan":       ["ticket", "target_files"],
    "impl":       ["plan", "unresolved_findings", "gate_latest"],
    "gate":       [],                                    # LLM 不使用
    "review":     ["diff", "acceptance_criteria", "frozen_findings"],
    "research":   ["ticket"],
    "writeup":    ["research_notes", "ticket"],
    "draft_adr":  ["ticket", "unresolved_findings"],
}
```

review に `plan` を渡さないこと。渡すと「計画通りだから OK」という採点が始まる。

### 7.3 ノード実行

```python
def run_claude_node(node: str, state: RunState, project: Project) -> NodeResult:
    ...
```

共通の呼び出し規約:

- `--bare` を必ず付ける（スクリプト/SDK 呼び出しの推奨モード。将来 `-p` の既定になる）
- `--output-format json` で構造化出力を読む
- `--max-turns` をノードごとに設定（ノード内の停止条件）
- `--allowedTools` を最小権限で明示

| ノード | allowedTools | max-turns |
|---|---|---|
| triage | `Read,Grep` | 5 |
| plan | `Read,Grep,Bash(git log:*)` | 10 |
| impl | `Read,Edit,Write,Bash` | 40 |
| review | `Read` | 10 |
| research | `Read,Grep,WebSearch` | 20 |

**セッション継続は既定 fresh、`--resume` は opt-in。**
gate 失敗からのリトライで impl セッションを再開すると失敗した推論ごと復活する。
同一ファイルの単純な修正のみ resume、方針から誤っていた場合は fresh。
`state.sessions` に session_id を持たせて選択可能にする。

**exit code の扱い:** ゼロ / 非ゼロで分岐する。
`-p` の全パターンを網羅した exit code 表は公開されていないため、特定の非ゼロ値を決め打ちしない。
正確な理由は `--output-format json` の構造化出力から読む。

**バックグラウンドプロセスの注意:** `claude -p` 実行中に起動されたバックグラウンド Bash タスク
（dev サーバや watch ビルド）は、最終結果を返して stdin が閉じた約5秒後に終了する。
バックグラウンドのサブエージェント/ワークフローはこの猶予の対象外だが、既定10分でキャップされる。
watch ビルドを使うプロジェクトではノードのタイムアウト設計時に考慮すること。

### 7.4 ストップ条件は2層

- **ノード内**: `--max-turns`
- **ラン全体**: `Route.max_attempt` と `Route.budget_minutes`（壁時計）

片方だけだと、5ターンで終わるノードが30回リトライされて一晩溶ける。

---

## 8. ドライバ

```python
def drive(state: RunState) -> RunState:
    project = PROJECTS[state.project]
    route = ROUTES[state.route]

    while state.outcome is None:
        node = route.steps[state.cursor]

        if exceeded_budget(state, route):
            return finish(state, "failed_budget")

        result = run_node(node, state, project)      # LLM を呼ぶのはこの中だけ

        if result.ok:
            state.cursor += 1
            if state.cursor >= len(route.steps):
                finish(state, "done")
        else:
            back_to = route.on_fail.get(node)
            if back_to is None:
                finish(state, "failed_hard")
            else:
                state.attempt[node] = state.attempt.get(node, 0) + 1
                if state.attempt[node] > route.max_attempt.get(node, 3):
                    finish(state, terminal_for(node))   # gate → failed_gate 等
                else:
                    state.cursor = route.steps.index(back_to)

        persist(state)          # 毎遷移で必ず書く（INV-2, INV-3）

    return state
```

`drive` は50〜80行に収まること。ここに `if route == "..."` が生えたら設計が漏れている
（経路固有の分岐は `Route` / `Project` のデータへ移す）。

---

## 9. gate と review

### 9.1 gate（決定的。LLM 不使用）

```python
def run_gate(state, project) -> NodeResult:
    log = Path(f"runs/{state.run_id}/gate/attempt-{state.attempt.get('gate', 0)}.log")
    proc = subprocess.run(project.gate_cmd, shell=True, cwd=worktree_of(state),
                          capture_output=True, text=True, timeout=1800)
    log.write_text(proc.stdout + proc.stderr)
    state.artifacts["gate_latest"] = str(log)
    return NodeResult(ok=(proc.returncode == 0))
```

**このノードに `claude -p` を入れてはならない。**
「テストが通ったか確認して」を LLM に聞いた時点で INV-1 違反。

**gate が緑でなければ review を実行しない。** 赤い diff のレビューはトークンの無駄。

### 9.2 review（合否はドライバが決める）

レビュアーは所見を返すだけ。`verdict` を返させない。

```python
def decide(findings: list[Finding]) -> Literal["pass", "revise"]:
    if any(f.severity == "blocker" and not f.resolved for f in findings): return "revise"
    if sum(f.severity == "major" and not f.resolved for f in findings) >= 2: return "revise"
    return "pass"      # minor は記録して PR 本文に載せるのみ
```

閾値をコードに置くことで、チューニング可能なパラメータになる。

#### 所見の凍結（収束保証の本体）

ラウンドごとに新しい指摘が出ると、実装が直る速さと同じ速さで基準が動くため原理的に収束しない。

- **1周目**: 所見集合を確定し `state.frozen_findings` に保存
- **2周目以降**: 凍結した所見を渡し、各 finding の `resolved: bool` のみを返させる
- 2周目以降の**新規所見は `blocker` のみ受理**。それ以外は破棄してログに残す
- `criterion` が `null` の所見は自動的に `minor` へ降格（かつ起票候補へ回す。§10）

**これがないループは、他がすべて正しくても終わらない。**

#### 所見の機械検証（捏造の除去）

```python
def validate(f: Finding, diff: Diff, acs: list[str]) -> bool:
    if f.file not in diff.changed_files: return False        # 変更していない箇所への指摘
    if f.line not in diff.line_range(f.file): return False
    if f.criterion and f.criterion not in acs: return False
    return True
```

検証に落ちた所見は破棄する。LLM 出力を機械で検算できる数少ない箇所であり、コストはゼロ。

#### 発振検知

```python
h = hashlib.sha256(diff.encode()).hexdigest()[:12]
if h in state.diff_history:
    return finish(state, "oscillation")
state.diff_history.append(h)
```

試行回数上限より早く効く。3周待たずに捕まる。

#### review ループ全体

```python
while True:
    run("impl", inputs=["plan", unresolved(state.frozen_findings), "gate_latest"])

    if not run("gate"):
        if bump(state, "gate") > route.max_attempt["gate"]:
            return finish(state, "failed_gate")
        continue                                    # review まで行かせない

    if detect_oscillation(state):
        return finish(state, "oscillation")

    raw = run("review", inputs=NODE_INPUTS["review"])
    if not raw.can_judge:
        return finish(state, "needs_human", reason="reviewer_abstained")

    findings = [f for f in raw.findings if validate(f, diff, acs)]

    if state.review_round == 0:
        state.frozen_findings = findings            # 凍結
    else:
        merge_resolutions(state.frozen_findings, findings)

    if decide(state.frozen_findings) == "pass":
        return finish(state, "done")

    state.review_round += 1
    if state.review_round >= route.max_review_rounds:
        return finish(state, "needs_human", reason="review_unconverged")
```

impl に戻すのは**未解決の所見リストだけ**。レビュアーの文章全体でも前回の実装会話でもない。

---

## 10. 起票（Proposals）

### 10.1 位置づけ

独立ノードにしない。既存ノードの副産物として収集する。

| 収集元 | 内容 |
|---|---|
| plan | 実装の前提として先に必要なもの |
| impl | 作業中に踏んだ、無関係な不具合 |
| review | `criterion` が null の所見（既に取得済み） |
| gate | flaky テストの検出 |

**本来の目的はタスクを増やすことではなく、impl が範囲外のバグをその場で直すのを止めること。**
各ノードのプロンプトに次を入れる:

> 範囲外の問題を見つけたら、**直さずに** proposal に記録して作業を続けること。

diff が小さくなり、レビューが通りやすくなり、gate の失敗も減る。副次効果のほうが大きい。

### 10.2 収集と検証

ノードは `runs/<id>/proposals/*.json` へ append するだけ。
Trello への書き込みは**ドライバとアダプタが行う**（ノードに MCP でカードを作らせない）。

- `evidence` が空、または指すファイルが実在しないものは機械的に破棄

### 10.3 暴走の防止

エージェントが起票し、ポーラーが起票されたものを拾う構造は正のフィードバックを持つ。
3つの歯止めを必ず入れる。

1. **起票先は Inbox（Ready ではない）** — 自動生成カードは自動で拾われない。
   人間が目を通して Ready に上げたものだけが実行される。ループが構造的に切れる。
2. **世代深度** — `depth >= 2` のタスクからは起票させない。
3. **1ランあたり上限** — `MAX_PROPOSALS_PER_RUN = 3`。超過分は破棄し、
   代わりに「N件の問題を検出」という要約カード1枚を作る。
   大量起票は起票の問題ではなく実装対象の問題なので、そちらを人間が見るべき。

### 10.4 重複排除

```python
if hits := src.find_similar(draft):
    src.comment(hits[0], f"{parent.external_id} の作業中にも再発を確認: {draft.evidence}")
    continue
```

アダプタのネイティブ検索（Trello ならカード検索 API）で実装する。タイトル類似で十分。

### 10.5 冪等性

INV-3 に従い `state に proposal_id を書く → create → external_id を書く`。
再開時は `proposal_id` で検索してから作る。
そのため description のフッタに `proposal_id: <uuid>` を必ず埋め込む。
Trello に冪等性キーが無くても、このマーカーで実質的な冪等性を作れる。

### 10.6 コミットのタイミング

```python
finally:
    commit_proposals(state, src)      # 先。実行結果と独立
    src.report(task, state.outcome)
    cleanup_worktree(state)
```

**終端状態に関わらず実行する。** gate が落ちたランでも、途中で踏んだ無関係なバグは実在する
（proposal の evidence は既存コードを指すため、そのランの成否と独立）。
例外は `abandoned` で、この場合は parent リンクを外しプロジェクトのみ紐付けて作成する。

---

## 11. worktree

- ブランチ名は `chevuoi/<source>-<external_id>` として**外部 ID から決定的に導出**する
  （再計算できるため state に持つ必要がない）
- 作成は `setup_worktree` ノード（必要な経路のみ）
- 削除は終端状態に依存（§4.3 の表）
- `vuoi gc` を最初から用意する: 終端済みかつ N 日以上経過した worktree を削除
- 残した場合は `Outcome.worktree_path` に記録し、チケットのコメントにも記載する

ラン間の並列実行時、worktree は別ディレクトリなので衝突しない。
SQLite インデックス（§12）を使う場合は WAL モードにすること。

---

## 12. 永続化と観測

### 12.1 state の保存

`runs/<run_id>/state.json` を single source of truth とする。
SQLite は**後から read model として追加**する（state.json を流し込むだけ）。
最初から両方を持つと write path が二重になる。

横断クエリ（stuck しているランの一覧、gate 失敗の多いノード）が必要になった時点で追加する。

### 12.2 OpenTelemetry

`claude -p` は OTel 計装を内蔵している。次の環境変数で有効化する。

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOG_TOOL_DETAILS=1     # file_path / full_command などの拡張属性が必要な場合
```

span 構造は `claude_code.interaction` をルートに、`claude_code.llm_request`
（model, duration_ms, ttft_ms, input/output tokens, cache_read_tokens, stop_reason）と
`claude_code.tool`（tool_name, duration_ms, result_tokens）がぶら下がる。
hook span は対話 CLI では組織の allowlist が必要だが、**非対話の `-p` セッションは対象外**。

`state.trace_id` を各ノードに環境変数で渡し、**ラン全体を1つの trace に束ねる**こと。
これによりグラフ実行がそのまま可視化される。

コレクタを立てない場合は PostToolUse フックで構造化イベントを自前パイプラインに追記する。

### 12.3 記録すべき指標

| 指標 | 用途 |
|---|---|
| route × terminal の混同行列 | triage 精度。誤ルーティング1件のコストは実行1本ぶんなので改善 ROI が最も高い |
| review ラウンド分布 | 1周通過率。9割なら閾値が緩い、3割なら厳しい |
| 所見の生存率 | `validate` で破棄された割合。高ければ review プロンプトが悪い |
| proposal promotion rate | 自動起票のうち人間が Ready に上げた割合。**3割未満なら基準が緩すぎる** |
| impl の diff 行数 | 起票機能がスコープ規律として効いているかの副次指標 |

---

## 13. 実行

```bash
vuoi run                      # 全ソースをポーリングして1巡
vuoi run --source trello --limit 1
vuoi resume <run_id>          # 中断ランの再開
vuoi status                   # 未終端ランの一覧
vuoi gc --older-than 7d       # 終端済み worktree の掃除
```

常駐は systemd timer + oneshot service。
`claude -p` はデーモンではなく、1回の呼び出しで1ターン走って終了するため、
ターン・クラッシュ・再起動をまたぐにはスーパーバイザが要る。
`RuntimeMaxSec` でハードストップをかけ、`Restart=no`、ログは journald に出す。

---

## 14. 実装順序

**先に全部を作らない。** 各段階を実運用で回し、失敗が観測されてから次へ進む。

| # | 内容 | 完了条件 |
|---|---|---|
| 1 | `models.py`, `routes.py`, `driver.py`, `store.py` | ダミーノードで `implement` 経路が最後まで歩き、途中で kill して `resume` できる |
| 2 | `ports/task_source.py` + `adapters/trello.py` | fetch → claim → report が冪等に動く。`grep` 検算（INV-5）が空 |
| 3 | `nodes/base.py` + `impl` + `gate` | 1チケットから PR が出る。**gate が赤なら PR を出さない** |
| 4 | `identify_project` + 第1層 triage のみ | ラベルベースで `implement` / `trivial` が振り分く。第2層 LLM はまだ入れない |
| 5 | 2週間運用し、`pretriage` が `None` を返すチケットを観察 | 第2層 LLM が本当に必要か、どの判断で必要かを実データで判断 |
| 6 | `nodes/review.py`（凍結・検証・発振検知を同時に入れる） | 3周以内に必ず終端へ到達する |
| 7 | proposals（まず review の `criterion=null` を流すだけ） | Inbox に溜まる。promotion rate を2週間計測 |
| 8 | `investigate` / `design` 経路 | 該当チケットが実際に来て、既存経路が噛み合わないと判明してから |
| 9 | OTel、SQLite read model | 横断で見たくなってから |

段階 1〜3 が動けば実用価値が出る。段階 4 以降は各々が独立に追加可能で、
ドライバへの変更を伴わないこと（伴うなら設計が漏れている）。

---

## 15. テスト方針

- **ドライバ**: ノードを全てモックし、遷移テーブルを網羅する。
  特に「gate 3回失敗 → `failed_gate`」「同一 diff → `oscillation`」「reroute 2回目 → `needs_human`」
- **アダプタ**: httpx のモックトランスポート。`claim` の競合ケース（既に In Progress / 別リストへ移動済み）
- **review**: 所見の凍結と `validate` は純関数として単体テスト。LLM を呼ばない
- **冪等性**: 各外部作用について「intent 書き込み後にクラッシュ → 再開」でカードが二重に動かないこと

`claude -p` を実際に呼ぶテストは CI に入れない。
ノード単体の動作確認は手動 (`vuoi run --source trello --limit 1`) で行う。
