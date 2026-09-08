# vuoi コードレビューワークフローで PR にコメントが付かない現象の調査

## 背景・問い

カード https://trello.com/c/bKQ0DuU1/ より。vuoi に PR レビューを任せても GitHub の PR には何も付かず、結果は Trello カードのコメントにしか残らない。

- なぜ PR にコメントが付かないのか（未実装なのか、実装はあるが動いていないのか）
- 「PR コメント・コードコメント・レビュー下書きまでしてほしい、可能か？」に答える

## 調査方法（見たもの・手順・前提）

1. ワークフロー実体を読んだ: `~/.config/vuoi/workflows/{dev,task,merge,research,...}`（この Linux ホストの運用設定リポジトリ）。
2. ホスト側の GitHub 操作を読んだ: `src/chevuoi/domain/ports/pull_request_publisher.py`、`src/chevuoi/infrastructure/git/gh_pull_request_publisher.py`、`src/chevuoi/application/usecases/process_card_usecase.py`。
3. 設計文書 `docs/design/20260830-pr-review-workflow/design.md` と仕様 `docs/spec/gate-review.md` を読み、実体との差分を取った。
4. 実行ログ `~/.local/state/vuoi/vuoi.log` で、PR レビューのカードが実際にどのワークフローへ振られたかを確認した。
5. `~/.claude/projects/**/<session_id>.jsonl`（claude のトランスクリプト）を解析し、PR レビューの 3 実行（`trello-mSzv554A` の 2 回、`trello-fvl74LSH` の 1 回）が実際にどのツールを叩いたかを確認した。
6. `gh auth status` と `claude --help` で、投稿に必要な権限・オプションが環境側で揃っているかを確認した。

前提: レビューの実行主体は `claude -p`（`ClaudeCliRunner`）であり、GitHub への書き込みはホストか LLM のどちらかが `gh` を叩く以外に経路はない。

## 結果（事実）

### 1. PR レビューのカードは `task` ワークフローに振られている

`~/.local/state/vuoi/vuoi.log` の実測:

```
2026-09-02 00:56:11 ルーティング(LLM): 未来リサーチ PRコードレビュー -> task (confidence=high)
  既存 PR (#143) のレビュー作業であり、リポジトリへのコミットや PR 作成は発生しない。
  task の when_to_use が「他 PR のレビュー」を明示的に挙げており…
2026-09-02 02:23:35 ルーティング(LLM): 未来リサーチ PR レビュー -> task (confidence=high)
```

「他 PR のレビュー」を自分の担当だと宣言しているのは `task/workflow.toml` の **`summary`**（`"運用作業フロー: PR を作らない作業（他 PR のレビュー、チケット整理、情報の転記など）…"`）である。`when_to_use` は「リポジトリへのコミットや PR が発生しない作業。コードを変更するなら dev、調査して報告書を残すなら research を使う」で、この文字列は含まない（ログのルーティング理由が `when_to_use` と書いているのはモデル側の言い間違い）。いずれにせよ設計文書の「現状、PR レビューのカードは汎用の `task` ワークフローが引き受けている」という記述と一致する。

### 2. `task` ワークフローは GitHub への出口を持たず、投稿するかは LLM の裁量任せになっている

`~/.config/vuoi/workflows/task/workflow.py` の唯一のノード `work` のプロンプト:

- 「これは PR を作らない運用作業です」
- 「リポジトリのコミット・push・PR 作成はしない」
- 「最後に、カードのコメントにそのまま使える形で成果を報告してください」

GitHub へコメントせよという指示はない。`workflow.toml` の `outcome = "comment"` も、**ホストが用意する**成果の出口がカードコメント 1 本であることを示している。

ただし投稿を禁じてもおらず、`gh` は LLM が自由に叩ける。実測した PR レビュー 3 実行の内訳:

| 実行 | worktree / session | 人間からの追加指示 | PR への投稿 |
|---|---|---|---|
| 2026-09-02 00:56（PR #143） | `trello-mSzv554A` / `5828c50d-…` | なし | なし |
| 2026-09-02 02:23（PR #143、差し戻し再実行） | `trello-mSzv554A` / `9aab62c1-…` | 「このコメントをPRコメントに入れて」 | **あり** |
| 2026-09-02 02:23（PR #146） | `trello-fvl74LSH` / `faf7b8f2-…` | なし | なし |

2 回目は `gh pr comment 143 --body-file /tmp/review143.md` を実行して成功し、ツール結果として
`https://github.com/laboroai/pjt_mirai_research_poc/pull/143#issuecomment-5497811621` を受け取っている。
つまり **PR コメントは現状のワークフローのままでも投稿できており、投稿されないのは既定のプロンプトに指示が無く、
投稿するか否かが LLM の裁量に委ねられているためである**。

レビュー内容そのものの実挙動:

- `gh pr view` / `gh pr diff` / `gh pr checks` で PR を読み、対象ブランチを checkout し、`pytest`（541 passed / 1 skipped / 1 xfailed）と検証スクリプトまで走らせている
- 根拠付きの所見を複数出しており、レビューの中身の質は高い
- 一方でインラインの行コメント（`gh api …/pulls/{n}/reviews` の `comments[]`）はどの回でも使っていない。投稿された 1 件も PR 全体への通常コメント
- 権限拒否は 3 回とも発生していない（`gh` の読み取り・`gh pr comment` による書き込み・`git checkout`・`/tmp` への書き込みはすべて成功）
- 最終出力には「カードコメント用の報告」にあたる Markdown が含まれる

### 3. `dev` の `code_review` ノードも PR にはコメントしない

`~/.config/vuoi/workflows/dev/workflow.py`:

```python
r = ctx.runner.run(f"/code-review {review_level}", cwd=ctx.workdir, session_id=None)
```

- `--comment` を付けていない
- PR を作るのは `ProcessCardUsecase.finalize`（`process_card_usecase.py`）で、ワークフローが終わった後。初回の実行では `code_review` の時点で PR がまだ存在しない（差し戻しの再実行では `GhPullRequestPublisher.publish` が既存 PR を再利用するので PR 自体は存在するが、ワークフローはその番号を知らない）
- レビュー対象も未コミットの作業ツリー差分であり、PR の diff ではない（プロンプトも「未コミット差分をレビューしました」と書いている）

### 4. ホスト側に「PR へコメントする」経路が存在しない

ホストの GitHub 操作は `PullRequestPublisher` ポートと `GhPullRequestPublisher`（`git add` / `commit` / `push` / `gh pr create`）だけ。`grep -rn 'PullRequestCommenter\|report_finding\|ReviewFinding\|pr_review' src/ tests/ docs/spec/` は 0 件。

### 5. 設計は済んでいるが、第 1 段階すら未実装

`docs/design/20260830-pr-review-workflow/design.md`（コミット `40daa6c`）は本件をそのまま解いている。

- 第 1 段階: `~/.config/vuoi/workflows/pr_review/`（PR 番号抽出 → `gh pr checkout --detach` → `/code-review <PR> <level> --comment`）
- 第 2 段階: `PullRequestCommenter` ポート + `gh api pulls/{n}/reviews`、`ctx.report_finding()`、機械検証・重複排除・冪等投稿

実体は `~/.config/vuoi/workflows/` に `_template / academic_writing / design / dev / doc / merge / research / task` のみ。**`pr_review` も `_shared/pr_numbers.py` も存在しない**。

### 6. 投稿するための環境条件は揃っている

- `gh auth status`: `ishida722` でログイン済み、スコープは `gist, read:org, repo`。`repo` があれば PR コメント・レビューの作成は可能。結果 2 のとおり実際に投稿が成功しており、机上の確認ではない
- `claude --help`: `--permission-mode` は `auto` を受け付ける。`ClaudeCliRunner.build_command` は `8adde6e` で `--permission-mode auto` を渡すようになっており、非対話の `-p` でも書き込み系ツールが拒否されない

### 7. `/code-review` が空 diff のとき無関係な差分をレビューしている（別事象）

`dev` の `code_review` セッションのトランスクリプト:

- `…-trello-k7xziIiV/68d661e6-….jsonl`（2026-09-01 19:45 JST、カード「未来リサーチ 後処理MA補正の実装」）
  > **Scope note:** the current branch `chevuoi/trello-k7xziIiV` has zero commits ahead of `main` and a clean working tree, so `git diff main...HEAD` and `git diff HEAD` are both empty. I fell back to `git diff HEAD~1`, which is **the most recently merged PR (#144 …)**
- `…-trello-Ng9Ezj63/b7fd5217-….jsonl`（2026-09-01 18:40 JST、AF のカード）
  > the branch has no upstream and is identical to `main` … so I reviewed the tip commit `b12007a` (`git diff HEAD~1`)

いずれも実装ノードが差分を作れていない回で、`/code-review` は差分ゼロを検出して `HEAD~1`（直前にマージ済みの PR）にフォールバックし、そこへの所見を返している。

## 考察と結論

PR にコメントが付かない原因は、バグでも権限不足でもなく **決定的な投稿経路が未実装で、投稿するか否かが LLM の裁量に委ねられている**ことである。内訳は 3 点。

1. **経路が決定的でない。** PR レビューのカードは `task` に落ちるが、`task` は「カードにコメントして終わる」ワークフローで、ホスト側に GitHub への出口を持たない。プロンプトにも投稿の指示が無いため、投稿は毎回ぶれる。実測 3 回のうち、人間が「PRコメントに入れて」と明示した 1 回だけ投稿された（結果 1・2）。設計済みの `pr_review` ワークフローが作られていない（結果 5）。
2. **`dev` のレビューは PR コメント用ではない。** `--comment` が無いうえ、初回は PR 作成前に走り、対象も PR の diff ではなく未コミット差分なので、PR へのコメントには使えない（結果 3）。
3. **ホストに口が無い。** `PullRequestPublisher`（PR を作る）はあるが `PullRequestCommenter`（PR にコメントする）は設計文書の中だけ（結果 4）。

一方でレビューの中身の質は十分に高く、投稿の権限も揃っており、PR コメント自体は現状の仕組みのままで一度成功している（結果 2・6）。足りないのは**出力先の配管を決定的にすること**である。

### 「PR コメント・コードコメント・レビュー下書きは可能か」への回答

いずれも可能。実現コストの順:

| やりたいこと | GitHub 側の口 | 実現方法 | 見積もり |
|---|---|---|---|
| **PR コメント**（PR 全体への通常コメント 1 件） | `gh pr comment <n> --body` | ワークフローのプロンプトで指示するだけで動く（結果 2 で実測済み）。毎回確実にやるならホストの `PullRequestCommenter` から | 小 |
| **コードコメント**（ファイル・行に紐づくインラインコメント） | `POST repos/{o}/{r}/pulls/{n}/reviews` の `comments[]`（`path` / `line` / `side=RIGHT`） | 第 1 段階は `/code-review <PR> <level> --comment` に委譲。第 2 段階はホストが `gh api` で 1 リクエスト投稿 | 第 1 段階は小、機械検証・冪等化まで入れると中 |
| **レビュー下書き**（GitHub の Pending review） | 同じ `reviews` API で **`event` を省略**すると PENDING になる | ホストが `event` を付けずに投稿し、人間が GitHub 上で本文を直して Submit | 中（第 2 段階の `post_review` に分岐を足す） |

補足:

- 「レビュー下書き」は運用上いちばん筋が良い。PENDING レビューは作成した認証ユーザー本人にしか見えないため、`gh` のトークンが石田さん自身のアカウントである現状では「bot が下書きを用意し、本人が目を通して Submit する」形になり、誤指摘を PR 参加者に晒さずに済む。設計文書の「approve / request changes は人間の判断」という方針とも方向は揃う。**ただし設計文書は「`event` は常に `COMMENT`」と明記している（design.md のスコープ対象外の項）ので、`event` 省略の PENDING を採るなら設計の変更にあたる**。また **PENDING の挙動は API 仕様から判断しただけで実投稿では未検証**なので、最初に 1 回だけ実 PR で確かめること。
- 承認（APPROVE）は対象外のままでよい。自分の PR には APPROVE できないという GitHub の制約もある。

### 結果 7 についての考察

`/code-review` の対象ズレは本件（PR にコメントが付かない）とは別問題だが、コードレビュー経路の信頼性に直接効く。差分ゼロのときにフォールバック先の所見をそのまま `apply_review` へ渡しているため、今回のタスクと無関係な指摘の修正に走るおそれがある。別カードとして `vuoi-proposal` で申告した（起票の可否はホストが判断する）。

## 参考（ファイル・URL・ログの場所）

- Trello カード: https://trello.com/c/bKQ0DuU1/
- `~/.config/vuoi/workflows/task/workflow.py` — `work` ノードのプロンプト（GitHub への出口が無い）
- `~/.config/vuoi/workflows/task/workflow.toml` — `summary` に「他 PR のレビュー」（`when_to_use` ではない）
- `~/.config/vuoi/workflows/dev/workflow.py` — `code_review`（`--comment` なし、PR 作成前）
- `~/.config/vuoi/workflows/merge/workflow.py:37` — 再利用できる `PR_PATTERN`
- `src/chevuoi/application/usecases/process_card_usecase.py` — `finalize`（PR 作成はワークフロー終了後）
- `src/chevuoi/infrastructure/git/gh_pull_request_publisher.py` — 唯一の GitHub 書き込み経路
- `src/chevuoi/infrastructure/workflows/claude_cli_runner.py` — `--permission-mode auto`
- `docs/design/20260830-pr-review-workflow/design.md` — 本件の設計（未実装）
- `docs/spec/gate-review.md` — 所見の形式と機械検証の仕様
- `~/.local/state/vuoi/vuoi.log` — ルーティング実績
- `~/.claude/projects/-home-ubuntu-worktrees-vuoi-chevuoi-trello-mSzv554A/*.jsonl` — `task` による PR レビューの実挙動（`9aab62c1-….jsonl` に `gh pr comment 143` の成功が残っている）
- `~/.claude/projects/-home-ubuntu-worktrees-vuoi-chevuoi-trello-fvl74LSH/faf7b8f2-….jsonl` — 指示が無く投稿しなかった回
- https://github.com/laboroai/pjt_mirai_research_poc/pull/143#issuecomment-5497811621 — 実際に投稿された PR コメント
- `~/.claude/projects/-home-ubuntu-worktrees-vuoi-chevuoi-trello-{k7xziIiV,Ng9Ezj63}/*.jsonl` — `/code-review` の対象ズレ

## 次のアクション案

1. **設計の第 1 段階を実装する**（`docs/design/20260830-pr-review-workflow/design.md` の実装手順 1）。`~/.config/vuoi/workflows/pr_review/` を新設し、PR 番号抽出（`merge` の `PR_PATTERN` を `_shared/pr_numbers.py` へ切り出して共用）→ `gh pr checkout <n> --detach` → `/code-review <n> high --comment` → 要約をカードへ、という流れにする。あわせて `task/workflow.toml` の **`summary`**（`when_to_use` ではなくこちらに「他 PR のレビュー」が入っている）からその記述を外し、`pr_review` へ寄せる。ここまでで PR コメントとコードコメントが決定的に動く。
2. **第 1 段階で実測する**（設計文書「第 1 段階で確かめること」）。diff 外の行を指したときの失敗のしかた、同じ PR に 2 回走らせたときの重複、レベル別の所見数。同時に PENDING レビュー（`event` 省略）の挙動も 1 回確認する。
3. **第 2 段階へ進む**。`PullRequestCommenter` / `ctx.report_finding()` / `select_findings` / `PostReviewUsecase` を入れ、レビュー下書きを `event` 省略で出せるようにする。冪等キー `<!-- vuoi:finding:<key> -->` は再実行時の二重投稿防止に必須。
4. **結果 7 を潰す**（別カード）。`code_review` の前に `git status --porcelain` と `git diff main...HEAD` を決定的に確認し、差分が無ければレビューをスキップする。
