# vuoi 特定のカードがプロジェクト確定できない現象の調査

## 背景・問い

カード https://trello.com/c/eLK2oo22 （「未来リサーチ　後処理MAのデザインドックとADR」）がプロジェクトルーティングに失敗し、自動処理から外れる。タイトルに全角スペースが入っていることが原因ではないか、という仮説を検証する。修正が必要なら起票する。

## 調査方法（見たもの・手順・前提）

1. Trello MCP（trelloReadCard）で対象カードを取得し、タイトルの実際の文字を確認した。
2. タグ抽出とルーティングのコードを読んだ:
   - `src/chevuoi/domain/value_objects/project_tag.py`（`ProjectTag.from_title`）
   - `src/chevuoi/domain/entities/card.py`（`Card.project_tag`）
   - `src/chevuoi/application/usecases/process_card_usecase.py`（`resolve_project`）
3. Python ワンライナーで、対象タイトルに対する `partition(" ")` の挙動を再現確認した。

前提: ルーティングは「タイトル先頭の1語をプロジェクトタグとし、設定の対応表で引く」決定的ロジックである（LLM 推測なし）。

## 結果（事実）

- 対象カードの実タイトルは `未来リサーチ　後処理MAのデザインドックとADR` で、「リサーチ」と「後処理」の間の区切り文字は U+3000（全角スペース）である。半角スペースは含まれない。
- `ProjectTag.from_title` は `title.partition(" ")`（ASCII 半角スペース）で分割しており、区切りが見つからない場合は `None` を返す（project_tag.py:23-25）。
- 対象タイトルに `partition(" ")` を適用すると `('未来リサーチ　後処理MA...', '', '')` となり分割されない（再現確認済み）。
- `resolve_project` は `card.project_tag` が `None` のとき `NullProject` を返す（process_card_usecase.py:147-149）。
- タグ `未来リサーチ` は運用設定 `~/.config/vuoi/config.toml` の `[projects."未来リサーチ"]` に登録済み。したがって区切りの問題さえ解消すれば対応表の解決は成立する（タグ未登録による NullProject ではない）。

## 考察と結論

仮説どおり。タイトルの区切りが全角スペースのため `from_title` がタグを抽出できず `None` → `NullProject` となり、プロジェクト確定に失敗している。音声入力や日本語 IME ではスペースが全角になることは普通に起こるため、運用でタイトルを直すのではなくコード側で全角スペースを区切りとして受け入れるべき。

修正は `title.split(maxsplit=1)` への変更が最小: Python の `str.split()` は U+3000 を含む Unicode 空白全般で分割し、前後の空白除去も兼ねる。

## 参考（ファイル・URL・ログの場所）

- 対象カード: https://trello.com/c/eLK2oo22
- 調査元カード: https://trello.com/c/TUjB2Laj
- `src/chevuoi/domain/value_objects/project_tag.py:22`
- `src/chevuoi/domain/entities/card.py:47`
- `src/chevuoi/application/usecases/process_card_usecase.py:142-160`

## 次のアクション案

1. `ProjectTag.from_title` を `split(maxsplit=1)` ベースに修正し、全角スペース区切りタイトルのテストを追加する（bug として vuoi-proposal ブロックで申告。カードの起票はホストがラン終了後に行うため、本報告時点で起票カードは存在しない）。
2. 修正リリースまでの暫定運用として、対象カードのタイトル区切りを半角スペースに直せば流せる。
3. 派生検討（任意）: NullProject 時の In review コメントに「タイトル先頭を半角スペースで区切ってください」等の理由を含めると運用者が自己解決しやすい。
