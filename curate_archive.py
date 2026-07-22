#!/usr/bin/env python3
"""Curate archive posts against the account's tone & manner via Claude batch.

Writes preview/archive_curation.json: {tweet_id: {"keep": bool, "reason": str}}
"""
import json
import os
import re
import subprocess
import time

CUTOFF = "2026-07-19T15:00:00"
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

results = {}
if os.path.exists("preview/archive_curation.json"):
    loaded = json.load(open("preview/archive_curation.json"))
    results = {k: v for k, v in loaded.items() if v.get("keep") is not None}

todo = [p for p in cand if p["id"] not in results]
print(f"candidates: {len(cand)} / to judge: {len(todo)}")
for i in range(0, len(todo), BATCH):
    batch = todo[i:i + BATCH]
    items = [{"id": p["id"], "ym": p["created_at"][:7],
              "text": clean(p["text"])} for p in batch]
    prompt = f"""あなたはInstagramアカウント「TOKYO PROTOTYPE」(日本テレビR&Dラボ運営)の編集長です。
このアカウントのトンマナは「デザイン・アート・先端技術・展示会情報を紹介する」キュレーションメディアです。
以下のXの過去投稿それぞれについて、このアカウントに転載する価値があるか判定してください。

転載する(keep: true)の基準:
- 作品・インスタレーション・展示・技術デモの紹介として、単体で見て面白い/美しい/驚きがある
- 展示会・イベントのレポートで、内容(作品や技術)が主役のもの
- アーカイブとして後から見ても価値が伝わるもの

転載しない(keep: false)の基準:
- 宣伝・告知・募集・設営報告・カウントダウン・閉幕報告など、お知らせが主目的
- 内輪ネタ、雑談、感想だけで作品・技術の中身が薄いもの
- 時事的な文脈がないと意味が通じないもの
- 品質・トーンがキュレーションメディアとして見劣りするもの

入力(JSON):
{json.dumps(items, ensure_ascii=False)}

出力はJSON配列のみ(コードブロック不要):
[{{"id": "...", "keep": true/false, "reason": "判定理由を20字以内で"}}]"""
    txt = claude_code(prompt).strip()
    if txt.startswith("```"):
        txt = txt.strip("`").lstrip("json").strip()
    try:
        arr = json.loads(txt)
        for it in arr:
            results[it["id"]] = {"keep": bool(it.get("keep")),
                                 "reason": it.get("reason") or ""}
    except Exception as e:
        print(f"batch {i//BATCH} parse failed: {e}")
        for p in batch:
            results[p["id"]] = {"keep": None, "reason": ""}
    with open("preview/archive_curation.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"batch {i//BATCH + 1}/{(len(todo)+BATCH-1)//BATCH} done")

print("curation done")
