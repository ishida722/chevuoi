# 実行ログ収集設計

カード処理のたびに `claude -p` が何を考え、どのツールをどう叩いたかを後から閲覧・分析できるようにするための設計です。`claude` が自身で書き出すセッショントランスクリプトを、カード処理の完了後にまとめて回収し、DuckDB に投入します。{doc}`workflow-engine` のレイヤー構成に載せ、既存の `Runner` / `GraphExecutor` の契約は変えません。

## 背景と方針

現状の `ClaudeCliRunner` は `claude -p --output-format json` で最終結果のみを受け取り、ログに残るのは `ok / session_id / cost_usd` の 1 行だけです。途中のツール呼び出しや思考過程は失われています。

取得方法として `--output-format stream-json --verbose` への切り替えも検討しましたが、採用しません。

- 標準出力に全イベントが流れるため、`RunResult` を組み立てるパース処理が煩雑になる。
- 実行中にリアルタイムで見たいわけではなく、目的は事後分析である。

代わりに、**`claude` が `~/.claude/projects/` 配下に書き出すトランスクリプト（JSONL）を完了後に回収する**方式を採ります。

1. **Runner は触らない。** `RunResult.session_id` はすでに返っている。これを捨てずにカード単位で集めるだけでよい。
2. **回収と投入は 1 か所にまとめる。** カード処理の終端（`ProcessCardUsecase.execute` の末尾）でだけ行う。ワークフローやノードは何も意識しない。
3. **生データを正とする。** トランスクリプトの JSON は Claude Code の内部形式でバージョンによって変わり得る。各行を丸ごと保持し、分析用の列は「抽出したもの」として扱い、形式変更時は再抽出で追従する。
4. **失敗しても本処理を妨げない。** ログ収集の失敗（ファイルが見つからない、DuckDB が開けない）は警告ログにとどめ、カードの終端処理には影響させない。

## トランスクリプトの所在

`claude` はセッションごとに次のパスへトランスクリプトを書きます。

```
~/.claude/projects/<cwd スラッグ>/<session_id>.jsonl
```

`<cwd スラッグ>` は実行時 cwd の絶対パスの `/` `.` などを `-` に置換したものです。chevuoi は worktree 上で `claude` を起動するため、スラッグは worktree のパスから作られます。またルーター（`ClaudeWorkflowRouter`）はプロジェクトの本体リポジトリを cwd にして起動します。

スラッグの生成規則は Claude Code の内部仕様なので自前では組み立てず、**`session_id` で glob 検索**します。session_id は UUID で衝突しません。

```
~/.claude/projects/**/<session_id>.jsonl
```

留意点：

- worktree を削除してもトランスクリプトは `~/.claude` 側に残る。削除順序の制約はない。
- Claude Code の設定 `cleanupPeriodDays`（既定 30 日）で古いトランスクリプトは削除される。カード完了直後に回収する。

## session_id の収集

1 枚のカード処理で `claude -p` は複数回起動します。

- ルーターによるワークフロー選択（1 回）
- ワークフロー内の各ノード（`--resume` で継続する場合も、新規セッションを起こす場合もある）

ここが現状のコードで唯一足りない部分です。`vuoi_sdk` の ContextVar 機構（`bind_workdir` / `bind_project` と同じ流儀）で「このカード処理で発生した session_id の列」を束縛し、`Runner` が結果を返すたびに追記します。

```python
# vuoi_sdk
_sessions: ContextVar[list[str] | None] = ContextVar("vuoi_sessions", default=None)

@contextmanager
def bind_session_sink() -> Iterator[list[str]]:
    """ホストが 1 回のカード処理に session_id の受け皿を束縛する。"""
    sink: list[str] = []
    token = _sessions.set(sink)
    try:
        yield sink
    finally:
        _sessions.reset(token)

def record_session(session_id: str | None) -> None:
    """Runner が RunResult を返す直前に呼ぶ。受け皿が無ければ何もしない。"""
    sink = _sessions.get()
    if sink is not None and session_id and session_id not in sink:
        sink.append(session_id)
```

`ClaudeCliRunner.run` は `_parse` の後に `record_session(result.session_id)` を 1 行呼ぶだけです。ルーターも同じ Runner を使うため、選択時のセッションも自動で入ります。`vuoi workflow run` のような単発実行では受け皿が束縛されないので何も起きません。

## ディレクトリ構造

追加・変更されるファイルのみ示します。

```
src/
├── vuoi_sdk/
│   └── __init__.py                    # bind_session_sink / record_session を追加
│
└── chevuoi/
    ├── domain/
    │   ├── entities/
    │   │   └── execution_record.py    # ExecutionRecord（run 1 件のメタ情報）
    │   └── ports/
    │       ├── transcript_locator.py  # TranscriptLocator（ABC）
    │       └── execution_log_store.py # ExecutionLogStore（ABC）
    │
    ├── application/
    │   └── usecases/
    │       ├── process_card_usecase.py     # 終端で collector を呼ぶ（変更）
    │       └── collect_execution_log_usecase.py  # 回収→投入の 1 か所
    │
    └── infrastructure/
        ├── config/
        │   └── settings.py            # ExecutionLogConfig を追加
        ├── claude/
        │   └── claude_transcript_locator.py  # ~/.claude/projects を glob
        ├── duckdb/
        │   └── duckdb_execution_log_store.py # DuckDB への投入
        └── workflows/
            └── claude_cli_runner.py   # record_session を呼ぶ（変更）
```

## ドメイン層

### `ExecutionRecord`

カード処理 1 回分のメタ情報。chevuoi 側で決まる値だけを持ち、トランスクリプトの中身には立ち入りません。

```python
class ExecutionRecord(BaseModel):
    run_id: str                 # UUID。1 回のカード処理を識別
    card_id: str
    card_name: str
    project: str                # ProjectTag
    workflow: str | None        # 棄権時は None
    branch: str | None
    started_at: datetime
    finished_at: datetime
    ok: bool                    # 例外なく終端処理まで到達したか
    blocked: str
    comment: str                # カードに残したコメント
    session_ids: list[str]
```

### ポート

```python
class TranscriptLocator(ABC):
    @abstractmethod
    def locate(self, session_id: str) -> Path | None: ...

class ExecutionLogStore(ABC):
    @abstractmethod
    def save(self, record: ExecutionRecord, transcripts: dict[str, Path]) -> None:
        """record と、session_id → トランスクリプトパスの対応を永続化する。"""
```

## アプリケーション層

### `CollectExecutionLogUsecase`

回収と投入をまとめた唯一の場所です。

```python
class CollectExecutionLogUsecase:
    @inject
    def __init__(self, locator: TranscriptLocator, store: ExecutionLogStore) -> None: ...

    def execute(self, record: ExecutionRecord) -> None:
        transcripts: dict[str, Path] = {}
        for sid in record.session_ids:
            path = self.locator.locate(sid)
            if path is None:
                logger.warning("transcript not found: session=%s run=%s", sid, record.run_id)
                continue
            transcripts[sid] = path
        self.store.save(record, transcripts)
```

### `ProcessCardUsecase` の変更

`execute` の冒頭で `run_id` と `started_at` を決め、本体を `bind_session_sink()` で包み、末尾（カードを In review へ移した後）で collector を呼びます。collector の例外は捕捉して警告ログにします。

```python
def execute(self, card: Card) -> None:
    ...
    with bind_session_sink() as sessions:
        try:
            ...  # 既存の処理
        except Exception as e:
            ...
        card.add_comment(...)
        card.move_to_review()

    try:
        self.collector.execute(ExecutionRecord(..., session_ids=list(sessions)))
    except Exception:
        logger.exception("execution log collection failed: %s", card.id)
```

`ProcessCardUsecase` は「claim → 実行 → 終端」の責務にログ収集を足すことになりますが、呼び出しは 1 行で、失敗を握りつぶす境界もここに閉じるので許容します。

## インフラ層

### `ClaudeTranscriptLocator`

```python
class ClaudeTranscriptLocator(TranscriptLocator):
    def __init__(self, projects_dir: Path = Path.home() / ".claude" / "projects") -> None: ...

    def locate(self, session_id: str) -> Path | None:
        hits = list(self._projects_dir.glob(f"*/{session_id}.jsonl"))
        return hits[0] if hits else None
```

### `DuckDbExecutionLogStore`

テーブルは 2 つです。

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id      VARCHAR PRIMARY KEY,
    card_id     VARCHAR,
    card_name   VARCHAR,
    project     VARCHAR,
    workflow    VARCHAR,
    branch      VARCHAR,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    ok          BOOLEAN,
    blocked     VARCHAR,
    comment     VARCHAR,
    session_ids VARCHAR[]
);

CREATE TABLE IF NOT EXISTS events (
    run_id        VARCHAR,
    session_id    VARCHAR,
    seq           INTEGER,      -- ファイル内の行番号
    timestamp     TIMESTAMP,
    type          VARCHAR,      -- user / assistant / summary ...
    role          VARCHAR,
    tool_name     VARCHAR,      -- tool_use ブロックの name（複数なら先頭）
    input_tokens  INTEGER,
    output_tokens INTEGER,
    raw           JSON,         -- 行を丸ごと保持（正）
    PRIMARY KEY (session_id, seq)
);
```

投入は `read_json_auto` でファイルを直接読み、抽出列は SQL の JSON 関数で埋めます。`format='newline_delimited'` を明示し、行ごとに形が違っても落ちないよう `union_by_name` を使います。

```sql
INSERT OR IGNORE INTO events
SELECT
    ? AS run_id,
    ? AS session_id,
    row_number() OVER () AS seq,
    try_cast(raw->>'timestamp' AS TIMESTAMP),
    raw->>'type',
    raw->'message'->>'role',
    raw->'message'->'content'->0->>'name',
    try_cast(raw->'message'->'usage'->>'input_tokens' AS INTEGER),
    try_cast(raw->'message'->'usage'->>'output_tokens' AS INTEGER),
    raw
FROM read_json_auto(?, format='newline_delimited', union_by_name=true, records=false) AS t(raw);
```

抽出列は「よく使う集計に必要な最小限」に絞ります。形式が変わったら `raw` から再抽出します（`UPDATE events SET tool_name = raw->...`）。

同じ session_id を複数 run が共有することはない前提ですが、再投入に備えて `INSERT OR IGNORE` にします。

DuckDB は単一プロセス書き込みが前提です。`max_parallel` によりカードは並列処理されるため、`save` はプロセス内のロックで直列化します。将来プロセスをまたぐ場合はファイルロックか、run ごとに JSONL を書き出して別途取り込む方式へ切り替えます。

### 設定

```toml
[execution_log]
enabled = true
db_path = "~/.local/state/vuoi/execution.duckdb"
# claude_projects_dir = "~/.claude/projects"   # 既定値
```

`enabled = false` なら `NullExecutionLogStore` を束縛し、collector は何もしません。

## 分析の例

DuckDB CLI からそのまま引けます。

```sql
-- カードごとのトークン量とセッション数
SELECT r.card_name, count(DISTINCT e.session_id) AS sessions,
       sum(e.input_tokens) AS in_tok, sum(e.output_tokens) AS out_tok
FROM runs r JOIN events e USING (run_id)
GROUP BY 1 ORDER BY out_tok DESC;

-- ツール別の呼び出し回数
SELECT tool_name, count(*) FROM events
WHERE tool_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC;

-- 1 セッションの流れを追う
SELECT seq, type, tool_name, raw->'message'->'content'->0->>'text' AS text
FROM events WHERE session_id = ? ORDER BY seq;
```

MLflow Tracing や Langfuse のような UI が欲しくなった場合も、`events` テーブルからスパンを組み立ててエクスポートすればよく、収集経路は変わりません。

## 段階的な導入

1. `vuoi_sdk` に `bind_session_sink` / `record_session` を追加し、`ClaudeCliRunner` から呼ぶ。
2. `ExecutionRecord` とポート、`CollectExecutionLogUsecase` を追加し、`ProcessCardUsecase` から呼ぶ。この時点では `NullExecutionLogStore` で動作を確認する。
3. `ClaudeTranscriptLocator` と `DuckDbExecutionLogStore` を実装し、`[execution_log]` 設定で有効化する。

## 未決事項

- トランスクリプトの JSON 構造（`type` の種類、`content` ブロックの形）は Claude Code のバージョン依存。抽出列のパスは実物のファイルを確認して確定する。
- `vuoi workflow run`（単発実行）でもログを取るか。現状は対象外。
- 生ファイルを DuckDB とは別に run ディレクトリへコピーして保管するか。`cleanupPeriodDays` で消える前に DuckDB へ入るので当面は不要とみるが、`raw` 列で復元できるかは形式次第。
