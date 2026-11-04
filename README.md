# tiktok-vault

Share a TikTok to a Telegram bot → video (or photo slideshow) archived to S3 → transcript + on-screen text + structured
extraction (places, products, recipes, creative notes) → new row in **that person's** Notion database, with a playable video,
cover thumbnail and Telegram confirmation.

```
Telegram ─► bot ─► Redis (arq) ─► worker: canonicalize → dedupe → yt-dlp / gallery-dl → ffmpeg
                                          → S3 → whisper → VLM extraction → Notion → reply
```

## Setup

1. **Bot**: create it with @BotFather, put the token in `.env`.
2. **Notion (each person, in their own workspace)**
   - Create an internal integration (notion.so/profile/integrations), copy its token.
   - Create an empty page, connect the integration to it (page `•••` → Connections).
   - `docker compose run --rm worker python scripts/setup_notion_db.py --token ntn_... --parent-page <page url>`
     → prints `notion_database_id`.
   - Add a Gallery view with card preview = *Page cover* for browsing.
3. **Profiles**: `cp profiles.example.yaml profiles.yaml`, fill in IDs. Each person sends `/start` to the bot to see their Telegram user ID.
   Strangers are silently ignored.
4. **Storage**: create the bucket. For free Notion workspaces, set `PUBLIC_MEDIA_BASE_URL` (see below).
5. **Models**: `cp .env.example .env`. Any OpenAI-compatible endpoint works for both the VLM (`LLM_*`, must accept images)
   and Whisper (`STT_*`, `/audio/transcriptions`, e.g. speaches / faster-whisper-server).
6. `docker compose up -d --build`

Usage: in TikTok, **Share → Telegram → the bot**, or paste links (several per message is fine).

## Video playback in Notion

| `video_mode` | Behaviour |
|---|---|
| `auto` (default) | Ask Notion for the workspace upload limit. Fits → upload into Notion, otherwise external. |
| `upload` | Always upload (multi-part above 20 MiB). Paid workspaces allow up to 5 GiB/file. |
| `external` | `video` block pointing at `PUBLIC_MEDIA_BASE_URL/<key>`. Needs a stable public URL. Presigned URLs expire, don't use them. |

Free Notion workspaces cap uploads at 5 MiB, so most videos go external there. Thumbnails and slideshow images are small and are always uploaded.
If neither option works, the video is still archived in S3 and the page says so.

## S3 layout

```
<profile>/tiktok/<post_id>/video.mp4 | img_01.jpg… + audio.* | thumb.jpg | meta.json
```
`meta.json` holds the yt-dlp metadata, transcript and extraction, so you can rebuild or re-index without re-downloading.

## Notes

- Archiving happens **before** AI. If the LLM fails, the page is still created with the caption, and the reply warns you.
- Dedupe is per person per post ID (`/data/index.sqlite3`), so you can both save the same video.
- The worker runs `pip install -U yt-dlp gallery-dl` on every start. When TikTok breaks downloads, `docker compose restart worker`.
- Videos are forced to H.264/AAC + faststart (TikTok sometimes serves H.265, which browsers won't play).
- The Notion API is pinned to `2022-06-28` so `database_id` parents keep working. The databases here are single-source.

## Next (not in MVP)
- Instagram Reels (yt-dlp + burner-account cookies)
- `/import` for the TikTok data-export favourites list
- Reply to the bot's message with `#tag` / `kind:recipe` to correct a row
