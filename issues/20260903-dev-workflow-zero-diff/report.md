# dev ワークフローで実装ノードが差分ゼロのまま完走する件

## 背景・問い

vuoi の dev ワークフローで、実装ノード `plan_implement` が `ok=True` を返しながらファイルを
一切変更せず、そのままテストゲート・`/code-review` を通過して終端まで進む回がある。

対象事例: 未来リサーチ 後処理MA補正の実装（trello:k7xziIiV）。2026-09-01〜09-02 に同一カードが
3 回処理され、そのうち 2026-09-01 19:39 の回は code_review 時点でブランチが main と同一・
作業ツリーがクリーンだった。

（カード本文は「4 回処理されている」としているが、残っているログ・reflog・カードのコメントは
いずれも 3 回で一致する。ログの最古の行が 2026-09-01 17:39 なので、それ以前の回があった可能性は
否定できないが、裏づけとなる記録は無い。以下は 3 回を前提に書く。）

問い:

1. 実装ノードの前後で差分の有無を決定的に確認し、差分ゼロなら blocked にして人間へ返すべきか
2. それとも実装ノードのプロンプト・入力側に問題があるのか

## 調査方法（見たもの・手順・前提）

前提: 本調査ではリポジトリのソースを変更していない。成果物はすべて本フォルダ配下に置いた。

1. `~/.local/state/vuoi/vuoi.log` から k7xziIiV の実行を時系列に復元し（残っていたのは 3 回）、
   各ノードの実行時刻・セッション ID・コストを対応づけた。
2. 各回で実際に成果が残ったかを、ログではなく git で確認した
   （`git reflog show chevuoi/trello-k7xziIiV --date=iso`、`git log main..HEAD`、`git worktree list`）。
   カードのタイトルが併記していた AF カード（ログから YDalIiKj / pGzcfDHQ と特定）についても
   同じ確認を行った。
3. 19:39 の回のトランスクリプト 2 本を読んだ（`dump_transcript.py` で整形、抜粋を `t_8ba83e1b.txt` に保存）。
   - `8ba83e1b`（plan_implement → apply_review → summarize のメインセッション）
   - `68d661e6`（`/code-review high` の新規セッション）
4. 現行コードを読み、終端処理の分岐を確認した
   （`process_card_usecase.py` の `finalize` / `build_message`、`git_worktree_manager.py` の `has_changes`、
   `gh_pull_request_publisher.py`、`claude_cli_runner.py`、`~/.config/vuoi/workflows/dev/workflow.py`）。
5. chevuoi の git 履歴で、事象の前後に関連する修正が入っていないかを確認した。
6. claude CLI 2.1.252 のバンドルを grep し、実行結果 JSON に `permission_denials` があることを確認した。
7. 対象カード（k7xziIiV）のコメントを読み、各回がどの終端コメント（`🤖 変更なし:` / `🤖 PR:`）で
   終わったかを直接確認した。

## 結果（事実）

### F1. ログに残る 3 回のうち差分ゼロだったのは 19:39 の回だけ

`git reflog show chevuoi/trello-k7xziIiV`（pjt_mirai_research_poc）:

```
6482bb5 @{2026-09-02 02:40:16}: commit: 未来リサーチ 後処理MA補正の実装
8f8199f @{2026-09-02 00:55:56}: commit: 未来リサーチ 後処理MA補正の実装
6e7f2a0 @{2026-09-01 19:39:03}: branch: Created from HEAD
```

19:39 の回はブランチ作成のみでコミットが無い。00:17 と 02:23 の回はコミットが積まれ、
02:23 は既存 PR #147 を再利用している（`vuoi.log` 02:40:19 の「既存 PR を再利用」）。
reflog のエントリ数（作成 1 + コミット 2）も、カードに残る自動処理コメント 3 件
（`🤖 変更なし:` 1 件・`🤖 PR:` 2 件）も、実行 3 回と整合する。

なお 02:23 の回は、カードに付いた人間のコメント「PRコードレビューに対応して」
（2026-09-02 01:59）を受けた再投入であり、システムが同じ処理を自動で繰り返したものではない。

### F2. 19:39 の回は「変更なし」の終端に落ちていた

ホストの `finalize` は差分が無ければ PR を作らず `🤖 変更なし:` のコメントを残す
（`process_card_usecase.py:187-188`、判定は `git_worktree_manager.py:85-105`）。
19:39 の回がこの経路で終わったことは、カードに実際に残っているコメントで直接確認できる
（2026-09-01 19:54 の `🤖 変更なし:` — 本文も「このブランチにコード変更はありません」で始まる）。
F1 のとおりコミットが無いことも、`publish` が `git add -A` とコミットを伴う以上、
PR 作成経路を通っていないことの裏づけになる。

ただし「blocked ではないから In review に移った」わけではない。`process_card_usecase.py:175-177`
はコメントを付けたあと終端状態に関わらず `move_to_review()` を呼ぶので、blocked でも
カードの行き先は In review で変わらない。両者の違いは PR を作らないことと
コメントの見出し（`🤖 blocked:` か `🤖 変更なし:` か）だけである。

### F3. 差分ゼロの原因は、実装ノードが書き込みを一切できなかったこと

トランスクリプト `8ba83e1b` の経過:

- 10:39:07〜10:42:41（UTC）: 仕様・関連コードの読み取りは正常に完了
- 10:42:41 以降: `Write` が「permissions ... you haven't granted it yet」で拒否、
  `Edit` も拒否、`Bash` のリダイレクトも作業ツリー内（`./_probe.txt`、`src/.../_probe.txt`）ですら拒否
- 最終出力: 「ファイルの読み取りは通りましたが、**このセッションでは書き込みが一切できません**でした。
  … つまりコードを 1 行も置けないため、**実装は未着手**です」

それでも `claude` の終了コードは 0・`is_error` は false なので `RunResult.ok=True` になり
（`claude_cli_runner.py:89`）、ワークフローは `plan_implement → ok` の分岐で先へ進んだ。

### F4. その権限問題は事象の 4 時間後に修正済み

chevuoi `8adde6e`（2026-09-01 23:32、PR #29 で 23:50 マージ）が `claude -p` に
`--permission-mode auto` を追加している。コミットのコメントも
「非対話（-p）では承認を求める先が無く、既定のままだと Write / Edit と書き込みを伴う Bash が
その場で拒否される」と、F3 と同じ事象を指している。
修正後の 00:17 の回は実際にコミットを残した（F1）。

### F5. 差分ゼロ判明後に $4.09 とテスト 2 回が無駄になった

| 時刻 | ノード | コスト |
|---|---|---|
| 19:45:24 | plan_implement（全書き込み拒否） | $4.405 |
| 19:51:48 | code_review | $1.703 |
| 19:53:59 | apply_review | $2.043 |
| 19:54:54 | summarize | $0.348 |

合計 $8.50。うち plan_implement より後の 3 ノード分 $4.09、およびテストゲート 2 回・
実装ノード終了から終端までの約 9 分は成果に結びついていない。

### F6. 差分ゼロの回の `/code-review` は無関係な差分をレビューしていた

`68d661e6` の scope note:

> the current branch `chevuoi/trello-k7xziIiV` has zero commits ahead of `main` and a clean working tree,
> so `git diff main...HEAD` and `git diff HEAD` are both empty. I fell back to `git diff HEAD~1`,
> which is the most recently merged PR (#144, "未来リサーチ MA事後補正設計ブラッシュアップ")

その指摘が apply_review（$2.043）へそのまま渡っている。
なお本件は姉妹カード trello:oMHbOMFZ で別途処理され、本調査中に
`~/.config/vuoi/workflows/dev/workflow.py` へ `diff_gate` ノードが追加されたことを確認した
（`test_gate` の後・`code_review` の前に置かれ、差分が無ければレビューを飛ばして summarize へ抜ける）。

### F7. カードが指す仕様書は main に存在しなかった

カード本文の `docs/specs/ma-count-calibration.md` は main にも作業ブランチにも無く、
未マージのコミット `6ac5634` にのみ存在する。実装ノードはこれを検知して `vuoi-proposal` で申告している。
一方で 00:17 の回は同じ状態のまま設計ドキュメントを根拠に実装を完了させている。

### F8. AF カードは差分ゼロではない

`AF アップローダー エラーUI表示の削除機能`（YDalIiKj）と `AF アップローダーデータ削除ボタンが押せない`
（pGzcfDHQ）は 2026-09-02 17:21 の回でコミットを残している
（reflog: 17:34:22 / 17:38:34）。ログに PR 行が出ないのは、
`GhPullRequestPublisher` が既存 PR の再利用時しかログを出さず、新規作成時は無言だからである
（`gh_pull_request_publisher.py:30, 32-36`）。

### F9. 「変更なし」で終わった回は次回の入力に反映されない

`_previous_outcome`（`process_card_usecase.py:64-73`）は `🤖 PR:` と `🤖 完了:` だけを見る。
`🤖 変更なし:` は該当しないため、再実行時のプロンプトに前回の空振りは伝わらない。
今回の 3 回では 00:17 の回が実装を完了させたので実害は出ていない（同じ空振りの反復は観測していない）。
以降で同じ環境不備が続いた場合に効いてくる、という位置づけの欠陥である。

## 考察と結論

**「差分ゼロ」は症状であって原因ではない。** 原因は性質の異なる 2 つに分かれ、あるべき終端も異なる。

| 原因 | 判別方法 | あるべき終端 |
|---|---|---|
| A. ノードが作業**できなかった**（ツール拒否・環境不備） | 実行結果 JSON の `permission_denials` が非空 | blocked（人間へ返す） |
| B. ノードが作業した結果として**変更不要と判断した** | 差分ゼロ かつ denials なし | 変更なし（既存の終端でよい） |

現状はどちらも B の経路（`🤖 変更なし`）に落ちるため、A が B と同じ「正常終了」として扱われる。
ここで失われているのは情報そのものではなく、機械可読な終端状態である。19:39 の回の
`🤖 変更なし:` コメントは本文で「環境の書き込みブロックにより着手できません」と明示しており、
カードを読んだ人間には伝わる。伝わらないのはワークフローとホストで、`blocked` は立たず、
`_previous_outcome`（F9）も拾わず、カードの行き先も blocked と同じ In review（F2）になる。
19:39 の回は A であり（F3）、その直接原因は権限設定で、実装ノードのプロンプトでも入力でもなかった。
入力側の欠陥（F7）は実在するが、設計どおり `vuoi-proposal` で申告されており、
かつ同じ入力で 00:17 の回は実装を完了しているので、差分ゼロの原因ではない。

したがって問い 1 への答えは「差分ゼロを一律 blocked にするのは行き過ぎ」。
dev の終端は元々「変更なし」を正当な結果として持ち（`finalize` の分岐、summarize プロンプトの
「変更が不要だった場合はその理由」）、それを潰すと「確認したら既に直っていた」類のカードが
すべて人間に差し戻されるようになる。分けるべきは A と B であって、差分の有無だけでは分けられない。

問い 2 への答えは「今回に限れば実装ノードのプロンプト・入力の問題ではない」。
ただし F4 の修正は今回の原因（既定の permission mode）を潰しただけで、
deny ルール・フック・サンドボックスなど別経路の拒否が起きれば同じ空回りが再発する。
`ok` が終了コードしか見ていない以上（F3）、検出手段は権限設定とは独立に必要である。

ここで上表の「判別方法」には限界がある。CLI 2.1.252 のバンドル内の説明は、
`canUseTool` に届く前に決着する拒否 — PreToolUse フックの deny、フックの allow/ask を
上書きする deny ルール、および MCP の `--permission-prompt-tool` 経由 — はこの記録の対象外だと
述べている。19:39 の回でも、`Write` / `Edit` の拒否は承認待ちが終端化したものなので記録される見込みだが、
`Bash` の拒否は「許可された作業ディレクトリにしか書けません」というツール結果のエラーで、
権限拒否として記録されるかは確認できていない。したがって `permission_denials` は
A を検出する有力な手掛かりではあるが、A の網羅的な検出器ではない。
実運用に入れる前に、拒否経路ごとに実際の結果 JSON を 1 回ずつ取って確かめる必要がある。

コスト面では、F5 と F6 のとおり「何も達成していない」ことが判明した後の処理が最も高くつく。
F6 の `diff_gate` でレビュー以降の浪費は塞がったが、位置が `test_gate` の後なので
テストスイート 1 回分は依然として走る。

## 参考（ファイル・URL・ログの場所）

- ログ: `~/.local/state/vuoi/vuoi.log`（19:38:55〜19:54:57 が該当の回）
- トランスクリプト: `~/.claude/projects/-home-ubuntu-worktrees-vuoi-chevuoi-trello-k7xziIiV/`
  - `8ba83e1b-6cce-4afa-964a-816ca45da4d5.jsonl`（実装〜要約のメインセッション）
  - `68d661e6-eb95-4937-a276-df24ef7604b8.jsonl`（`/code-review high`、scope note）
  - 抜粋: 本フォルダの `t_8ba83e1b.txt`（生成スクリプト `dump_transcript.py`）
- ワークフロー: `~/.config/vuoi/workflows/dev/workflow.py`（`plan_implement` / `test_gate` / `diff_gate` / `code_review`）
- ホスト側の終端処理: `src/chevuoi/application/usecases/process_card_usecase.py:64-73, 179-195`
- 差分判定: `src/chevuoi/infrastructure/git/git_worktree_manager.py:85-105`
- PR 発行: `src/chevuoi/infrastructure/git/gh_pull_request_publisher.py:17-36`
- 実行結果のパース: `src/chevuoi/infrastructure/workflows/claude_cli_runner.py:81-95`、
  `src/vuoi_sdk/__init__.py:35-41`（`RunResult`）
- 権限修正: chevuoi `8adde6e` / PR #29
- 対象カード: <https://trello.com/c/k7xziIiV>、PR: <https://github.com/laboroai/pjt_mirai_research_poc/pull/147>
- 姉妹カード（`/code-review` の HEAD~1 フォールバック）: trello:oMHbOMFZ
- `permission_denials` の存在確認: `/home/ubuntu/.local/share/claude/versions/2.1.252`（CLI 2.1.252 バンドル）を grep。
  結果メッセージのスキーマに `permission_denials` があり、「result.permission_denials is the
  authoritative record」という記述がある。同じ説明文が「Denials that resolve before canUseTool runs
  — PreToolUse hook denies, and deny-rule overrides of hook allow/ask decisions — are not covered here,
  and neither is the MCP --permission-prompt-tool surface」と限界も述べている
- 終端コメントの実物: 対象カード k7xziIiV のコメント 4 件（`🤖 変更なし:` 1 / `🤖 PR:` 2 / 人間 1）

## 次のアクション案

1. **`RunResult` に `permission_denials` を持たせ、非空なら blocked にする**（原因 A の検出）
   `claude_cli_runner.py:_parse` が `RunResult` に写しているのは `result` / `session_id` /
   `total_cost_usd` だけ（`is_error` は `ok` の判定にしか使っていない）。
   拒否されたツール名を blocked の理由に載せれば、19:39 の回は最初のノードで止まり、
   $4.09 とテスト 2 回を使わずに「書き込み権限がない」と人間へ返せた。
   権限設定（F4）とは独立に必要な、再発防止の本命。ただし考察のとおり全経路を拾える保証は無いので、
   まず拒否経路ごとに結果 JSON を実測して記録範囲を確かめること。
   また blocked にしてもカードの行き先は In review のまま（F2）なので、
   人間に「これは差し戻しだ」と伝わるのはコメント見出しだけである点は変わらない。
2. **差分チェックを `plan_implement` の直後にも置く**（原因 B の最短終了）
   既に入った `diff_gate` は `test_gate` の後なので、テストスイート 1 回分は無駄に走る。
   実装ノード直後に同じ判定を置き、差分ゼロなら test_gate と review を飛ばして summarize へ。
   ここでは blocked にしない（「変更不要」は dev の正当な終端）。
   原因を問わず差分ゼロを確実に捉えられるのはこちらなので、1 が拾えない拒否経路があっても
   浪費だけは止まる。1 は「止める」ためではなく「理由を付ける」ために要る、という関係になる。
3. **`_previous_outcome` に「変更なし」を認識させる**（F9）
   `🤖 変更なし:` で終わった回を再実行時のプロンプトに反映する。
   今回の 3 回では実害は出ていない（F9）ので、1・2 より優先度は低い。
4. **PR 新規作成もログに残す**（F8、`vuoi-proposal` として申告済み）
   終端種別がログ単体で追えるようになり、今回のような調査で git reflog との突き合わせが不要になる。
5. カード入力側（F7）は追加の仕組み不要。現行の `vuoi-proposal` 申告で意図どおり機能している。
