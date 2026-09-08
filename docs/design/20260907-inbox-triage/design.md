# Inbox トリアージ機能の設計

## 背景・目的（何を解決する設計か）

ワークフローが作業中に見つけた範囲外の問題を副次タスクとして起票する機能（{doc}`../20260829-card-issuing/design`）を入れた結果、Trello の Inbox が溢れつつある。人間が 1 枚ずつ読む前提が崩れており、次の 3 つが主因である。

1. **似たカードが何度も起票される。** 現在の重複排除は「親カード ID + 正規化タイトルの完全一致」（`TaskProposal.key`）だけなので、親が違えば、あるいは言い回しが 1 文字違えば別カードになる。同じ問題を別のカードから 2 回踏めば、必ず 2 枚起票される。
2. **他ブランチで既に解決済みの問題が起票される。** worktree はカードのブランチをベースブランチから切って作るため、まだマージされていない修正は見えない。その差分を「未解決の問題」と読んで起票してしまう。
3. **溢れた Inbox を人間が読む単価が高い。** 1 枚ずつ開いて、既視感のあるカードを探し、リポジトリを見て解決済みか確かめる作業は、カードが増えるほど線形に増える。

本設計は、Inbox のカード集合そのものを対象に、**重複を代表カードへ集約し、解決済みのカードを畳み、残りに読む順序の手がかり（ラベル）を付ける**バッチ機能を定義する。人間の判断を置き換えるのではなく、人間が読むべきカードの枚数を減らすことが目的である。

### 用語の衝突について（先に決めておくこと）

本リポジトリでは既に「triage」という語を **経路（ワークフロー）の選択**の意味で使っている（{doc}`../../spec/triage`、`SelectWorkflowUsecase`）。本設計が扱うのは受信箱の整理であり、別物である。以後、次のように呼び分ける。

| 呼称 | 対象 | 実装 |
|---|---|---|
| **ルーティング** | 1 枚のカード → 適用するワークフロー | `SelectWorkflowUsecase` / `WorkflowRouter` |
| **Inbox トリアージ**（本設計） | Inbox のカード集合 → 集約・畳み込み・ラベル | `TriageUsecase`（新規） |

既存ドキュメントの見出し変更は実装手順のステップ 1 に含める。

## スコープ（対象 / 対象外）

対象:

- Inbox のカード集合を横断して読む、プロジェクトに紐づかないバッチ実行（`vuoi triage`）
- 決定的な類似度計算による重複候補のクラスタリングと、候補に限った LLM による同一性判定
- ベースブランチを根拠とする「解決済み」判定（決定的シグナル + 読み取り専用の LLM 確認）
- 判定結果の適用: 代表カードへのコメント集約・重複カードのアーカイブ・ラベル付け（既定は dry-run、`--apply` で適用）
- 再実行時に判定をやり直さないためのローカル台帳（ledger）
- 起票時に「どのコミットを見て起票したか」を記録する（トリアージ側の判定材料）

対象外:

- **カードを Ready へ昇格させること。** 仕様の非目標「自動起票カードの自動実行禁止（Inbox 止まり）」（{doc}`../../spec/overview`）を維持する。トリアージは人間の読む枚数を減らすだけで、実行の可否には関与しない。
- カードの削除。アーカイブ（Trello の `closed=true`、可逆）までとする。
- 人間が書いたカードの改変。人間起票カードは代表カードにはなり得るが、アーカイブ・ラベル付けの対象にしない（後述の安全規則）。
- Inbox 以外のリスト（Ready / In Progress / In Review / Blocked）のカード。人間が動かした時点でトリアージの管轄外とする。
- Trello 以外のタスクソース（ポートで抽象化するが実装は Trello のみ）。
- 意味埋め込みによる類似検索（Strategy で差し替え可能にするが、実装は文字トライグラム）。

## 現状分析（既存コードの構造・問題点）

### 現行ワークフローは「1 カード = 1 プロジェクト = 1 worktree」で固定されている

`RunUsecase` → `ProcessCardUsecase.execute` の流れは次のとおりで、すべての段がカード 1 枚とプロジェクト 1 つを前提にしている。

```
fetch_ready_cards() → claim() → resolve_project()（NullProject なら needs_human）
  → SelectWorkflowUsecase → WorktreeManager.create(project, card)
  → GraphExecutor.execute(workflow, message, workdir=worktree.path, project=project)
  → IssueProposalsUsecase → finalize（PR / コメント）→ move_to_review()
```

- `ProcessCardUsecase.resolve_project` はタイトル先頭のタグ（`ProjectTag.from_title`）を設定表で引く。引けなければ `NullProject` で人間に返す。つまり**プロジェクトが決まらない仕事は現状ひとつも実行できない**。
- `WorktreeManager.create(project, card)` はブランチ名をカード ID から決定的に導出する（`BranchName.from_card_id`）。カードに紐づかない作業環境を作る口はない。
- `Card` ABC は「1 枚を処理する」ための抽象データ型で、`claim` / `add_comment` / `move_to_review` を持つ。**他のカードを参照・操作する口はなく、集合として扱う口もない**（取得だけが `CardProvider` ポートに切り出されている）。
- `TrelloCardProvider.fetch_ready_cards` は Ready リストしか見ない。Inbox を読むのは `TrelloCardIssuer.find_by_key`（冪等キー照合）だけで、カード本文とタイトルしか取っていない。

トリアージは「カード集合が入力」「プロジェクトは各カードの属性でしかない」「成果物は PR ではなくカードの状態」なので、この流れには載らない。**新しい実行単位（ボード単位のラン）を 1 つ足すことが、この設計の中心**である。

### 起票側の重複排除は構造的に弱い

- `TaskProposal.key(parent)` は `sha1(親カード ID + 正規化タイトル)` なので、**親が違えば同じ問題でも別キー**になる。
- `select_proposals` の重複排除は 1 ラン内の完全一致のみ（`normalize_title` は casefold と空白畳み込みだけ）。
- `TrelloCardIssuer.find_by_key` は Inbox 一覧のフッター照合で、**Inbox から出たカードは探さない**（{doc}`../20260829-card-issuing/design` の未決事項）。

つまり「起票時に頑張る」方向では、親をまたぐ重複と言い換えの重複は原理的に取り切れない。起票は安く保ち、**集合を見渡せる場所（トリアージ）で畳む**のが素直である。

### 起票時に「どの時点を見たか」が残っていない

`TrelloCardIssuer.build_footer` が本文末尾に書くのは `key` / `parent` / `generation` / `kind` だけである。どのコミットを見て起票したかが残っていないため、後から「その後この箇所は変わったか」を機械的に判定できない。フッターの書式は `vuoi: <key>=<value> ...` の汎用形（`parse_footer`）なので、キーの追加は後方互換に行える。

### 既存の非決定性の封じ込め方（踏襲すべき型）

`ClaudeWorkflowRouter` は良い先例である。LLM には**候補を 1 つ返させるだけ**で、実在検証・確信度による棄権・失敗時の棄権はすべて Python 側にあり、例外を投げず `RoutingDecision` という値で返す。トリアージの判定も同じ型（判定は値、行動は決定表）で作る。

### 既存機能との干渉点

- `ProcessCardUsecase._is_bot_comment` は「1 行目が 🤖 で始まるコメント」を自動処理のものとみなし、それより新しい人間コメントだけをプロンプトへ渡す。**トリアージが付けるコメントも 1 行目を `🤖 triage:` で始めなければ、人間の追加指示として再実行時に読まれてしまう。**
- ポートに Trello の語彙を漏らさない（既存の `CardProvider` / `CardIssuer` が守っている規律。既存設計は INV-1 / INV-3 / INV-4 までしか番号を振っていないので、本設計ではこれを **INV-5** と呼ぶ）。`"Inbox"` や `closed` などの文字列は `infrastructure/trello/` の中だけに置く。

## 設計方針（採用する原則と、その理由）

1. **トリアージはホストの機能とし、ユーザー定義ワークフローにはしない。** ワークフローは「1 カード + worktree」を前提に設計されており（`ctx.workdir` / `ctx.project`）、カード集合を渡す口も、カードを操作する口も SDK にない。カード操作を SDK に開けば「判断はホスト、申告はワークフロー」（{doc}`../20260829-card-issuing/design` 方針 2）が崩れ、`vuoi_sdk` がホストのドメインを知ることになる。**LLM を呼ぶ部分だけ**は既存の `Runner` ポート（`claude -p`）を使い、ワークフロー機構とは独立させる。
2. **判定は LLM、行動は決定表（INV-1）。** LLM が返してよいのは「この 2 枚は同じ問題か」「この指摘は現在のベースで解消済みか」という**単一の判定と確信度と理由**だけである。何を残し何を畳むかは `triage_policy` の純粋関数が決める。ルーターと同じく、確信度が high でなければ棄権し、棄権は「人間が見るべき」ラベルに落とす。
3. **LLM を呼ぶ前に決定的にふるいをかける。** 全カードの総当たりを LLM に投げない。文字トライグラムの類似度でペア候補を作り（無料・決定的）、閾値を超えたペアだけを LLM に照会する。「解決済みか」も、フッターの起票時コミットとベースブランチの差分から `git log` で機械的に絞り込む。LLM 呼び出し回数には 1 ラン上限を置く（予算）。
4. **既定は dry-run。破壊的操作は明示的に有効化する。** 誤アーカイブのコストは「人間がカードを 1 枚見失う」ことなので、`vuoi triage` は既定で計画を表示するだけとし、`--apply` を付けたときだけ適用する。適用する操作は Trello のアーカイブ（可逆）に限り、必ず理由コメントを残す。
5. **人間が書いたカードは変更しない。** 対象は本文フッター（`vuoi: key=...`）を持つ自動起票カードに限る。人間起票カードは重複クラスタの代表にはなり得る（＝自動カードの方を畳む）が、それ自身は畳まない。安全側の非対称性を仕様として固定する。
6. **「解決済み」の根拠はベースブランチだけにする。** 他のブランチや未マージの PR に修正があっても「解決済み」とは見なさない。カードが起票された原因（ベースとの差分）を、同じベースを基準に判定することで、判定が実行時点のブランチ状況に依存しなくなる。
7. **設計指針からの取捨選択。**
   - **Repository パターンは採る。** 本機能はカードを「ID で引き、集合で取り、状態を書き戻す」対象として扱う。既存の `Card` ADT は 1 枚を処理する抽象で、集合操作・他カード参照に向かない。`TriageCardRepository`（リモートが真実源）と `TriageLedger`（ローカル台帳）の 2 つを置く。既存設計が採らなかった Repository を、ここでは素直に当てはめられる。
   - **Strategy パターンも採る。** 類似度アルゴリズムは、まずトライグラムで始めて後で埋め込みに差し替えたい箇所であり、切り替え軸がはっきりしている。指針のファクトリ + `binder.bind` の形（パターン 4）をそのまま使う。
   - **エンティティは Pydantic の不変値にする。** クラスタリングと決定表を純粋関数で書き、外部 I/O なしで単体テストできるようにするため、カードは振る舞いを持たないスナップショット（`TriageCard`）として扱う。
   - **`@provider` は使わない。** 複雑な初期化がないため、すべて `binder.bind` と `@inject` の自動解決で足りる（既存 `AppModule` と同じ）。
8. **段階を踏む。** 第 1 段階は「収集 → クラスタリング → 重複判定 → dry-run 表示」まで。第 2 段階で適用と解決済み判定を入れる。閾値の初期値には根拠がないので、第 1 段階を dry-run で回して較正してから破壊的操作を有効にする（{doc}`../20260830-pr-review-workflow/design` と同じ進め方）。

### Inbox トリアージの仕様

#### 実行単位

- `vuoi triage` の 1 回の実行を **1 トリアージラン**とする。入力は Inbox のカード集合、出力はカードの状態変更と実行レポートである。
- プロジェクトには紐づかない。プロジェクトは各カードの属性（タイトル先頭タグ）として扱い、判定はプロジェクトごとにグループ化して行う。タグが設定表で引けないカードは「解決済み判定」の対象外とし、重複判定のみ行う。
- worktree はカード単位では作らない。解決済み判定が必要なプロジェクトについてのみ、**プロジェクトごとに 1 つの読み取り専用チェックアウト**（detached HEAD、ベース参照）を使い回す。
- `vuoi run` の中では実行しない。並行して起票が走ると Inbox のスナップショットが揺れ、判定が実行順に依存するためである。常駐は `vuoi run` とは別の systemd timer（または borro のループ）で、頻度を落として回す。

#### 対象カードの決定（決定的）

次をすべて満たすカードだけを判定対象（mutable）とする。

| 条件 | 理由 |
|---|---|
| Inbox リストにある | 人間が動かしたカードは管轄外 |
| 本文に `vuoi:` フッターがある（自動起票） | 人間が書いたカードを畳まない |
| 作成から `settle_minutes`（既定 10 分）以上経過している | 実行中のランが今まさに起票したカードを触らない |
| ledger の digest が前回判定時と異なる、または前回が予算超過で判定できなかった | 変化のないカードを再判定しない（コスト） |

最後の行は「前回 `keep` 以外なら再判定」ではない点に注意する。棄権（`review`）は内容が変わらない限り何度やっても同じ結果になるので、再判定しても LLM 呼び出しを捨てるだけである。一方、予算超過で判定に到達しなかったカードは次回で拾う必要があるため、`TriageLedgerEntry` に `reason`（`"budget"` / `"abstained"` / `""`）を持たせて区別する。

Inbox の全カード（人間起票を含む）は、重複クラスタの**代表候補**としては読み込む。作成時刻は Trello の**内部カード ID（24 桁 16 進の ObjectId）**の先頭 8 桁を Unix 秒として読むことで決定的に導出できるので、追加の API 呼び出しは要らない。既存コードの `CardId.external_id` は `shortLink`（8 文字の英数字）であって ObjectId ではないため、`fetch_open` は Trello の `id` フィールドも取得し、時刻の導出にはそちらを使う（`CardId` の中身は既存どおり `shortLink` のままにする。Trello の `/cards/{id}` は shortLink も受け付けるので、書き戻し系の操作は `CardId` だけで足りる）。

#### 判定の 3 層（ルーティングと同じ構造）

**第 1 層 — 決定的な事前判定（LLM 不使用）**

| 条件 | 判定 |
|---|---|
| 同一プロジェクト内に正規化タイトル完全一致のカードがある | `duplicate`（LLM 照会不要） |
| 本文が空、**かつ** evidence が 1 件もない | `needs_info` |
| フッターの `base` コミット以降、evidence の指すパスがベースで一度も変更されていない | `keep`（解決しているはずがない。LLM 照会不要） |
| evidence の指すパスがベースに存在しない | 第 2 層（解決済みか要確認） |
| 類似度が閾値以上のペアがある | 第 2 層（同一かどうか要確認） |
| 上記以外 | `keep` |

行の順序は意図的である。`evidence` は SDK の `Proposal` でも `TaskProposal` でも任意であり（既定は空タプル）、`IssueCardUsecase` は evidence があるときだけ本文に「根拠:」節を書く。したがって evidence を持たない自動起票カードは珍しくない。「本文が空 **または** evidence なし → `needs_info`」にすると、そうしたカードが重複判定に到達する前にすべて `needs_info` へ落ち、本機能の第一の目的（重複の畳み込み）が働かなくなる。条件を **かつ** にし、完全一致の重複判定をその上に置く。

**第 2 層 — LLM による判定（第 1 層で決まらなかったものだけ）**

- 重複判定: 候補ペア (A, B) を渡し、`{"verdict": "duplicate"|"distinct", "confidence": "high"|"low", "reason": "..."}` を返させる。読み取りツールは与えない（タイトル・本文・evidence だけで判断できる問いにする）。
- 解決済み判定: プロジェクトのベースチェックアウトを `cwd` にして読み取り専用ツール（`Read` / `Grep` / `Glob`）で走らせ、`{"verdict": "resolved"|"unresolved", ...}` を返させる。
- 1 ランあたりの照会回数に上限（`max_judgments_per_run`、既定 30）を置き、超えた分は判定せず `review` ラベルだけ付けて次回に回す。この上限は**重複判定と解決済み判定の合計**に対するもので、`max_pairs_per_run` は「候補ペアを何組まで作るか」というクラスタリング側の上限である。両方 30 だと重複判定だけで予算を使い切って解決済み判定が 1 件も走らない構成になるため、消費の優先順位を決めておく: **重複判定を先に、残った予算で解決済み判定を行う**（重複の畳み込みのほうが枚数を減らす効果が大きく、解決済み判定は次回のランに持ち越しても損失がない）。`max_pairs_per_run` は `max_judgments_per_run` 以下に設定する。
- 判定 1 件につき `claude -p` のプロセスを 1 回起動する（`ClaudeWorkflowRouter` と同じ）。予算いっぱいの 30 件を直列に回すとランは数分規模になる。`vuoi run` とは別タイマーで頻度を落として回す前提なので許容するが、並列化はしない（Trello のレート制限と、判定順による非決定性を避けるため）。

**第 3 層 — 棄権パス（必須）**

確信度が high でない、出力が解析できない、`runner` が失敗した場合は、いずれも `review` 判定（＝人間が見る）とする。カードには何もせず、ラベルだけ付ける。必ずどれかを選ばされる判定器は必ず間違える、というルーターと同じ理由である。

#### 行動の決定表（純粋関数。先に一致した行を採る）

| # | 条件 | 行動 | 外部作用 |
|---|---|---|---|
| 1 | 対象外カード（人間起票 / 非 Inbox / settle 内 / 変化なし） | `skip` | なし |
| 2 | 判定 `duplicate` かつ confidence high かつ 代表が自分でない | `merge` | 代表へ集約コメント → 自分にコメント → アーカイブ |
| 3 | 判定 `resolved` かつ confidence high かつ 決定的シグナルあり | `archive` | 理由コメント → アーカイブ |
| 4 | 判定 `needs_info` | `label` | `triage/needs-info` を付ける |
| 5 | 判定が棄権 / 上限超過 | `label` | `triage/review` を付ける |
| 6 | それ以外 | `keep` | なし（ledger に `keep` を記録するだけ） |

行 2 と行 3 は、`config.apply` が偽なら `label`（`triage/duplicate` / `triage/stale`）に降格する。dry-run では外部作用そのものを行わない。

行の順序について。`needs_info`（行 4）は `merge` / `archive` より**下**に置く。情報が薄いカードほど重複している見込みが高く、「本文が空だから人間に書き足させる」より「代表カードへ畳む」ほうが人間の読む枚数が減るためである。

行 6 に外部作用を持たせない理由。`triage/keep` を全カードに付けると、ラベルは Inbox のほぼ全件に付くことになり読む順序の手がかりにならない。加えて、後述のレート制限の見積もり（適用対象カードごとに最大 3 回）が「Inbox の全カード × 1 回」に膨らむ。トリアージ済みであることは ledger に記録すれば足りる。

#### 冪等性（INV-3）

- 台帳に「計画」を書く → 外部作用を実行する → 台帳に「適用済み」を書く、の順で行う。途中でクラッシュした場合、次回のランは計画済みのカードから再開する。アーカイブとラベル付けは元から冪等である。
- コメントは冪等キーを持つ。`🤖 triage:` 行に `digest=<12 桁>` を含め、投稿前に対象カードの直近コメントを照合する（代表カードのみ 1 回の追加 API 呼び出し）。
- 台帳を失っても、真実源は Trello 側の状態（アーカイブ済み・ラベル）であり、再判定のコストが増えるだけで結果は変わらない。

## レイヤー構成とディレクトリ構造

追加・変更するファイルのみ示す（`★` 新規、`*` 変更）。

```
src/chevuoi/
├── domain/
│   ├── entities/
│   │   ├── triage_card.py              ★ TriageCard（不変スナップショット）
│   │   ├── triage_judgment.py          ★ TriageJudgment / Verdict（棄権を表現できる値）
│   │   └── triage_plan.py              ★ TriageAction / TriagePlan / TriageReport
│   ├── ports/
│   │   ├── triage_card_repository.py   ★ TriageCardRepository（取得 + 状態の書き戻し）
│   │   ├── triage_ledger.py            ★ TriageLedger（ローカル台帳。第 2 段階）
│   │   ├── triage_judge.py             ★ TriageJudge（LLM 判定。棄権必須・例外を投げない）
│   │   ├── repository_inspector.py     ★ RepositoryInspector（git の読み取りとベース参照）
│   │   └── card_issuer.py              * CardIssueRequest.base_commit
│   └── services/
│       ├── similarity.py               ★ SimilarityStrategy（ABC）+ 正規化の純粋関数
│       ├── triage_clustering.py        ★ 純粋関数: 候補ペア生成・クラスタ・代表選出
│       └── triage_policy.py            ★ 純粋関数: 決定表 decide()
├── domain/entities/project.py          * Project.base_ref（ProjectConfig からの写像先）
├── application/
│   └── usecases/
│       ├── triage_usecase.py           ★ TriageUsecase（収集 → 判定 → 計画 → 適用）
│       ├── process_card_usecase.py     * resolve_project で base_ref を写す
│       └── issue_card_usecase.py       * 起票時に base_commit を載せる
├── infrastructure/
│   ├── trello/
│   │   ├── trello_triage_repository.py ★ TriageCardRepository の Trello 実装
│   │   └── trello_card.py              * フッターの base / evidence 読み出し（parse_footer は流用）
│   ├── strategies/
│   │   ├── trigram_similarity.py       ★ 文字トライグラム Jaccard
│   │   └── similarity_factory.py       ★ 名前 → 実装クラスの解決
│   ├── triage/
│   │   └── claude_triage_judge.py      ★ claude -p による判定（Runner を使う）
│   ├── git/
│   │   ├── git_repository_inspector.py ★ git の読み取り + ベースチェックアウト
│   │   └── gh_pull_request_publisher.py   （変更なし）
│   ├── state/
│   │   └── json_triage_ledger.py       ★ ~/.local/state/vuoi/triage.json
│   └── config/settings.py              * TriageConfig / ProjectConfig.base_ref / trello.board_id
├── interface/di_modules.py             * 新規ポートの bind と Strategy のファクトリ
└── interfaces/cli/
    └── commands/triage.py              ★ vuoi triage [--apply] [--project TAG] [--limit N]
```

`infrastructure/strategies/` と `infrastructure/state/` は新設ディレクトリである（指針のテンプレートに沿う）。

## 主要コンポーネント

### エンティティ

```python
# domain/entities/triage_card.py
class TriageCard(BaseModel):
    """トリアージ対象カードのスナップショット。振る舞いを持たない不変値。

    集合を純粋関数で扱うため、Card ADT ではなく値として持つ。
    """

    model_config = {"frozen": True}

    id: CardId
    title: str                      # プロジェクトタグを含む生のタイトル
    body: str
    url: str
    created_at: datetime            # Trello はカード ID 先頭 8 桁（Unix 秒）から導出
    labels: tuple[str, ...] = ()
    # --- フッター由来（自動起票カードのみ） ---
    key: str = ""                   # 冪等キー。空なら人間起票
    kind: str = "chore"
    generation: int = 0
    parent_id: CardId | None = None
    base_commit: str = ""           # 起票時に見ていたベースのコミット（新フッター）
    evidence: tuple[str, ...] = ()  # 本文の「根拠:」行から復元

    @property
    def is_auto_issued(self) -> bool:
        return bool(self.key)

    @property
    def project_tag(self) -> ProjectTag | None:
        return ProjectTag.from_title(self.title)

    def digest(self) -> str:
        """再判定要否の判断に使う内容ダイジェスト。タイトル + 本文の正規化 sha1。"""
```

```python
# domain/entities/triage_judgment.py
Verdict = Literal["duplicate", "distinct", "resolved", "unresolved", "needs_info", "unknown"]


class TriageJudgment(BaseModel):
    """判定器の出力。RoutingDecision と同じく、棄権を表現できることが要件。"""

    model_config = {"frozen": True}

    verdict: Verdict = "unknown"
    confidence: Literal["high", "low"] = "low"
    reason: str = ""
    duplicate_of: CardId | None = None

    @property
    def abstained(self) -> bool:
        return self.verdict == "unknown" or self.confidence != "high"
```

```python
# domain/entities/triage_plan.py
TriageAction = Literal["skip", "keep", "label", "merge", "archive"]


class TriagePlan(BaseModel):
    """カード 1 枚に対する行動。決定表の出力であり、適用前に一覧表示できる。"""

    model_config = {"frozen": True}

    card_id: CardId
    action: TriageAction
    labels: tuple[str, ...] = ()
    representative: CardId | None = None   # merge のときの集約先
    reason: str = ""                       # カードに残すコメントの本文になる


class TriageReport(BaseModel):
    """1 ランの結果。CLI の表示とログに使う。"""

    planned: list[TriagePlan] = []
    applied: list[CardId] = []
    failed: list[tuple[CardId, str]] = []
    judgments_used: int = 0
    budget_exceeded: bool = False

    def to_text(self, *, dry_run: bool) -> str: ...
```

### リポジトリインターフェース

```python
# domain/ports/triage_card_repository.py
class TriageCardRepository(ABC):
    """トリアージ対象カードの取得と状態の書き戻し。

    Card ADT ではなくリポジトリにする理由: トリアージは集合を読み、ID で他カードを
    操作する（重複カードを代表カードへ集約する）。1 枚を処理する Card の抽象では
    表現できない。INV-5 に従い、リストの概念（Inbox など）は実装側に閉じる。
    """

    @abstractmethod
    def fetch_open(self) -> list[TriageCard]:
        """トリアージ対象リストの未アーカイブカードを、作成順（昇順）で返す。"""

    @abstractmethod
    def add_comment(self, card_id: CardId, text: str) -> None: ...

    @abstractmethod
    def has_comment(self, card_id: CardId, digest: str) -> bool:
        """同じ digest のトリアージコメントが既にあるか（コメントの冪等性）。"""

    @abstractmethod
    def add_label(self, card_id: CardId, label: str) -> None:
        """ラベルを付ける。既に付いていれば何もしない（冪等）。"""

    @abstractmethod
    def archive(self, card_id: CardId) -> None:
        """アーカイブする。可逆であること（削除しない）。既にアーカイブ済みなら何もしない。"""
```

```python
# domain/ports/triage_ledger.py
class TriageLedgerEntry(BaseModel):
    card_id: CardId
    digest: str
    action: TriageAction
    # 再判定の要否を分けるための理由。"budget" のときだけ、内容が変わらなくても次回再判定する
    reason: Literal["", "budget", "abstained"] = ""
    state: Literal["planned", "applied"]
    updated_at: datetime


class TriageLedger(ABC):
    """ローカル台帳。真実源ではなく、再判定を省くためのキャッシュ + INV-3 の intent 記録。"""

    @abstractmethod
    def load(self) -> dict[CardId, TriageLedgerEntry]: ...

    @abstractmethod
    def record(self, entry: TriageLedgerEntry) -> None: ...
```

```python
# domain/ports/repository_inspector.py
class RepositoryInspector(ABC):
    """プロジェクトのリポジトリを読み取るだけのポート。作業ツリーを汚さない。

    トリアージ以外（起票時の base_commit 記録）でも使う。
    """

    @abstractmethod
    def base_commit(self, project: Project) -> str:
        """ベース参照（Project.base_ref。既定 origin/HEAD）の現在のコミット SHA。
        解決できなければ空文字を返す（例外は投げない）。"""

    @abstractmethod
    def path_exists(self, project: Project, path: str) -> bool:
        """ベース参照にそのパスが存在するか（作業ツリーではなく ref を見る）。"""

    @abstractmethod
    def changed_since(self, project: Project, path: str, since: str) -> bool:
        """since 以降、ベース参照でそのパスに変更があったか。since が無効なら True。"""

    @abstractmethod
    def base_checkout(self, project: Project) -> Path:
        """ベース参照を detached HEAD で置いた読み取り専用チェックアウトを用意して返す。
        プロジェクトごとに使い回し、ブランチを作らないのでカード用 worktree と衝突しない。"""
```

実装上の補足を 3 点、先に固定しておく。

- **ベース参照の解決は既存実装に揃える。** `GitWorktreeManager._base_ref` が既に「`refs/remotes/origin/HEAD` → 本体側の現在ブランチ」の順で解決し、`rev-parse --verify` で実在を確認するロジックを持っている。`GitRepositoryInspector` はこれと同じ順序・同じフォールバック（リモートの無いリポジトリでは本体の HEAD ブランチ）を実装する。両者はプロセス境界も設定も共有しないので、まずは同じ規則を独立に実装し、3 つ目の利用者が出た時点で共通化する。
- **ベース参照は `git fetch` してから読む。** `origin/main` はリモート追跡参照であり、`vuoi` の既存コードはどこでも `fetch` していない。「他ブランチで解決済みのカードを畳む」という本機能の目的は、マージ済みの変更がベースに反映されていて初めて成立する。`GitRepositoryInspector` はトリアージランの開始時にプロジェクトごとに 1 回だけ `git fetch --quiet <remote>` を行い、以降そのランの中では追加の fetch をしない（判定がラン中の時刻に依存しないため）。fetch に失敗した場合はログに残し、そのプロジェクトの解決済み判定を丸ごと見送る（重複判定は続ける）。起票時の `base_commit` 記録では fetch しない（起票を遅くしないため。記録するのは「そのとき見ていたベース」で十分）。
- **ベースチェックアウトの置き場所と後始末。** `config.worktree_root / "triage-base" / <tag>` に `git worktree add --detach` で作る。`GitWorktreeManager.list_stale` はディレクトリ名が `chevuoi-` で始まるものだけを掃除対象にするため、この命名なら `vuoi gc` がカード用 worktree と取り違えて消すことはない。裏を返すと `vuoi gc` では消えないので、`base_checkout` は既存のチェックアウトがあれば `git fetch` 後に `git checkout --detach <base>` で更新して使い回す（作り直さない）。

### Strategy インターフェースと実装

```python
# domain/services/similarity.py
class SimilarityStrategy(ABC):
    """2 枚のカードの類似度を [0.0, 1.0] で返す。決定的であること（同じ入力に同じ値）。"""

    @abstractmethod
    def score(self, a: TriageCard, b: TriageCard) -> float: ...


def normalize_for_similarity(card: TriageCard) -> str:
    """比較用の正規化。プロジェクトタグを落とし、casefold し、空白を畳む。
    タグは同一プロジェクト内では共通で、類似度を一様に押し上げるため除く。"""
```

```python
# infrastructure/strategies/trigram_similarity.py
class TrigramSimilarity(SimilarityStrategy):
    """文字トライグラムの Jaccard 係数。日本語の分かち書きが要らず、外部依存もない。

    タイトルの類似度と evidence パスの一致を重み付きで合成する:
        score = 0.7 * jaccard(title_trigrams) + 0.3 * jaccard(evidence_paths)
    evidence が両方空なら title のみで判断する。
    """
```

`similarity_factory.get_similarity_class(name)` で `"trigram"` → `TrigramSimilarity` を解決する。将来 `"embedding"` を足すときは、実装クラスとマップの 1 行追加だけで済み、DI モジュールは変更不要（指針のファクトリ + bind パターン）。

### 判定器（LLM）

```python
# domain/ports/triage_judge.py
class TriageJudge(ABC):
    """カードの内容から判定を 1 つ返す。LLM を使ってよい箇所はここだけ。

    必ず棄権（verdict="unknown"）を返せること。例外は投げず棄権で表現する
    （WorkflowRouter と同じ契約）。
    """

    @abstractmethod
    def judge_duplicate(self, card: TriageCard, other: TriageCard) -> TriageJudgment: ...

    @abstractmethod
    def judge_resolved(self, card: TriageCard, *, cwd: Path) -> TriageJudgment: ...
```

`ClaudeTriageJudge` は `ClaudeWorkflowRouter` と同じ作りにする。`Runner` を注入し、モデルは `[triage] model`、重複判定はツールなし、解決済み判定は `("Read", "Grep", "Glob")` のみ許可、出力は 1 個の JSON オブジェクトを正規表現で抜き出して解析し、解析不能・実行失敗・候補外の値はすべて棄権に落とす。

「ツールなし」の渡し方だけは既存の契約に穴がある。`Runner.run(allowed_tools=None)` は「Claude Code の既定（＝全ツール許可）」の意味であり、`ClaudeCliRunner.build_command` は `allowed_tools` が `None` でないときだけ `--allowedTools` を付ける。空タプルを渡すと `--allowedTools ""` になるが、この経路は既存テストで踏まれていない。重複判定はタイトル・本文・evidence だけで答えられる問いなので、**空タプルを渡し、`--allowedTools ""` が実際にツールを禁じることをステップ 5 の統合テストで確認する**。確認できなければ `Runner` は変更せず、`ClaudeTriageJudge` 側で `("Read",)` のような無害な最小集合を渡す（プロンプトはリポジトリを見る必要がない問いに保つ）。SDK の公開契約を触らない方針は変えない。

### 純粋関数（ドメインサービス）

```python
# domain/services/triage_clustering.py
class DuplicatePair(BaseModel):
    card_id: CardId
    other_id: CardId
    score: float
    exact_title: bool          # 正規化タイトル完全一致（LLM 照会不要）


def find_duplicate_pairs(
    cards: Sequence[TriageCard],
    similarity: SimilarityStrategy,
    *,
    threshold: float,
    max_pairs: int,
) -> list[DuplicatePair]:
    """同一プロジェクト内のペアだけを比較し、閾値以上を score 降順・ID 昇順で返す。

    ソートキーに ID を含めることで、同スコアでも順序が決定的になる。
    """


def choose_representative(cluster: Sequence[TriageCard]) -> TriageCard:
    """最古のカードを代表とする。同時刻は CardId の文字列順で決める（全順序）。
    人間起票カードが含まれる場合は、作成時刻に関わらず人間起票を優先する。"""
```

```python
# domain/services/triage_policy.py
class DeterministicSignals(BaseModel):
    """決定表に渡す、LLM を使わずに得られた事実。"""

    has_evidence: bool = False
    body_is_empty: bool = True
    path_missing_in_base: bool = False   # evidence のパスがベースに存在しない
    unchanged_since_issue: bool = False  # 起票時コミット以降そのパスが変わっていない
    exact_title_duplicate: bool = False


def decide(
    card: TriageCard,
    *,
    signals: DeterministicSignals,
    judgment: TriageJudgment,
    representative: CardId | None,
    apply: bool,
) -> TriagePlan:
    """決定表（先に一致した行を採る）。外部依存なし・副作用なしで、表そのものを単体テストする。"""
```

### ユースケース

```python
class TriageUsecase:
    """vuoi triage の 1 巡。プロジェクトを横断し、Inbox のカード集合を整理する。

    段の順序:
      1. 収集       cards = repo.fetch_open()（作成順）
      2. 対象決定   settle / 自動起票 / ledger の digest で mutable な集合を絞る
      3. 決定的判定 evidence の有無・タイトル完全一致・git の読み取り（プロジェクト単位）
      4. LLM 判定   決まらなかったものだけ、予算の範囲で judge へ
      5. 計画       decide() で TriagePlan を作り、ledger に planned を書く
      6. 適用       apply が真なら外部作用を行い、ledger に applied を書く
    """

    @inject
    def __init__(
        self,
        cards: TriageCardRepository,
        judge: TriageJudge,
        similarity: SimilarityStrategy,
        inspector: RepositoryInspector,
        ledger: TriageLedger,
        config: AppConfig,
    ) -> None: ...

    def execute(self, *, apply: bool = False, project: str | None = None) -> TriageReport: ...
```

失敗の扱いは `IssueProposalsUsecase` に倣う。カード 1 枚の適用失敗は `TriageReport.failed` に落とし、ランは続ける。判定器の失敗は棄権であり、失敗ではない。

### 設定

```toml
[triage]
enabled = true
model = "haiku"                 # 判定は分類なので軽量モデルで十分（[router] と同じ考え方）
similarity = "trigram"
similarity_threshold = 0.55     # 第 1 段階の dry-run 運用で較正する
max_pairs_per_run = 20          # LLM に照会する重複候補ペアの上限（max_judgments_per_run 以下にする）
max_judgments_per_run = 30      # 1 ランの LLM 呼び出し総数の上限（重複判定 + 解決済み判定の合計）
settle_minutes = 10             # これより新しいカードは触らない
apply = false                   # 既定は dry-run。CLI の --apply が上書きする
ledger_path = "~/.local/state/vuoi/triage.json"

[trello]
board_id = "..."                # ラベル付けに必要（未設定ならラベルは付けず警告）

[projects.MIRAI]
path = "/home/ubuntu/projects/mirai"
base_ref = "origin/main"        # 省略時は origin/HEAD を解決する
```

`AppConfig.triage: TriageConfig = TriageConfig()` としてセクション省略を許す（`RouterConfig` と同じ）。`board_id` / `inbox_list_id` が未設定なら、該当する操作だけを `failed` に落として警告する（既存の `inbox_list_id` は `CardIssueError` を投げる作りだが、トリアージは 1 枚の失敗でランを止めない方針なので、例外にはせず `failed` に落とす）。

**`base_ref` をどう `RepositoryInspector` まで届けるか。** ポートの引数は `Project`（ドメインエンティティ）であり、`ProjectConfig` はインフラ層の型なので、インフラの設定をそのままポートに渡すことはできない。既に `test_commands` が `ProjectConfig` → `Project` へ写して運ばれているので、`base_ref` も同じ経路にする。

- `Project` に `base_ref: str = ""`（空なら実装側が `origin/HEAD` を解決）を足す。
- `ProcessCardUsecase.resolve_project` の `Project(...)` 構築に `base_ref=entry.base_ref` を足す。
- `TriageUsecase` はカード処理を通らないので、同じ写像を行う小さな純粋関数（`resolve_project` 相当）をタグ → `Project` の解決に使う。タグの照合は既存どおり**大文字小文字を無視**する（`ProcessCardUsecase.resolve_project` と同じ規則。`self.config.projects` の素の辞書引きだけにすると挙動がずれる）。

この写像を `ProcessCardUsecase` から切り出して共有するか、トリアージ側に写経するかは実装時に決めてよいが、タグ照合の規則は必ず一致させる。

### DI モジュール

```python
def _configure_triage(self, binder: Binder) -> None:
    binder.bind(TriageCardRepository, to=TrelloTriageRepository, scope=singleton)
    binder.bind(TriageJudge, to=ClaudeTriageJudge, scope=singleton)
    binder.bind(TriageLedger, to=JsonTriageLedger, scope=singleton)
    binder.bind(RepositoryInspector, to=GitRepositoryInspector, scope=singleton)
    # 類似度はファクトリで解決し、bind するだけ（設定を変えても DI の記述は変わらない）
    binder.bind(
        SimilarityStrategy,
        to=get_similarity_class(self._config.triage.similarity),
        scope=singleton,
    )
    # TriageUsecase は @inject の自動解決に任せる（bind 不要）
```

`AppModule.configure` が肥大化してきたため、既存の bind も指針どおり `_configure_repositories` / `_configure_workflows` / `_configure_triage` に分割する。

### CLI

```
vuoi triage                      # dry-run。計画を表示するだけ
vuoi triage --apply              # 計画を適用する
vuoi triage --project MIRAI      # 対象プロジェクトを絞る
vuoi triage --limit 50           # 読むカード数の上限（大きな Inbox の試し打ち用）
```

出力は `vuoi workflow list` と同じく人間が読む整形テキストとし、`merge` / `archive` は集約先・理由を 1 行で示す。終了コードは、適用失敗が 1 件でもあれば 1、それ以外は 0 とする。

## 依存関係

- 依存の方向は既存どおり **Interface → Infrastructure → Application → Domain** のみ。ドメイン層の外部依存は Pydantic だけで、`triage_clustering` / `triage_policy` / `similarity` の正規化は標準ライブラリ（`hashlib` / `re` / `datetime`）のみで書く。
- **新しい外部ライブラリは追加しない。** 類似度は文字トライグラムの自前実装、git 操作は `subprocess`、Trello は既存の `TrelloClient`（httpx）を使う。
- `vuoi_sdk` はこの機能に登場しない。トリアージはワークフロー機構の外にあり、SDK の契約は変わらない。`Runner` は SDK が定義する ABC だが、`ClaudeWorkflowRouter` と同じくインフラ層からのみ import する。
- ただし `TriageUsecase` は既存のユースケース（`ProcessCardUsecase` / `IssueProposalsUsecase`）と同じく `chevuoi.infrastructure.config.settings.AppConfig` を注入する。これは指針の依存方向（application はインフラを知らない）に反するが、リポジトリ全体の既存の慣行であり、本設計だけで直すべきものではない（後述の未決事項）。
- 検算: `grep -rn 'httpx\|subprocess\|vuoi_sdk' src/chevuoi/domain src/chevuoi/application` が空であること。`grep -rn '"Inbox"\|idList\|closed' src/chevuoi --include='*.py' | grep -v infrastructure/trello` が空であること（INV-5）。
- 既存コードへの変更は次の 5 点に限られ、`ProcessCardUsecase.execute` の流れ（claim → 実行 → finalize → In review）そのものには手を入れない。
  1. `CardIssueRequest.base_commit` と `build_footer` の `base=<sha>`（起票側）
  2. `IssueCardUsecase` が `RepositoryInspector` を注入して `base_commit` を載せる
  3. `Project.base_ref` の追加と、`ProcessCardUsecase.resolve_project` での写像（1 行）
  4. `trello_card.py` の読み出し側（`parse_footer` 自体は変更なし）
  5. `AppModule` への bind 追加（既存 bind の分割を伴う）

## 実装手順（dev ワークフローにそのまま渡せる粒度）

各ステップが 1 PR。コード上の依存は 3 → 4 → 5 → 6 と 5 → 7 だけで、1（ドキュメント）と 2（起票側のフィンガープリント）はどこにも依存されない。

ステップ 2 を先頭近くに置くのは依存のためではなく、**データを貯めるため**である。`base` フッターは記録した時点より後に起票されたカードにしか付かず、それを実際に使うのはステップ 7 の解決済み判定なので、2 と 7 の間隔が広いほど 7 の投入時に使えるカードが多い。したがって 2 は 3 と並行して進めてよく、遅らせる理由だけがない。

1. **用語の整理と仕様の追加（ドキュメントのみ）** — `docs/spec/inbox-triage.md` を新設し、本設計の「Inbox トリアージの仕様」節（実行単位・対象カード・判定の 3 層・決定表・冪等性）を機能仕様として書く。既存 `docs/spec/triage.md` は経路選択の話であることが分かる見出し・導入に直し、相互参照を張る。`docs/index.md` の toctree に追加する。

2. **起票時フィンガープリントの記録** — `RepositoryInspector` ポートと `GitRepositoryInspector`（`base_commit` のみ実装）、`ProjectConfig.base_ref`、`CardIssueRequest.base_commit`、`build_footer` への `base=<sha>` 追加、`IssueCardUsecase` での取得（失敗時は空文字で続行）。テスト: フッターの往復（`parse_footer` が `base` を読める）、`base_ref` 省略時の既定、inspector が失敗しても起票が止まらないこと。

3. **カードの収集** — `TriageCard` エンティティ、`TriageCardRepository` ポートと `TrelloTriageRepository.fetch_open`（Inbox 一覧 + フッター解析 + カード ID からの作成時刻導出 + 本文「根拠:」行からの evidence 復元）、`TriageConfig`、`vuoi triage` の骨格（収集して一覧を表示するだけ）。テスト: `test_trello.py` の `MockTransport` に Inbox 一覧を足し、フッターあり・なしの分類、作成時刻の導出、evidence の復元。

4. **類似度と候補クラスタリング（純粋関数）** — `SimilarityStrategy` ABC と `normalize_for_similarity`、`TrigramSimilarity`、`similarity_factory`、`find_duplicate_pairs` / `choose_representative`、DI の bind。`vuoi triage` の出力に重複候補クラスタを追加する。テスト: 同スコアでの順序の決定性、タグを除いた正規化、日本語タイトルでの妥当な閾値挙動、`max_pairs` での打ち切り。

5. **判定器と決定表（dry-run まで）** — `TriageJudgment` / `TriagePlan` / `TriageReport`、`TriageJudge` ポートと `ClaudeTriageJudge`（重複判定のみ）、`DeterministicSignals` と `decide()`、`TriageUsecase`（適用は行わない）、CLI の計画表示。テスト: 決定表の全行（特に evidence を持たないカードが `needs_info` ではなく重複判定に到達すること）、棄権（JSON 壊れ・実行失敗・候補外の verdict）が `review` に落ちること、予算超過で `budget_exceeded` が立つこと、`allowed_tools=()` が実際にツールを禁じること（できなければ最小集合に切り替える）。

6. **適用と台帳** — `TriageLedger` ポートと `JsonTriageLedger`、`TrelloTriageRepository` の `add_comment` / `has_comment` / `add_label` / `archive`（ラベルは名前からボードのラベル ID を解決し、無ければ作成する）、`--apply`、intent → 実行 → 記録の順序、コメント 1 行目の `🤖 triage:` 規約。テスト: 二重適用で API を叩かないこと、台帳が消えても結果が変わらないこと、`board_id` 未設定でラベルだけ落ちること。

7. **解決済み判定** — `RepositoryInspector` に `path_exists` / `changed_since` / `base_checkout` とラン開始時の `git fetch` を実装（プロジェクトごとの detached チェックアウトの使い回し）、`ClaudeTriageJudge.judge_resolved`（読み取り専用ツール、`cwd` はベースチェックアウト）、決定表の行 3 を有効化。テスト: `unchanged_since_issue` が立つと LLM を呼ばないこと、`base_checkout` がブランチを作らないこと、fetch に失敗したプロジェクトでは解決済み判定を丸ごと見送り重複判定は続くこと、判定が high でなければアーカイブしないこと。

8. **（任意）指標の記録** — 判定別件数・LLM 呼び出し回数・適用件数をログに残し、{doc}`../../spec/cli` の指標表に「トリアージの畳み込み率」「アーカイブ復帰率（人間が戻した割合）」を追加する。閾値較正の判断材料にする。

第 1 段階はステップ 1〜5（dry-run まで）、第 2 段階はステップ 6〜8 とする。ステップ 5 の完了後、しばらく dry-run で回して `similarity_threshold` を較正してからステップ 6 に進む。

## 検討した代替案と却下理由

1. **ユーザー定義ワークフローとして実装する（`triage` ワークフロー + SDK に判定チャネル）。** ワークフロー機構は「1 カード + worktree + `ctx.propose`」を前提にしており、カード集合を渡すには SDK にホストのドメイン（カード・プロジェクト）を持ち込む必要がある。カード操作（アーカイブ・ラベル）を SDK に開けば「判断はホスト、申告はワークフロー」が崩れ、プロンプト遵守に依存した破壊的操作が生まれる。まずホスト機能として作り、判定の質が安定してからプロンプトだけを差し替え可能にする方が安い。

2. **`vuoi run` の 1 巡の中でトリアージも行う。** 並行して起票が走るため Inbox のスナップショットが揺れ、判定が実行順に依存する。ランのレイテンシも増え、トリアージの失敗が本流のカード処理に波及する。別コマンド・別タイマーにして、失敗の影響範囲を切る。

3. **起票側の重複排除を強化するだけで済ませる（トリアージを作らない）。** 冪等キーを親カード非依存（タイトルのみ）にする改善は有効だが、言い換えの重複は取れず、「他ブランチで解決済み」は起票時点では原理的に判定できない（そのブランチの内容を見ていない）。ただし本設計は起票側の改善も一部取り込む（`base_commit` の記録）。

4. **Trello の検索 API / カスタムフィールド（Power-Up）で判定状態を持つ。** 検索 API は索引更新が遅延し、テストでも再現しにくい（既存設計の判断）。カスタムフィールドは Power-Up 依存で、他タスクソースへの移植性も落ちる。ローカル台帳 + 本文フッター + ラベルで足りる。

5. **最初から埋め込みモデルで類似度を測る。** 依存（埋め込み API / ベクトル索引）とコストが増える割に、日本語の短いタイトルではトライグラムでも実用的な精度が出る見込みが高い。Strategy にしておけば、較正の結果として必要になった時点で差し替えられる。

6. **Inbox 全体を 1 回の `claude -p` に渡して「整理して」と頼む。** INV-1 違反であり、出力が非決定的で監査もできない。カード数に比例してプロンプトが伸び、上限に当たった瞬間に静かに劣化する。決定的なふるい → 個別の小さな問い、に分ける。

7. **トリアージが「実行してよい」と判断したカードを Ready へ昇格させる。** 仕様の非目標（自動起票カードの自動実行禁止）に真正面から反する。起票 → 実行 → 起票の正のフィードバックを構造的に切っているのが Inbox 止まりの規律であり、トリアージがそれを迂回してはならない。

8. **重複カードを削除する。** 誤判定が取り返しのつかない損失になる。Trello のアーカイブは可逆で、検索からも外れるため、目的（人間が読む枚数を減らす）は十分果たせる。

## 未決事項・リスク

- **誤アーカイブのコスト。** 重複判定の偽陽性は「別の問題を 1 件見失う」ことを意味する。緩和策は、既定 dry-run・`--apply` の明示・可逆なアーカイブ・必ず理由コメントを残すこと・代表カードに集約先として記録すること。それでも残るリスクなので、ステップ 8 で「人間がアーカイブを戻した割合」を計測し、閾値と確信度の要件を見直す。
- **`similarity_threshold` の初期値に根拠がない。** 0.55 は仮置きである。第 1 段階の dry-run 運用で、実際の Inbox に対する候補ペアの適合率・再現率を見て決める。較正前に `--apply` を使わない。
- **evidence の実在検証が起票時に行われていない**（{doc}`../../spec/proposals` の既知の未決事項）。本設計のトリアージは実質的にその検証を後追いで行う形になる。起票時に検証したほうが安いので、将来「起票時に evidence を検証して弾く」ほうへ寄せる可能性がある。そのときトリアージ側の `path_missing_in_base` シグナルは価値が下がる。
- **`base_commit` を持たない既存カード。** ステップ 2 以前に起票されたカードには `base` フッターがない。`changed_since` が使えないため LLM 照会に頼ることになり、コストが上がる。既存カードは「evidence のパスがベースに無い」場合のみ第 2 層へ回し、それ以外は `keep` にする（安全側）。
- **「解決済み」の偽陽性。** 別ブランチや未マージ PR に修正がある場合、ベース参照だけを見る本設計は「未解決」と判断する。これは意図した挙動だが、そのカードを人間が Ready に上げると、実装ランがベースから切った worktree で「既にある修正」を再実装する可能性は残る。これはトリアージではなくベース選択の問題であり、別途扱う。
- **Trello API のレート制限。** トークンあたり 10 秒 100 リクエストの制限がある。1 ランの呼び出しは「Inbox 一覧 1 回 + 適用対象カードごとに最大 3 回」で、Inbox 100 枚・適用 20 枚なら 60 回程度に収まる。ラベル ID の解決はラン内でキャッシュする。上限に当たった場合のバックオフは実装時に決める（現行の `TrelloClient` にリトライ機構はない）。
- **`RunUsecase` の並列実行との競合。** `settle_minutes` は緩和策であって排他ではない。同時刻に起票されたカードを取りこぼす（次回のランで拾う）ことは許容する。プロセス間ロックは置かない。
- **`TriageCard.evidence` の復元が本文の書式に依存する。** `IssueCardUsecase` が本文に書く「根拠:」+ `- path:line` の形を逆解析する。書式を変えると壊れるので、将来はフッターに `evidence=` として構造化して持たせるほうが堅い（ステップ 2 で同時にやってもよいが、既存カードは救えない）。
- **トリアージ結果の通知先。** 現状は標準出力とログのみである。dry-run を人間が読むには何らかの通知（要約カードの更新・Slack など）があったほうがよいが、要約カードを毎回起票すると Inbox を汚す本末転倒になる。通知手段は未決とする。
- **`SimilarityStrategy` を `domain/services/` と `domain/ports/` のどちらに置くか。** 設計指針のテンプレートは Strategy インターフェースを `domain/services/` に置くとしているが、本リポジトリは ABC をすべて `domain/ports/` に集めており（`WorkflowRouter` / `CardIssuer` / `GraphExecutor` など）、`domain/services/` には純粋関数だけが入っている（`proposal_policy` / `workflow_selection`）。本設計は指針側に寄せて `domain/services/similarity.py` に置いたが、リポジトリの慣行に寄せて `domain/ports/similarity_strategy.py` にし、`normalize_for_similarity` だけを `domain/services/` に残す選択もある。同じ理由が `TriageJudge`（ポートに置いた）にも当てはまるので、実装時に**どちらかに統一する**こと。
- **application 層が `AppConfig`（インフラ層の型）に依存している。** 既存の `ProcessCardUsecase` / `IssueProposalsUsecase` / `RunUsecase` が既にそうなっており、`TriageUsecase` もそれに倣う。指針の依存方向には反するので、直すなら設定用のドメイン型を切ってリポジトリ全体で一斉に移す作業になる。本設計のスコープでは扱わない。
- **`trello.board_id` を設定に足すべきか。** ラベル ID の解決に board を知る必要があるが、`GET /cards/{id}?fields=idBoard` でカードから引ける（ラン内で 1 回キャッシュすれば追加コストは 1 リクエスト）。設定項目を増やさないほうが運用は楽なので、実装時にどちらを採るか決める。設定を足す場合でも、未設定時はラベル付けだけを落として他の適用は続ける。
- **重複判定の予算 30 が現実的か未検証。** 溢れた Inbox（100 枚超）では閾値を超える候補ペアが 30 を大きく上回る可能性がある。その場合、毎ランで上位 20 ペアだけを処理して残りを次回に回すことになり、収束に何ランかかるかは較正するまで分からない。ステップ 5 の dry-run では「閾値超えのペアが実際に何組出るか」を必ずログに出す。
- **`TriageJudgment.duplicate_of` と `decide(representative=...)` が重複している。** 代表カードは決定的なクラスタリング（`choose_representative`）が決めるので、LLM に代表を選ばせる余地は残さないほうがよい。`duplicate_of` は「LLM が同一と判断した相手」の記録に留め、行動の決定には `representative` だけを使う、という規律を実装時に明文化する。
