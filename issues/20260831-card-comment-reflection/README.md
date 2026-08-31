# vuoi カードコメントをちゃんと反映できているかを調査

Status: OPEN
Trello: https://trello.com/c/dHOxV9N1

## What

カードを In review から差し戻し、追加指示をカードコメントに書いても、再処理でその指示が反映されない（ファイル修正なし・コミットなし・そのまま In review へ戻る）事象の原因調査。

## Why

差し戻しによる再処理は人間のレビューを反映する唯一の経路であり、これが機能しないとレビューコメントが恒常的に無視される。

## Tasks

- [x] chevuoi のカード処理経路（取得 → プロンプト生成 → 実行 → 終端処理）を読む
- [x] コメントが LLM に届いているかを確認する
- [x] 「変更なし・コミットなしで In review」になる経路を特定する
- [x] 差し戻された元コメントの GAS 調査依頼（テレ東 閲覧シート GAS）の内容をレポートに追記する
- [x] 修正カードを起票する

## メモ

- 原因確定: 差し戻しコメントは取得すらされておらず（provider が desc のみ取得、build_message も本文のみ）、既存 worktree 再利用と合わさり「変更なし → In review」が必然的に発生する。
- 修正カードを Inbox に起票済み: https://trello.com/c/Xdwal6AI。詳細レポートは report.md。

## 調査結果

### 結論: カードコメントは一切 LLM に渡っていない（仕様漏れ）

再処理時に観測された挙動（ファイル修正なし・コミットなし・In review 行き）は、以下の 3 点の合わせ技で必然的に発生する。

1. **コメントを取得していない。**
   `TrelloCardProvider.fetch_ready_cards`（`src/chevuoi/infrastructure/trello/trello_card_provider.py:21`）は
   `fields=name,desc,url,shortLink,idList` しか取らず、`/cards/{id}/actions?filter=commentCard` を呼ぶコードは
   リポジトリ内に存在しない。`Card` 抽象（`src/chevuoi/domain/entities/card.py`）にもコメント読み取りの
   メソッドがない（`add_comment` は書き込み専用）。

2. **プロンプトは本文のみ。**
   `ProcessCardUsecase.build_message`（`src/chevuoi/application/usecases/process_card_usecase.py:139`）は
   `タイトル + URL + 本文(desc)` だけを組み立てる。差し戻しコメントに書いた追加指示は
   ワークフローに届かず、LLM は初回とまったく同じタスク文を受け取る。

3. **worktree が再利用され「作業済み」に見える。**
   `GitWorktreeManager.create`（`src/chevuoi/infrastructure/git/git_worktree_manager.py:34`）は
   既存パスがあればそのまま返す。再処理では前回の成果（コミット済み・PR 済み）が入った
   クリーンな worktree 上で、初回と同じ指示が再実行される。ワークフローは「もう出来ている」と
   判断して何もせず、`has_changes`（同 `:86`、`git status --porcelain` = 未コミット差分のみ）が
   False になり、`finalize`（`process_card_usecase.py:129`）が `🤖 変更なし:` コメントを返し、
   `execute` がそれをカードに付けて `move_to_review` する（同 `:117-118`）。

つまり「コメントの指示に従っていない」のではなく、**指示がそもそもシステムに入力されていない**。
仕様側（`docs/spec/`）にも人間の差し戻しコメントを再処理へ反映する定義がない
（`~/.claude/skills/cycle/SKILL.md` の手順5「差し戻し」には相当する運用が定義されているが、
chevuoi 本体には未実装）。

### 対応策（起票済み）

`vuoi コメント反映` カードとして Inbox に起票。骨子:

- `Card` にコメント読み取り（例: `comments() -> list[CardComment]`）を追加し、
  `TrelloCard` で `/cards/{id}/actions?filter=commentCard` を実装する。
- `build_message` で、`🤖` 以外（人間）のコメントを新しい順に「レビューコメント」節として
  プロンプトへ追加する。過去に `🤖 PR:` / `🤖 完了:` コメントがあれば
  「これは差し戻しである。前回成果を前提にコメントの指示へ対応せよ」という文脈も付ける。
- 差し戻し検出時は worktree 再利用を前提に「前回ブランチ上で追加コミットする」ことを
  ワークフローに明示する（現在の再利用挙動自体は差し戻しには都合が良い）。

### 追記: 元コメントの GAS 調査依頼への回答（テレ東 閲覧シート GAS）

差し戻しコメントが指す GAS は `pjt_tv_tokyo_program_lineup_parser/gas/unified_tool.gs`
（v1.4.0、閲覧用シートに導入する統合ツール。同 `gas/README.md` が single source of truth）。

**期待するシート名:**
- コードにシート名のハードコードはない。DB 側のシート名は「⚙️ 接続設定」
  （`configureConnection`）でユーザーが入力し、スクリプトプロパティ
  `DB_SPREADSHEET_ID` / `DB_SHEET_NAME` に保存される（プロンプト例は「番組情報」）。
- 閲覧側は常に `SpreadsheetApp.getActiveSheet()`（unified_tool.gs:179 ほか）を使うため、
  閲覧用シートのシート名は任意（開いているシートが対象になる）。
- 期待する**列名**は `CONFIG` で定義: `エピソードID` / `行ID` / `作成日時` / `最新フラグ` /
  `放送休止フラグ`（無い番組ではグレー表示をスキップ）。差分ハイライト除外は `内容`。

**実行時にシートが作られるか:**
- 作られない。`insertSheet` の呼び出しは存在しない。DB 側に指定シートが無ければ
  `fetchDataFromDB` / `updateToDB` は「DBファイルにシート「…」が見つかりません」で
  エラーになる（unified_tool.gs:168）。閲覧側もアクティブシートへの上書きのみ。

**推定されるクライアントシート構成:**
- スプレッドシートは 2 系統。①「DB スプレッドシート」: 抽出 CLI の出力先で実行のたびに
  上書きされる。番組データを 1 シート（例: 番組情報）に保持。GAS は導入しない。
  ②「閲覧用シート」: ユーザーごとに作成する別スプレッドシートで、GAS はこちらに
  コンテナバインドで導入（配布は手動コピー、推奨はテンプレート複製）。
- 閲覧用シートは通常 1 シート構成（アクティブシート運用のため）で、DB から
  `fetchDataFromDB` で全列コピーし、列幅・フィルタ等の表示設定だけをローカルに保持。
  行は エピソードID × 作成日時 の履歴持ちで、`最新フラグ` により
  「最新（薄緑）+ 1 つ前」を既定表示にする。編集は `updateToDB` で差分承認のうえ
  `行ID`（UUID）キーで DB に書き戻す（両シートに `行ID` 列が無い場合のみ
  `エピソードID` で突き合わせる。unified_tool.gs:251-252）。

なお、この GAS 調査依頼が元々書かれたカード（テレ東案件の差し戻しカード）自体への反映は、
上記の通りコメント未反映バグにより行われていない。本レポートが代替の回答となる。
