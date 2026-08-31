# Che vuoi?

タスク管理ツールのチケットを起点に、`claude -p` を実行ノードとするパイプラインを回し、成果物（PR / 調査メモ / ADR）を生成してチケットを更新する常駐バッチです。

## 命名

- 正式名称は **Che vuoi?**
- パッケージ／リポジトリ名は `chevuoi`
- CLI コマンドは `vuoi`

上位の定期実行層（複数ループを束ねる側）は別ソフトの `borro` であり、本リポジトリの対象外です。`borro` が `vuoi run` を周期的に起動する関係にあります。

## やること / やらないこと

やること:

- 複数のタスクソース（初期は Trello のみ）からのチケット取得
- チケット種別に応じた経路（implement / investigate / design / trivial）の選択
- git worktree による作業隔離
- `claude -p` をノードとするパイプライン実行
- 決定的ゲート（テスト・lint・型チェック）による合否判定
- レビューループ（収束保証つき）
- 終端状態に応じたチケット更新
- 作業中に発見した副次タスクの起票

やらないこと:

- ノードの並列実行（1ラン内は逐次）
- PR の自動マージ（PR 作成で必ず人間ゲートを挟む）
- 自動起票カードの自動実行（Inbox 止まり）
- ワークフローフレームワーク（LangGraph / Temporal 等）の導入
- Web UI

## 基本原則

1. **フロー制御に LLM を使わない。** 「次に何をするか」「合格か不合格か」は必ず決定的なルールで判断します。LLM は所見・成果物・分類候補を出力するだけです。唯一の例外は入口の triage 1回で、それも候補を1つ返すだけです。
2. **すべての実行は明示的な終端状態で終わる。** 「なんとなく止まった」状態を作りません。

## 動作の概要

`vuoi run` を1回実行すると、各タスクソースを次の流れで1巡します。

```
チケット取得 → クレーム（占有） → プロジェクト特定 → triage（経路選択）
  → 経路の実行（worktree 準備 → 計画 → 実装 ⇄ ゲート ⇄ レビュー）
  → 後処理（副次タスク起票・チケット更新・worktree 後始末）
```

詳細は `docs/spec/workflow.md` を参照してください。

## セットアップ

Python 3.12 以上と [uv](https://docs.astral.sh/uv/) が必要です。

```bash
uv sync
```

設定は TOML ファイルで行います。既定のパスは `~/.config/vuoi/config.toml` で、`--config` オプションで変更できます。Trello の認証情報は、設定ファイルに無ければ環境変数 `TRELLO_KEY` / `TRELLO_TOKEN` から読み込みます。ユーザー定義ワークフローの既定の置き場所は `$XDG_CONFIG_HOME/vuoi/workflows`（未設定時は `~/.config/vuoi/workflows`）です。

## 使い方

```bash
vuoi run                      # 全ソースをポーリングして1巡
vuoi run --source trello --limit 1
vuoi resume <run_id>          # 中断ランの再開
vuoi status                   # 未終端ランの一覧
vuoi gc --older-than 7d       # 終端済み worktree の掃除
vuoi workflow list            # ユーザー定義ワークフローの一覧
vuoi workflow run <name> ["メッセージ"]   # ワークフローを名指しで1回実行
vuoi workflow select <title> ["本文"]    # カード内容からワークフローを選ぶ
```

各コマンドの挙動と常駐方法（systemd timer + oneshot service）は `docs/spec/cli.md` を参照してください。

## ユーザー定義ワークフロー（vuoi_sdk）

`vuoi_sdk` は、ユーザーのワークフローが import する唯一の公開インターフェースです。ホスト本体（`chevuoi`）には依存しません。詳細は `docs/spec/workflow-engine.md` と `docs/design/workflow-engine.md` を参照してください。

## ドキュメント

`docs/` は Sphinx（MyST）による仕様書です。機能仕様は `docs/spec/`、設計は `docs/design/` にあります。ルートの `chevuoi-spec.md` は一枚ものの仕様書で、不変条件（Invariants）を含みます。

次のコマンドでビルドします。

```bash
uv run sphinx-build docs docs/_build/html
```

## 開発

src レイアウトで、`src/chevuoi`（本体）と `src/vuoi_sdk`（ワークフロー SDK）の2モジュール構成です。本体はクリーンアーキテクチャ（domain / application / infrastructure / interfaces）で分割しています。

テストは次のコマンドで実行します。

```bash
uv run pytest
```
