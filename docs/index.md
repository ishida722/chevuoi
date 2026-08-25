# Che vuoi? 仕様書

タスク管理ツールのチケットを起点に、`claude -p` を実行ノードとするパイプラインを回し、成果物（PR / 調査メモ / ADR）を生成してチケットを更新する常駐バッチの仕様書です。

本ドキュメントは「何をするか」を定める機能仕様であり、設計・内部実装には立ち入りません。

```{toctree}
:maxdepth: 2
:caption: 機能仕様

spec/overview
spec/workflow
spec/task-sources
spec/triage
spec/routes
spec/gate-review
spec/outcomes
spec/proposals
spec/worktree
spec/cli
```
