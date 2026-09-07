#!/usr/bin/env bash
# ルーターの実運用ログを集計する使い捨てスクリプト（調査用・リポジトリ本体からは参照しない）。
# 使い方: bash issues/20260907-task-router-inference/analyze_router_log.sh [ログパス]
set -euo pipefail
LOG="${1:-$HOME/.local/state/vuoi/vuoi.log}"

echo "=== 期間 ==="
head -1 "$LOG" | cut -d, -f1
tail -1 "$LOG" | cut -d, -f1

echo
echo "=== 層別の件数 ==="
printf '第1層(決定的マーカー): %s\n' "$(grep -c 'ルーティング(決定的)' "$LOG" || true)"
printf '第2層(LLM)          : %s\n' "$(grep -c 'ルーティング(LLM)' "$LOG" || true)"

echo
echo "=== 棄権（confidence=low もしくは workflow=None） ==="
grep 'ルーティング(LLM)' "$LOG" | grep -E 'confidence=low|-> None' || echo '(なし)'

echo
echo "=== 選択されたワークフローの分布 ==="
grep -o -- '-> [a-zA-Z_]* (confidence' "$LOG" | sort | uniq -c | sort -rn

echo
echo "=== 理由がカード本文の自己申告を根拠にしている件数 ==="
tot=$(grep -c 'ルーティング(LLM)' "$LOG" || true)
exp=$(grep 'ルーティング(LLM)' "$LOG" | grep -cE '明記|明示|冒頭に「|カード自身が|カード本文に「' || true)
echo "$exp / $tot"

echo
echo "=== 同一タイトルで判定が割れたカード ==="
grep 'ルーティング(LLM)' "$LOG" \
  | sed -E 's/^.*ルーティング\(LLM\): (.*) -> ([a-zA-Z_]+|None) \(confidence.*/\1\t\2/' \
  | sort -u \
  | awk -F'\t' '{c[$1]=c[$1]" "$2}
      END{for(k in c){n=split(c[k],a," "); u=" ";
        for(i=1;i<=n;i++) if(index(u," "a[i]" ")==0) u=u a[i]" ";
        if(split(u,b," ")>1) print k" =>"u}}'

echo
echo "=== claude 実行時のモデル指定 ==="
grep 'claude 実行' "$LOG" | grep -o 'model=[^ ]*' | sort | uniq -c
