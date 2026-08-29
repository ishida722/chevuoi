# Che vuoi? 仕様書

タスク管理ツールのチケットを起点に、`claude -p` を実行ノードとするパイプラインを回し、成果物（PR / 調査メモ / ADR）を生成してチケットを更新する常駐バッチの仕様書です。

機能仕様の各章は「何をするか」を定めるものであり、設計・内部実装には立ち入りません。実装の設計は「設計」の各章で扱います。

```{toctree}
:maxdepth: 2
:caption: 機能仕様

spec/overview
spec/mvp
spec/workflow
spec/task-sources
spec/triage
spec/routes
spec/gate-review
spec/outcomes
spec/proposals
spec/worktree
spec/cli
spec/workflow-engine
```

```{toctree}
:maxdepth: 2
:caption: 設計

design/mvp
design/workflow-engine
design/execution-log
```
