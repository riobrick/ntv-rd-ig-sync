#!/usr/bin/env python3
"""Generate archive-style rewrites for past posts via Claude (batch).

Reads preview/posts_dump.json, writes preview/archive_rewrites.json:
  {tweet_id: {"needs_rewrite": bool, "rewritten": str|None}}
"""
import json
import os
import re
import subprocess
import time

CUTOFF = "2026-07-19T15:00:00"  # posts before 2026-07-20 JST are archive
CLAUDE_OAUTH = re.sub(r"\s+", "", os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", ""))
BATCH = 15


def claude_code(prompt):
    for attempt in range(3):
        r = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=600,
            env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": CLAUDE_OAUTH})
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return out
        print(f"claude attempt {attempt+1} failed: {(r.stderr or out)[:200]}")
        time.sleep(15)
    raise RuntimeError("claude cli failed")


def clean(text):
    t = re.sub(r"https?://t\.co/\S+", "", text)
    return re.sub(r"[ \t]+\n", "\n", t).strip()


posts = json.load(open("preview/posts_dump.json"))
cand = [p for p in posts if p["media"] and p["created_at"] < CUTOFF]
cand.sort(key=lambda p: p["created_at"])
print(f"candidates: {len(cand)}")

results = {}
if os.path.exists("preview/archive_rewrites.json"):
    results = json.load(open("preview/archive_rewrites.json"))

todo = [p for p in cand if p["id"] not in results]
for i in range(0, len(todo), BATCH):
    batch = todo[i:i + BATCH]
    items = [{"id": p["id"], "ym": p["created_at"][:7],
              "text": clean(p["text"])} for p in batch]
    prompt = f"""以下は日本テレビR&Dラボ(@ntv_rd)のXの過去投稿です。Instagramに「過去のアーカイブ」として転載するため、各投稿の本文を確認してください。

ルール:
- 「開催中」「〇/〇まで」「本日」「明日」「まもなく」「今週末」など、投稿時点に依存する表現があれば、過去のアーカイブとして自然な表現に書き換える(例: 「馬喰町のparcel/CON_にて7/26まで！」→「馬喰町のparcel/CON_にて開催されました(2026年7月)」)。ymが投稿年月なので時期の補足に使う
- 内容の紹介部分(作品・技術の説明)はできるだけ原文のまま残す
- @ユーザー名、作品名、ハッシュタグは一切変更しない
- 時点依存の表現がない投稿は書き換え不要(needs_rewrite: false)

入力(JSON):
{json.dumps(items, ensure_ascii=False)}

出力はJSON配列のみ(コードブロック不要):
[{{"id": "...", "needs_rewrite": true/false, "rewritten": "書き換え後の本文(不要ならnull)"}}]"""
    txt = claude_code(prompt).strip()
    if txt.startswith("```"):
        txt = txt.strip("`").lstrip("json").strip()
    try:
        arr = json.loads(txt)
        for it in arr:
            results[it["id"]] = {"needs_rewrite": bool(it.get("needs_rewrite")),
                                 "rewritten": it.get("rewritten")}
    except Exception as e:
        print(f"batch {i//BATCH} parse failed: {e}")
        for p in batch:
            results[p["id"]] = {"needs_rewrite": None, "rewritten": None}
    with open("preview/archive_rewrites.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"batch {i//BATCH + 1}/{(len(todo)+BATCH-1)//BATCH} done "
          f"({len(results)}/{len(cand)})")

print("all done")
