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
   - For an existing database, connect the integration to it and use its ID in the profile. See property mapping below.
   - To create a new database:
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
For a favorites import, send the bot a UTF-8 `.json` or `.txt` favorites file from your TikTok data export (up to 20 MiB).
Extract the export ZIP first. Every TikTok link in the supplied file is imported, so send the favorites file rather than
an entire export containing browsing history if you only want favorites. Normal post URLs, `tiktokv.com/share/video` export links, and vm/vt short links are supported.
Repeated canonical post links are collapsed before queuing.

## Homelab deployment

The Ubuntu 24.04 host uses Docker Engine, Compose v2, and Buildx (`docker.io`,
`docker-compose-v2`, and `docker-buildx`). No inbound application port is needed:
the bot polls Telegram and media is served directly from object storage.

Copy source from your workstation with:

```bash
bash scripts/copy_to_host.sh eva-pilot@192.168.10.42 "$HOME/agent/id_ed25519"
```

This uploads an explicit list of source files to `/opt/tiktok-vault` using SSH and
sudo. It preserves host `.env`, `profiles.yaml`, `secrets/`, and Docker volumes.
It does not start or rebuild services. On first setup, copy `profiles.example.yaml`
to `profiles.yaml` and fill in the profile IDs. Enter credentials directly on the
host in `.env` (use `.env.example` as a reference), and restrict it with `sudo chmod
600 /opt/tiktok-vault/.env`. For R2, use `S3_REGION=auto`, the bucket's S3 endpoint,
and its public custom domain as `PUBLIC_MEDIA_BASE_URL` without the bucket name.
Set each profile's `video_mode` to `external` for public media playback.

After configuration, start or update from the host:

```bash
cd /opt/tiktok-vault
sudo docker compose up -d --build
sudo docker compose ps
```

Containers restart automatically after a reboot. Keep backups of both named
volumes (`data` for SQLite and `redis` for pending jobs), as well as host configuration.
Do not use `docker compose down -v` unless you intend to delete those volumes.

## Existing databases and multiple users

For the existing partner mood board's strict collection mappings, structured
JSON imports, and deployment status, see [the compatibility guide](docs/partner-moodboard.md).

Each profile selects its own Notion integration, database, AI focus, and S3 prefix. Add one profile per Telegram user;
duplicate Telegram IDs are rejected. Profiles can target the same database or different workspaces.
The integration needs access to the target database. Existing databases must have a single data source, as this
project still uses the pinned database API.

The database's title field is detected automatically. Matching optional fields are populated when their types match;
absent fields or incompatible optional fields are skipped. Existing rows are left untouched, and no properties are created or renamed. Notion may add new select or multi-select options when pages are saved.
Use `notion_properties` to map the pipeline's standard fields to your existing names:

```yaml
profiles:
  nils:
    telegram_user_id: 111111111
    notion_token: ${NOTION_TOKEN_NILS}
    notion_database_id: 0123456789abcdef0123456789abcdef
    notion_properties:
      URL: Source          # must be a URL property
      Tags: Topics         # must be a multi-select property
      Summary: Notes       # must be a rich-text property
      Status: ""           # leave this field unpopulated
```

Explicit mappings to missing or incompatible fields produce an error before downloading. The Name field must
map to the database's title field. When a compatible URL field exists, the pipeline checks it for an exact canonical
post URL and reuses a matching page. Rows containing short links or other URL variations are not automatically matched.
SQLite also tracks posts saved by each profile.

## Bulk workloads

Imports keep a single URL list in Redis and schedule batches of 100 with cursor-based continuation jobs. Each post
runs independently with the usual retries; a bad post does not stop the rest. Redis stores pending imports and jobs
for up to 30 days, and the existing append-only volume preserves them across restarts. The bot sends a result for
each completed post. Telegram replies are paced across workers and honor rate-limit responses.

`WORKER_CONCURRENCY` limits active jobs per worker (default 2). For more throughput, increase it cautiously or run
`docker compose up -d --scale worker=3`. Workers share Redis and the SQLite data volume on the same Docker host;
SQLite uses WAL mode and a busy timeout. A Redis lock prevents simultaneous saves of the same profile/post.
All workers share a request schedule per Notion integration. Adding workers helps downloads and AI calls, while
[Notion's integration rate limit](https://developers.notion.com/reference/request-limits) remains a throughput limit.
The data volume must remain shared; this SQLite deployment is not intended for separate hosts or network filesystems.

A process crash between Notion page creation and SQLite recording can still cause a duplicate if the database has no
compatible URL property. Keep a URL field mapped for recovery and matching existing pages.

Run regression tests with `python -m unittest discover -s tests -v` after installing `requirements.txt`.

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
- Reply to the bot's message with `#tag` / `kind:recipe` to correct a row
