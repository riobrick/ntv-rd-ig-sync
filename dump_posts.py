#!/usr/bin/env python3
"""Dump original posts (no replies/reposts) with text and media types to JSON."""
import json
import os
import urllib.parse
import urllib.request

BEARER = os.environ["X_BEARER_TOKEN"]
USERNAME = os.environ.get("X_USERNAME", "ntv_rd")
START = os.environ.get("START_TIME", "2025-07-20T15:00:00Z")


def x_api(path, params):
    url = f"https://api.x.com/2/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BEARER}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


user = x_api(f"users/by/username/{USERNAME}", {})["data"]
out, token = [], None
while True:
    params = {
        "max_results": 100, "exclude": "replies,retweets",
        "start_time": START,
        "tweet.fields": "created_at",
        "expansions": "attachments.media_keys",
        "media.fields": "type,media_key",
    }
    if token:
        params["pagination_token"] = token
    page = x_api(f"users/{user['id']}/tweets", params)
    media_map = {m["media_key"]: m["type"]
                 for m in page.get("includes", {}).get("media", [])}
    for tw in page.get("data", []):
        keys = tw.get("attachments", {}).get("media_keys", [])
        types = sorted({media_map.get(k, "?") for k in keys})
        out.append({"id": tw["id"], "created_at": tw["created_at"],
                    "media": types, "text": tw["text"]})
    token = page.get("meta", {}).get("next_token")
    if not token:
        break

os.makedirs("preview", exist_ok=True)
with open("preview/posts_dump.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print(f"dumped {len(out)} posts")
