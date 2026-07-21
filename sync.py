#!/usr/bin/env python3
"""
X (@ntv_rd) -> Instagram (tokyoprototye.jp) auto cross-post.

Runs on GitHub Actions.
- Fetches recent original posts from X (Owned Reads)
- Skips posts already on Instagram (dedupe via X post URL in IG captions)
- Videos: brand template (center-crop 9:16, navy grid bands: hook 2 lines /
  right-aligned title / 4s-rhythm telops, recolored logo) -> posted as Reels
- Images: posted as-is (16:9 within IG limits), carousel if multiple
- Caption: original JA text + natural EN translation + X post URL
Modes:
- DRY_RUN=true: no rendering, no posting; prints generated copy
- PREVIEW=true: full rendering, no posting; writes results to preview/
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import overlays

X_BEARER = os.environ["X_BEARER_TOKEN"]
IG_TOKEN = os.environ["IG_ACCESS_TOKEN"]
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_OAUTH = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "")
GH_PAT = os.environ.get("GH_PAT", os.environ.get("GITHUB_TOKEN", ""))
X_USERNAME = os.environ.get("X_USERNAME", "ntv_rd")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
PREVIEW = os.environ.get("PREVIEW", "false").lower() == "true"
CAPTIONS_ONLY = os.environ.get("CAPTIONS_ONLY", "false").lower() == "true"
STATE_FILE = "posted_ids.json"
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "3"))
LOOKBACK = int(os.environ.get("LOOKBACK_TWEETS", "10"))

GRAPH = "https://graph.instagram.com/v23.0"
HOOK_END = 2.8


def http(url, method="GET", headers=None, data=None, timeout=120):
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=body, method=method, headers=headers or {}),
            timeout=timeout,
        ) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:1000]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} for {url.split('?')[0]}: {detail}") from None


def x_api(path, params):
    url = f"https://api.x.com/2/{path}?{urllib.parse.urlencode(params)}"
    return http(url, headers={"Authorization": f"Bearer {X_BEARER}"})


def claude(prompt):
    last_err = None
    for model in ("claude-sonnet-5", "claude-sonnet-4-5"):
        body = json.dumps({
            "model": model,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        for attempt in range(3):
            try:
                r = http("https://api.anthropic.com/v1/messages", method="POST",
                         headers={"x-api-key": ANTHROPIC_KEY,
                                  "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"}, data=body)
                return r["content"][0]["text"]
            except RuntimeError as e:
                last_err = e
                msg = str(e)
                if ("HTTP 404" in msg or "not_found" in msg) and "model" in msg:
                    break  # unknown model -> try next
                if any(c in msg for c in ("HTTP 429", "HTTP 529", "HTTP 500",
                                          "HTTP 503")) and attempt < 2:
                    time.sleep(30)
                else:
                    raise
    raise last_err


def claude_code(prompt):
    """Generate via Claude Code CLI authenticated with a Max-plan OAuth token."""
    for attempt in range(3):
        r = subprocess.run(
            ["claude", "-p", "--output-format", "text"],
            input=prompt, capture_output=True, text=True, timeout=300,
            env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": CLAUDE_OAUTH})
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            return out
        print(f"claude-code attempt {attempt+1} failed: {(r.stderr or out)[:300]}")
        time.sleep(20)
    raise RuntimeError("claude code CLI failed after retries")


def gemini(prompt):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }).encode()
    last_err = None
    for model in ("gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash"):
        for attempt in range(3):
            try:
                r = http("https://generativelanguage.googleapis.com/v1beta/models/"
                         f"{model}:generateContent", method="POST",
                         headers={"content-type": "application/json",
                                  "x-goog-api-key": GEMINI_KEY}, data=body)
                return r["candidates"][0]["content"]["parts"][0]["text"]
            except RuntimeError as e:
                last_err = e
                msg = str(e)
                if "HTTP 429" in msg and attempt < 2:
                    m = re.search(r"retry in (\d+)", msg)
                    wait = min(int(m.group(1)) + 10, 120) if m else 65
                    print(f"gemini 429 on {model}; waiting {wait}s")
                    time.sleep(wait)
                elif "HTTP 429" in msg:
                    break  # try next model
                else:
                    break
    raise last_err


def deepl_translate(text):
    r = http("https://api-free.deepl.com/v2/translate", method="POST",
             headers={"Authorization": f"DeepL-Auth-Key {DEEPL_KEY}"},
             data={"text": text, "source_lang": "JA", "target_lang": "EN-US"})
    return r["translations"][0]["text"]


def clean_tweet_text(text):
    """Remove X's internal t.co media links; tidy whitespace."""
    t = re.sub(r"https?://t\.co/\S+", "", text)
    return re.sub(r"[ \t]+\n", "\n", t).strip()


def load_posted_ids():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_posted_id(tweet_id):
    ids = sorted(load_posted_ids() | {tweet_id})
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, indent=0)


def generate_copy(tweet_text, n_telops):
    """Generate hook, title, telops and bilingual caption. Returns dict."""
    if not (ANTHROPIC_KEY or CLAUDE_OAUTH):
        if DEEPL_KEY:
            return {"hook_line1": "", "hook_line2": "", "title": "",
                    "telops": [], "caption_ja": tweet_text,
                    "caption_en": deepl_translate(tweet_text)}
        raise RuntimeError("No translation API key configured")
    telop_req = (f'"telops": ["画面下部に順番に表示するテロップ。ちょうど{n_telops}行。'
                 '各行は全角18文字以内で1行。本文の要点を、続きが気になる語り口で'
                 '小出しにする(例: 現象の描写→驚き→種明かし→作家/会場→会期の順)"]'
                 if n_telops else '"telops": []')
    prompt = f"""あなたは日本テレビR&Dラボ(@ntv_rd)のSNS運用担当です。以下のXポストをInstagramリールに転記します。

Xポスト本文:
---
{tweet_text}
---

次のJSONだけを出力してください(コードブロック不要):
{{
  "hook_line1": "冒頭3秒に大きく出すフック。短く強い一言、全角13文字以内",
  "hook_line2": "フックのサブタイトル(補足の一言、全角18文字以内。不要なら空文字)",
  "title": "画面上部に常時表示する小さめのヘッダー(作家名・イベント名と会場・会期など、全角22文字以内)",
  {telop_req},
  "caption_ja": "Instagramキャプション日本語。原文の文章をそのまま使い、@ユーザー名は一字も変更しない(@メンションの捏造は厳禁)。ハッシュタグはInstagramの上限に合わせ、日本語・英語を厳選して合計5個ちょうどまで(原文由来のタグも5個の中に含める)。URLは入れない",
  "caption_en": "自然で簡潔な英訳(直訳調を避ける)。@ユーザー名は原文のまま。ハッシュタグとURLは入れない(タグはcaption_ja側の5個のみ)"
}}"""
    providers = []
    if ANTHROPIC_KEY:
        providers.append(("claude-api", claude))
    if CLAUDE_OAUTH:
        providers.append(("claude-code", claude_code))
    if GEMINI_KEY:
        providers.append(("gemini", gemini))
    txt, last_err = None, None
    for name, fn in providers:
        try:
            txt = fn(prompt).strip()
            break
        except Exception as e:
            print(f"provider {name} failed: {str(e)[:200]}")
            last_err = e
    if txt is None:
        raise last_err
    if txt.startswith("```"):
        txt = txt.strip("`").lstrip("json").strip()
    return json.loads(txt)


def ig_recent_captions():
    url = f"{GRAPH}/me/media?fields=caption&limit=50&access_token={IG_TOKEN}"
    return [m.get("caption") or "" for m in http(url).get("data", [])]


def pick_video_variant(media):
    best, best_br = None, -1
    for v in media.get("variants", []):
        if v.get("content_type") == "video/mp4" and v.get("bit_rate", 0) > best_br:
            best, best_br = v["url"], v["bit_rate"]
    return best


def probe_duration(path):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path]).decode().strip())


def n_telops_for(dur):
    if dur <= HOOK_END + 4:
        return 2
    return max(2, min(12, round((dur - HOOK_END) / 4.2)))


def render_video(src, dst, copy, dur, workdir):
    """Assemble the brand template: crop, logo, hook band, title, telops."""
    telops = [t for t in copy.get("telops", []) if t.strip()]
    overlays.build_overlays(workdir, copy.get("hook_line1") or " ",
                            copy.get("hook_line2") or "",
                            copy.get("title") or " ", telops)
    inputs = ["-i", src, "-i", f"{workdir}/ov_logo.png"]
    fc = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
          "crop=1080:1920[v];[v][1:v]overlay=0:0[c1]")
    prev, idx = "c1", 2
    if (copy.get("hook_line1") or "").strip():
        inputs += ["-i", f"{workdir}/ov_hook.png"]
        fc += f";[{prev}][{idx}:v]overlay=0:0:enable='between(t,0,{HOOK_END})'[c{idx}]"
        prev = f"c{idx}"; idx += 1
    if (copy.get("title") or "").strip():
        inputs += ["-i", f"{workdir}/ov_title.png"]
        fc += f";[{prev}][{idx}:v]overlay=0:0:enable='gte(t,{HOOK_END})'[c{idx}]"
        prev = f"c{idx}"; idx += 1
    if telops:
        start = HOOK_END if dur > HOOK_END + 4 else 0
        seg = (dur - start) / len(telops)
        for i in range(len(telops)):
            inputs += ["-i", f"{workdir}/ov_telop{i}.png"]
            a = start + i * seg
            b = start + (i + 1) * seg + (0.5 if i == len(telops) - 1 else 0)
            fc += (f";[{prev}][{idx}:v]overlay=0:0:"
                   f"enable='between(t,{a:.2f},{b:.2f})'[c{idx}]")
            prev = f"c{idx}"; idx += 1
    fc += f";[{prev}]format=yuv420p[out]"
    has_audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", src],
        capture_output=True).stdout.strip() != b""
    cmd = (["ffmpeg", "-y", "-v", "error"] + inputs +
           ["-filter_complex", fc, "-map", "[out]"] +
           (["-map", "0:a", "-c:a", "aac", "-b:a", "128k"] if has_audio else []) +
           ["-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-movflags", "+faststart", dst])
    subprocess.check_call(cmd)


def gh_release_upload(path, tag):
    api = f"https://api.github.com/repos/{GH_REPO}"
    h = {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json"}
    try:
        rel = http(f"{api}/releases/tags/{tag}", headers=h)
    except Exception:
        rel = http(f"{api}/releases", method="POST", headers=h,
                   data=json.dumps({"tag_name": tag, "name": tag,
                                    "body": "auto media hosting"}).encode())
    up = rel["upload_url"].split("{")[0] + f"?name={os.path.basename(path)}"
    with open(path, "rb") as f:
        data = f.read()
    asset = http(up, method="POST", data=data,
                 headers={**h, "Content-Type": "video/mp4"})
    return asset["browser_download_url"]


def ig_create_and_publish(params):
    r = http(f"{GRAPH}/me/media", method="POST",
             data={**params, "access_token": IG_TOKEN})
    cid = r["id"]
    for _ in range(60):
        st = http(f"{GRAPH}/{cid}?fields=status_code&access_token={IG_TOKEN}")
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"IG container error: {st}")
        time.sleep(10)
    pub = http(f"{GRAPH}/me/media_publish", method="POST",
               data={"creation_id": cid, "access_token": IG_TOKEN})
    return pub["id"]


def preview_note(url, kind, caption, note=""):
    os.makedirs("preview", exist_ok=True)
    with open("preview/captions.md", "a", encoding="utf-8") as f:
        f.write(f"\n---\n\n## {url}\n\n種別: {kind}{note}\n\n"
                f"### 投稿キャプション\n\n```\n{caption}\n```\n")


def main():
    user = x_api(f"users/by/username/{X_USERNAME}", {})["data"]
    params = {
        "max_results": LOOKBACK, "exclude": "replies,retweets",
        "expansions": "attachments.media_keys",
        "media.fields": "url,variants,type,media_key",
        "tweet.fields": "created_at,text,entities",
    }
    if os.environ.get("START_TIME"):
        params["start_time"] = os.environ["START_TIME"]
    if os.environ.get("END_TIME"):
        params["end_time"] = os.environ["END_TIME"]
    tweets = x_api(f"users/{user['id']}/tweets", params)
    media_map = {m["media_key"]: m for m in tweets.get("includes", {}).get("media", [])}
    posted_ids = load_posted_ids()
    existing = "" if (PREVIEW or CAPTIONS_ONLY) else " ".join(ig_recent_captions())
    posted = 0

    for tw in reversed(tweets.get("data", [])):
        url = f"https://x.com/{X_USERNAME}/status/{tw['id']}"
        if tw["id"] in posted_ids or tw["id"] in existing:
            continue
        text = clean_tweet_text(tw["text"])
        keys = tw.get("attachments", {}).get("media_keys", [])
        media = [media_map[k] for k in keys if k in media_map]
        if not media:
            print(f"skip (no media): {url}")
            continue
        if posted >= MAX_POSTS_PER_RUN:
            break
        print(f"processing: {url}")
        vids = [m for m in media if m["type"] in ("video", "animated_gif")]
        imgs = [m["url"] for m in media if m["type"] == "photo"]

        if DRY_RUN or CAPTIONS_ONLY:
            try:
                copy = generate_copy(text, 4 if (vids and not CAPTIONS_ONLY) else 0)
            except Exception as e:
                print(f"copy generation failed for {url}: {e}")
                preview_note(url, "生成失敗(レート制限)", "(後で再実行してください)")
                posted += 1
                continue
            caption = f"{copy['caption_ja']}\n\n{copy['caption_en']}"
            if CAPTIONS_ONLY:
                kind = "動画リール" if vids else f"画像投稿 ({len(imgs)}枚)"
                preview_note(url, kind, caption)
            else:
                print(json.dumps(copy, ensure_ascii=False, indent=2))
                print("DRY_RUN: not posting")
            posted += 1
            continue

        with tempfile.TemporaryDirectory() as td:
            if vids:
                src, out = f"{td}/src.mp4", f"{td}/reel.mp4"
                urllib.request.urlretrieve(pick_video_variant(vids[0]), src)
                dur = probe_duration(src)
                copy = generate_copy(text, n_telops_for(dur))
                caption = f"{copy['caption_ja']}\n\n{copy['caption_en']}"
                render_video(src, out, copy, dur, td)
                if PREVIEW:
                    os.makedirs("preview", exist_ok=True)
                    shutil.copy(out, f"preview/{tw['id']}.mp4")
                    preview_note(url, f"動画リール ({dur:.0f}秒)", caption,
                                 f" / フック:「{copy.get('hook_line1', '')}」")
                else:
                    host_url = gh_release_upload(out, f"media-{tw['id']}")
                    ig_create_and_publish({"media_type": "REELS",
                                           "video_url": host_url,
                                           "caption": caption,
                                           "share_to_feed": "true"})
                    save_posted_id(tw["id"])
            else:
                copy = generate_copy(text, 0)
                caption = f"{copy['caption_ja']}\n\n{copy['caption_en']}"
                if PREVIEW:
                    note = "\n\n画像URL:\n" + "\n".join(imgs)
                    preview_note(url, f"画像投稿 ({len(imgs)}枚, 無編集)", caption, note)
                elif len(imgs) == 1:
                    ig_create_and_publish({"image_url": imgs[0], "caption": caption})
                    save_posted_id(tw["id"])
                else:
                    children = []
                    for iu in imgs[:10]:
                        c = http(f"{GRAPH}/me/media", method="POST",
                                 data={"image_url": iu, "is_carousel_item": "true",
                                       "access_token": IG_TOKEN})
                        children.append(c["id"])
                    ig_create_and_publish({"media_type": "CAROUSEL",
                                           "children": ",".join(children),
                                           "caption": caption})
                    save_posted_id(tw["id"])
        print(f"{'previewed' if PREVIEW else 'posted'}: {url}")
        posted += 1

    print(f"done. processed={posted}")


if __name__ == "__main__":
    sys.exit(main())
