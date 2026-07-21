# ntv-rd-ig-sync

@ntv_rd (X) の投稿を Instagram (tokyoprototye.jp) に日英キャプション付きで自動転記します。

## 仕組み

- 毎時7分 (UTC) に GitHub Actions が起動
- X API (Owned Reads) で @ntv_rd の新規オリジナルポストを取得
- Instagram 側の直近キャプションに含まれる X ポスト URL と照合して重複投稿を防止
- 動画: 中央トリミングで 9:16 全画面化 + 左上ロゴ + 上部タイトル + 下部テロップ (Claude が本文から生成) → リールとして投稿
- 画像: そのまま投稿 (複数はカルーセル)
- キャプション: 日本語本文 + 英訳 + 元ポスト URL

## 必要な Secrets (Settings → Secrets and variables → Actions)

| Secret | 内容 |
|---|---|
| `X_BEARER_TOKEN` | X API の Bearer Token |
| `IG_ACCESS_TOKEN` | Instagram API (Instagram Login) の長期アクセストークン |
| `ANTHROPIC_API_KEY` または `GEMINI_API_KEY` または `DEEPL_API_KEY` | キャプション生成・翻訳用 (いずれか1つ。Claude推奨、Gemini無料枠可、DeepLは翻訳のみ) |
| `GH_PAT` | このリポジトリへの書き込み権限を持つ GitHub PAT (動画ホスティング用リリース作成とトークン自動更新に使用) |

## 運用メモ

- `refresh_token.yml` が毎週月曜に Instagram トークンを自動更新します (放置で期限切れしない)
- 手動実行: Actions タブ → "X to Instagram sync" → Run workflow (dry_run にチェックで投稿なしのテスト)
- 編集済み動画は Instagram が取得できるようリリースアセットとして一時ホストされます (リポジトリは public である必要があります)
