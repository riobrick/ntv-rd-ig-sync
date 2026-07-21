#!/usr/bin/env python3
"""Count original posts (no replies/reposts) for X_USERNAME since START_TIME."""
import json
import os
import urllib.parse
import urllib.request
from collections import Counter

BEARER = os.environ["X_BEARER_TOKEN"]
USERNAME = os.environ.get("X_USERNAME", "ntv_rd")
START = os.environ.get("START_TIME", "2025-07-20T15:00:00Z")


def x_api(path, params):
    url = f"https://api.x.com/2/{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BEARER}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


user = x_api(f"users/by/username/{USERNAME}", {"user.fields": "public_metrics"})["data"]
print(f"account: @{USERNAME} / total posts incl. replies+reposts: "
      f"{user['public_metrics']['tweet_count']}")

total, with_video, with_photo_only, no_media = 0, 0, 0, 0
monthly = Counter()
token = None
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
        total += 1
        monthly[tw["created_at"][:7]] += 1
        keys = tw.get("attachments", {}).get("media_keys", [])
        types = {media_map.get(k) for k in keys}
        if {"video", "animated_gif"} & types:
            with_video += 1
        elif "photo" in types:
            with_photo_only += 1
        else:
            no_media += 1
    token = page.get("meta", {}).get("next_token")
    if not token:
        break

print(f"\n== original posts since {START} ==")
print(f"total: {total}")
print(f"  with video/GIF : {with_video}")
print(f"  with photo only: {with_photo_only}")
print(f"  text only      : {no_media}")
print("\nmonthly:")
for m in sorted(monthly):
    print(f"  {m}: {monthly[m]}")
