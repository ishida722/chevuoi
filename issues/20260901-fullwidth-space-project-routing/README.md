# vuoi 特定のカードがプロジェクト確定できない現象の調査

Status: OPEN

## What

カード https://trello.com/c/eLK2oo22 （タイトル「未来リサーチ　後処理MAのデザインドックとADR」）のプロジェクトルーティングが失敗する原因を調査する。仮説: タイトルに全角スペースが入っているため。

## Why

プロジェクトが確定できないカードは NullProject 扱いとなり自動処理から外れる（In review へコメント付き移動）。原因を特定し、修正が必要なら起票する。

## Tasks

- [x] 対象カードのタイトルを Trello API で取得し、文字を確認する
- [x] ProjectTag.from_title のタグ抽出ロジックを確認する
- [x] 再現確認（Python で partition の挙動を検証）
- [x] 修正の申告（vuoi-proposal ブロック。起票はホストが実施）

## メモ

- 原因確定: タイトル区切りが全角スペース（U+3000）のため `ProjectTag.from_title` の `partition(" ")` が分割できず NullProject になる。
- 修正案は `split(maxsplit=1)`（Unicode 空白対応）。bug として vuoi-proposal で申告済み（起票カードの作成はホストがラン後に行う）。
- 詳細は [report.md](report.md) を参照。

## 調査結果

### 結論: 仮説どおり、タイトルの区切りが全角スペース（U+3000）であることが原因

- 対象カードの実タイトルは `未来リサーチ　後処理MAのデザインドックとADR`。「リサーチ」と「後処理」の間の区切り文字は U+3000（全角スペース）であることを Trello MCP 取得結果から確認した。
- タグ抽出は `src/chevuoi/domain/value_objects/project_tag.py` の `ProjectTag.from_title` で行われるが、`title.partition(" ")` と ASCII 半角スペースのみで分割している。全角スペースでは `sep` が空になり `None` を返す。

  再現:
  ```python
  '未来リサーチ　後処理MA...'.partition(' ')
  # => ('未来リサーチ　後処理MA...', '', '')  → タグ抽出失敗
  ```
- `Card.project_tag`（`domain/entities/card.py:47`）→ `ProcessCardUsecase.resolve_project`（`application/usecases/process_card_usecase.py:142`）で `tag is None` のため `NullProject` が返り、ルーティング失敗となる。

### 修正案

`from_title` で全角スペースも区切りとして扱う。例: `title.split(maxsplit=1)` は U+3000 を含む Unicode 空白で分割するため、最小修正で解決できる（前後の空白 strip も兼ねる）。テストに全角スペース区切りタイトルのケースを追加する。
