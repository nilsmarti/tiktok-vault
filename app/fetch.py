"""Download TikTok posts. Videos via yt-dlp, photo slideshows via gallery-dl
(yt-dlp only returns the background audio for slideshows)."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yt_dlp
from PIL import Image, ImageOps

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".opus", ".ogg"}
META_KEYS = (
    "id", "title", "description", "uploader", "uploader_id", "channel", "upload_date",
    "timestamp", "duration", "webpage_url", "view_count", "like_count", "comment_count",
    "track", "artists", "tags",
)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
POST_RE = re.compile(r"tiktok\.com/@(?P<user>[^/?#]+)/(?P<kind>video|photo)/(?P<id>\d+)")


class PermanentError(Exception):
    """Not worth retrying (unsupported URL, deleted post, ...)."""


@dataclass
class Post:
    url: str  # canonical
    post_id: str
    is_slideshow: bool
    meta: dict = field(default_factory=dict)


@dataclass
class Media:
    video_path: Path | None = None
    image_paths: list[Path] = field(default_factory=list)
    audio_path: Path | None = None


async def canonicalize(http: httpx.AsyncClient, url: str) -> str:
    """Resolve vm./vt.tiktok.com short links to the full /@user/video|photo/<id> URL."""
    if POST_RE.search(url):
        return url.split("?")[0]
    try:
        r = await http.get(url, follow_redirects=True, headers={"User-Agent": UA}, timeout=20)
        final = str(r.url)
        if POST_RE.search(final):
            return final.split("?")[0]
    except httpx.HTTPError:
        pass
    return url  # let yt-dlp try


def _ydl_opts(cookies: str | None, **extra) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 30, "retries": 3, **extra}
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def resolve(url: str, cookies: str | None) -> Post:
    m = POST_RE.search(url)
    url_says_photo = bool(m and m.group("kind") == "photo")
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(cookies)) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        if url_says_photo:  # gallery-dl may still manage; metadata will be thin
            return Post(url=url, post_id=m.group("id"), is_slideshow=True,
                        meta={"id": m.group("id"), "webpage_url": url, "uploader": m.group("user")})
        msg = str(e).lower()
        if any(s in msg for s in ("unsupported url", "not available", "private", "removed")):
            raise PermanentError(str(e)) from e
        raise
    if info.get("_type") == "playlist":
        raise PermanentError("Profile/playlist links aren't supported, send a single post.")
    meta = {k: info.get(k) for k in META_KEYS if info.get(k) is not None}
    fmts = info.get("formats") or []
    audio_only = bool(fmts) and all(f.get("vcodec") == "none" for f in fmts)
    webpage = info.get("webpage_url") or url
    return Post(url=webpage, post_id=str(info["id"]),
                is_slideshow=url_says_photo or "/photo/" in webpage or audio_only, meta=meta)


def download_video(post: Post, workdir: Path, cookies: str | None) -> Media:
    opts = _ydl_opts(
        cookies,
        outtmpl=str(workdir / "source.%(ext)s"),
        format="bv*+ba/b",
        format_sort=["vcodec:h264", "res:1080", "acodec:aac"],  # avoid h265 -> plays in browsers
        merge_output_format="mp4",
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(post.url, download=True)
    files = [p for p in workdir.glob("source.*") if p.suffix not in (".part", ".ytdl")]
    if not files:
        raise RuntimeError("yt-dlp finished but produced no file")
    return Media(video_path=files[0])


def _natural_key(p: Path):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def download_slideshow(post: Post, workdir: Path, cookies: str | None) -> Media:
    out = workdir / "slides_raw"
    out.mkdir()
    cmd = ["gallery-dl", "--dest", str(out), "-o", "directory=[]", post.url]
    if cookies:
        cmd[1:1] = ["--cookies", cookies]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    files = sorted((p for p in out.rglob("*") if p.is_file()), key=_natural_key)
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    if not images:
        raise RuntimeError(f"gallery-dl got no images (rc={res.returncode}): {res.stderr[-500:]}")
    norm_dir = workdir / "slides"
    norm_dir.mkdir()
    jpgs = []
    for i, src in enumerate(images, 1):
        dst = norm_dir / f"img_{i:02d}.jpg"
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1600, 1600))
            im.save(dst, "JPEG", quality=88)
        jpgs.append(dst)
    audio = next((p for p in files if p.suffix.lower() in AUDIO_EXT), None)
    return Media(image_paths=jpgs, audio_path=audio)
