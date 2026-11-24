# 対話中の Claude から vuoi のワークフローを呼び出す — 調査と方針

Status: OPEN

entwurf: ループアプリを修正して、もうちょっと手軽に使えるようにする方針作成（e6b21f50-df71-4798-801c-2d86b0fec9e2）

## What

手元で Claude と対話して方針を決めたあと、vuoi のワークフロー（dev など）を対話の中から起動し、方針をコンテクストとして渡し、結果を受け取れるようにする。そのための方針をまとめ、最小構成（`vuoi exec`）を実装する。

## Why

vuoi のワークフローは Trello のカードを起点に、取得から完了まで一気通貫で流れる。リモートのマシンで動いているこの運用は続けるが、手元での対話的な開発ではワークフローを使えず、もったいない。

## Tasks

- [x] カード処理（`ProcessCardUsecase`）と単発実行（`vuoi workflow run`）の構造を確認する
- [x] ワークフローの入力と出力（SDK の `WorkflowContext` / `ExecutionResult`）を確認する
- [x] 対話から呼ぶときに足りないものを洗い出す
- [x] 呼び出し口を MCP にするか CLI とスキルにするかを判断し、方針をまとめる（[report.md](report.md)）
- [x] `vuoi exec` を実装する
- [ ] Claude Code 側のスキル（仮称 `vuoi-handoff`）を作る
- [x] MCP サーバー（`vuoi mcp`、start / status 方式）を作る
- [ ] `vuoi-handoff` スキルを MCP のツールを使う手順に書き換える

## メモ

- ワークフロー本体はすでにカードから独立している。固定されているのは `ProcessCardUsecase` で、「作業場所 → 実行 → 終端処理」がカードの入口と出口に挟まれて切り出せない状態になっている。
- 方針（2026-11-19 改訂）: worktree と PR の機能は入れず、今の作業ツリーでワークフローを起動して結果を受け取るだけの最小構成にする。新しいコマンド `vuoi exec` を作り、プロジェクト情報（テストコマンド）・変更の有無の判定・`--brief`・`--json` を持たせる。既存の `vuoi workflow run` はそのまま残す。Trello の運用（`ProcessCardUsecase`）には手を入れない。
- ~~MCP は作らない。Claude Code からはスキル経由でバックグラウンド実行し、結果の JSON を読む。~~
- 方針変更（2026-11-24）: MCP サーバー `vuoi mcp` を追加する。Claude Code のサンドボックスの中からは `vuoi exec`（内部で `claude -p` を起動する）を実行できず、スキル経由の Bash 起動が成り立たなかったため。実行が長い問題は start / status の非同期方式で扱う（status は `wait_sec` で終了を待てるので、確認の回数を抑えられる）。
- 詳細は [report.md](report.md) を参照。
