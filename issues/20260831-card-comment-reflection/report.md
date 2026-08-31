# vuoi カードコメント反映の調査レポート

## 背景・問い

vuoi（chevuoi）で処理したカードを In review から差し戻し、追加指示をカードコメントに書いても、指示に従っていないように見える。実例では「GAS フォルダの閲覧シート GAS について、期待するシート名・実行時のシート作成有無・クライアントシート構成の推定をレポートに追記せよ」というコメントを書いて差し戻したが、ファイル修正もコミットもなく In review に戻っていた。

問い:
1. カードコメントは再処理時にワークフロー（LLM）へ渡っているか。
2. 「修正なし・コミットなし・In review 行き」はどの経路で発生するか。
3. （元コメントの依頼）テレ東 閲覧シート GAS の期待シート名・シート作成有無・クライアントシート構成。

## 調査方法（見たもの・手順・前提）

- chevuoi リポジトリのカード処理経路を通読: `TrelloCardProvider` → `ProcessCardUsecase`（claim / build_message / finalize）→ `GitWorktreeManager`。
- `grep` で `comments` / `actions` の取得コードの有無をリポジトリ全体から確認。
- 仕様書 `docs/spec/` と cycle スキル（`~/.claude/skills/cycle/SKILL.md`）で差し戻し処理の定義を確認。
- vuoi 設定 `~/.config/vuoi/config.toml` の projects から GAS フォルダを持つプロジェクトを探索し、`pjt_tv_tokyo_program_lineup_parser/gas/`（`unified_tool.gs` v1.4.0 と `README.md`）を読解。
- 前提: 調査対象カード dHOxV9N1 は Trello REST（trello.sh）で本文・コメントを取得して確認した（コメントは 0 件）。

## 結果（事実）

コメント反映について:

- `TrelloCardProvider.fetch_ready_cards`（`src/chevuoi/infrastructure/trello/trello_card_provider.py:21`）は `fields=name,desc,url,shortLink,idList` のみ取得する。`/cards/{id}/actions?filter=commentCard` を呼ぶコードはリポジトリ内に存在しない。
- `Card` 抽象（`src/chevuoi/domain/entities/card.py`）にコメント読み取りメソッドはない（`add_comment` は書き込み専用）。
- `ProcessCardUsecase.build_message`（`src/chevuoi/application/usecases/process_card_usecase.py:139`）は「タイトル + URL + 本文(desc)」のみを組み立てる。
- `GitWorktreeManager.create`（`src/chevuoi/infrastructure/git/git_worktree_manager.py:34`）は既存パスがあればそのまま返す（前回成果コミット済みの worktree を再利用）。
- `has_changes`（同 `:86`）は `git status --porcelain`（未コミット差分のみ）で判定し、False なら `finalize` が「🤖 変更なし:」コメントを付けて `move_to_review` する。
- 仕様書 `docs/spec/` に、人間の差し戻しコメントを再処理に反映する定義はない。cycle スキル（手順5「差し戻し」）には運用として定義があるが、chevuoi 本体には対応する実装がない。

GAS（`pjt_tv_tokyo_program_lineup_parser/gas/unified_tool.gs`）について:

- シート名のハードコードはない。DB 側シート名は「⚙️ 接続設定」（`configureConnection`）でユーザーが入力し、スクリプトプロパティ `DB_SPREADSHEET_ID` / `DB_SHEET_NAME` に保存される（プロンプト例:「番組情報」）。
- 閲覧側は常に `SpreadsheetApp.getActiveSheet()`（unified_tool.gs:179 ほか）を使う。閲覧用シートのシート名は任意。
- 期待する列名は `CONFIG` 定義: `エピソードID` / `行ID` / `作成日時` / `最新フラグ` / `放送休止フラグ`。差分ハイライト除外列は `内容`。
- `insertSheet` の呼び出しはなく、実行時にシートは作られない。DB 側に指定シートが無ければ `fetchDataFromDB` / `updateToDB` はエラーで停止する（unified_tool.gs:168）。
- `gas/README.md` によれば構成は 2 系統: ① DB スプレッドシート（抽出 CLI の出力先、実行のたび上書き、GAS 導入禁止）、② 閲覧用シート（ユーザーごとに作成、GAS をコンテナバインド導入、表示設定のみローカル保持、`updateToDB` は差分承認のうえ `行ID` キーで書き戻し）。

## 考察と結論

「コメントの指示に従わない」のではなく、**差し戻しコメントがそもそもシステムに入力されていない**。再処理では初回と同一のプロンプトが、前回成果が既にコミットされた worktree 上で再実行されるため、LLM は「作業済み」と判断して何もせず、未コミット差分ゼロ → 「🤖 変更なし」→ In review という観測どおりの挙動が必然的に発生する。これは実装バグというより仕様漏れ（差し戻し再処理の未定義）である。

クライアントシート構成の推定: 閲覧用シートは通常 1 シート運用（アクティブシート前提）で、DB から全列コピーした エピソードID × 作成日時 の履歴行を持ち、`最新フラグ` により「最新（薄緑）+ 1 つ前」を既定表示する。DB 側は「番組情報」等の名前のシート 1 枚に CLI が出力する構成と推定される。

## 参考（ファイル・URL・ログの場所）

- 調査カード: https://trello.com/c/dHOxV9N1
- 起票した修正カード: https://trello.com/c/Xdwal6AI（Inbox）
- `src/chevuoi/infrastructure/trello/trello_card_provider.py:21` — コメント未取得
- `src/chevuoi/application/usecases/process_card_usecase.py:139` — build_message（本文のみ）
- `src/chevuoi/infrastructure/git/git_worktree_manager.py:34,86` — worktree 再利用・has_changes
- `/Users/ishida/projects/pjt_tv_tokyo_program_lineup_parser/gas/unified_tool.gs`、同 `gas/README.md`
- `~/.config/vuoi/config.toml` — projects 対応表・リスト ID
- 詳細メモ: `issues/20260831-card-comment-reflection/README.md`

## 次のアクション案

1. 起票済みカード（https://trello.com/c/Xdwal6AI）で実装: `Card` にコメント読み取りを追加し、`TrelloCard` で `/cards/{id}/actions?filter=commentCard` を実装。`build_message` に人間コメント（`🤖` 以外）を「レビューコメント」節として新しい順に注入する。
2. `🤖 PR:` / `🤖 完了:` コメントの存在で差し戻しを検出し、「前回成果を前提にコメント指示へ対応し、同ブランチに追加コミットせよ」という文脈をプロンプトに付与する。
3. コメント総量はプロンプト上限を考慮して新しい順に切り詰める。仕様書 `docs/spec/` に差し戻し再処理の定義を追記する。
4. 元の GAS 調査依頼が書かれたテレ東の差し戻しカードには本レポートの GAS 節を回答として転記する。
