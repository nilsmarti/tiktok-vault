"""One TikTok link -> archived in S3 -> enriched -> Notion page."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import ai, fetch, media, notion
from .config import Profile, Settings
from .db import Store
from .storage import Storage

log = logging.getLogger(__name__)
PLATFORM = "tiktok"


@dataclass
class Result:
    notion_url: str
    title: str = ""
    kind: str = ""
    tags: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    duplicate: bool = False
    warnings: list[str] = field(default_factory=list)


async def _video_block(nc: notion.Notion, profile: Profile, storage: Storage,
                       video: Path, key: str, warnings: list[str]) -> dict | None:
    mode = profile.video_mode
    if mode == "auto":
        limit = await nc.max_upload_bytes()
        mode = "upload" if limit is None or video.stat().st_size <= limit else "external"
    if mode == "upload":
        try:
            return notion.media_block("video", upload_id=await nc.upload(video, "video/mp4"))
        except RuntimeError as e:
            log.warning("Notion video upload failed, falling back to external: %s", e)
    if url := storage.public_url(key):
        return notion.media_block("video", url=url)
    warnings.append("Video too large for this Notion workspace and no PUBLIC_MEDIA_BASE_URL set – archived in S3 only.")
    return None


async def run(s: Settings, http: httpx.AsyncClient, storage: Storage, store: Store,
              profile: Profile, url: str, redis=None) -> Result:
    url = await fetch.canonicalize(http, url)
    post = await asyncio.to_thread(fetch.resolve, url, s.tiktok_cookies_file)

    if redis is not None:
        # Covers dedupe, download and page creation across worker processes.
        async with redis.lock(f"post-lock:{profile.name}:{post.post_id}",
                              timeout=960, blocking_timeout=850):
            return await _archive(s, http, storage, store, profile, post, redis)
    return await _archive(s, http, storage, store, profile, post, redis)


async def _archive(s, http, storage, store, profile, post, redis) -> Result:
    if existing := store.get(profile.name, PLATFORM, post.post_id):
        return Result(notion_url=existing, duplicate=True)

    prefix = f"{profile.name}/{PLATFORM}/{post.post_id}"
    warnings: list[str] = []
    nc = notion.Notion(http, profile.notion_token, redis)
    schema = await nc.database_schema(profile.notion_database_id)
    # Validate configuration before downloading or invoking models.
    notion.adapt_properties(notion.build_properties(ai.Extraction(), post.meta,
        is_slideshow=post.is_slideshow, archive_uri=storage.uri(prefix)),
        schema, profile.notion_properties)
    if existing := await nc.find_existing(profile.notion_database_id, schema,
                                         profile.notion_properties, post.url):
        store.put(profile.name, PLATFORM, post.post_id, existing["url"], prefix)
        return Result(notion_url=existing["url"], duplicate=True)
    tmp_root = s.data_dir / "tmp"
    tmp_root.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(dir=tmp_root) as td:
        wd = Path(td)
        transcript = ""
        video: Path | None = None

        # 1. download + archive first: the whole point is that posts disappear
        if post.is_slideshow:
            m = await asyncio.to_thread(fetch.download_slideshow, post, wd, s.tiktok_cookies_file)
            images = m.image_paths
            thumb = images[0]
            frames = images[: s.max_frames]
            for i, img in enumerate(images, 1):
                await asyncio.to_thread(storage.put_file, img, f"{prefix}/img_{i:02d}.jpg", "image/jpeg")
            if m.audio_path:
                await asyncio.to_thread(storage.put_file, m.audio_path, f"{prefix}/audio{m.audio_path.suffix}",
                                        "audio/mpeg")
        else:
            m = await asyncio.to_thread(fetch.download_video, post, wd, s.tiktok_cookies_file)
            video, vmeta = await asyncio.to_thread(media.normalize, m.video_path)
            dur = media.duration(vmeta)
            thumb = await asyncio.to_thread(media.thumbnail, video, wd / "thumb.jpg", dur)
            frames = await asyncio.to_thread(media.keyframes, video, wd / "frames", s.max_frames, dur)
            await asyncio.to_thread(storage.put_file, video, f"{prefix}/video.mp4", "video/mp4")
            audio = await asyncio.to_thread(media.extract_audio, video, wd / "audio.mp3", vmeta)
            if audio and s.stt_base_url:
                try:
                    transcript = await ai.transcribe(http, s, audio)
                except httpx.HTTPError as e:
                    warnings.append(f"Transcription failed: {e}")
        await asyncio.to_thread(storage.put_file, thumb, f"{prefix}/thumb.jpg", "image/jpeg")

        # 2. enrich (never block archiving on the LLM)
        try:
            ex = await ai.extract(http, s, profile, post.meta, transcript, frames)
        except Exception as e:  # noqa: BLE001
            log.exception("extraction failed")
            warnings.append(f"AI extraction failed ({type(e).__name__}) – saved with caption only.")
            ex = ai.fallback_extraction(post.meta)

        await asyncio.to_thread(storage.put_json, {
            "url": post.url, "profile": profile.name, "meta": post.meta,
            "transcript": transcript, "extraction": ex.model_dump(),
        }, f"{prefix}/meta.json")

        # 3. Notion
        blocks: list[dict] = []
        if video is not None:
            if b := await _video_block(nc, profile, storage, video, f"{prefix}/video.mp4", warnings):
                blocks.append(b)
        else:
            for img in images:
                blocks.append(notion.media_block("image", upload_id=await nc.upload(img, "image/jpeg")))
        cover_id = await nc.upload(thumb, "image/jpeg")
        page = await nc.create_page(
            profile.notion_database_id,
            notion.adapt_properties(
                notion.build_properties(ex, post.meta, is_slideshow=post.is_slideshow,
                                       archive_uri=storage.uri(prefix)), schema, profile.notion_properties),
            notion.build_body(ex, post.meta, transcript, blocks),
            cover_upload_id=cover_id,
        )

    store.put(profile.name, PLATFORM, post.post_id, page["url"], prefix)
    return Result(notion_url=page["url"], title=ex.title, kind=ex.kind, tags=ex.tags,
                  places=[p.label for p in ex.places], warnings=warnings)
