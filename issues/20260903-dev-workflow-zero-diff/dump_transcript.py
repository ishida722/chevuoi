"""トランスクリプト(jsonl)から、ユーザープロンプト・ツール使用・アシスタント発話を時系列で吐く。"""
import json, sys

path = sys.argv[1]
mode = sys.argv[2] if len(sys.argv) > 2 else "tools"
for line in open(path):
    try:
        d = json.loads(line)
    except Exception:
        continue
    t = d.get("type")
    ts = d.get("timestamp", "")
    msg = d.get("message") or {}
    content = msg.get("content")
    if t == "user":
        if isinstance(content, str):
            print(f"--- USER {ts}\n{content[:3000]}")
        elif isinstance(content, list):
            for c in content:
                if c.get("type") == "tool_result" and mode == "all":
                    r = c.get("content")
                    r = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)[:800]
                    print(f"  <- result {r[:800]}")
                elif c.get("type") == "text":
                    print(f"--- USER {ts}\n{c['text'][:3000]}")
    elif t == "assistant" and isinstance(content, list):
        for c in content:
            if c.get("type") == "text" and c["text"].strip():
                print(f"=== ASSISTANT {ts}\n{c['text'][:4000]}")
            elif c.get("type") == "tool_use":
                inp = c.get("input", {})
                brief = {k: (str(v)[:200]) for k, v in inp.items() if k in
                         ("file_path", "command", "pattern", "path", "old_string", "prompt", "skill", "notebook_path")}
                print(f"  -> {c.get('name')} {json.dumps(brief, ensure_ascii=False)[:400]}")
