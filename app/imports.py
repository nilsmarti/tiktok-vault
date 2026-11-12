"""Extract post links from TikTok JSON or text exports without fetching them."""
from __future__ import annotations

import re
import json

from .moodboard import COLLECTIONS

# Explicit aliases for the sorted collection export; never infer classification.
COLLECTION_ALIASES = {
    "beauty_health": "beauty/health",
    "career_education": "career/education",
    "IG_content_captions&socials": "IG content/captions & socials",
    "Sewing_customizing": "Sewing/customizing",
    "bestie_vids": "bestie vids",
    "diy_ideas": "diy ideas",
    "fashion_hacks": "fashion hacks",
    "fashion_inspo": "fashion inspo",
    "graphics_ressources_websites": "graphics ressources",
    "graphics_tutorials": "graphics tutorials",
    "photography_editing": "photography+editing",
    "streaming_gaming": "streaming/gaming",
    "vid_content_ideas": "vid content ideas",
    "vid_editing": "vid editing",
}


def collection_records(text: str) -> list[dict]:
    """Validate the entire sorted export before any jobs are submitted."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    data = json.loads(text, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or not data:
        raise ValueError("Expected a nonempty JSON object keyed by video ID")
    records = []
    for key, row in data.items():
        if not isinstance(row, dict):
            raise ValueError(f"Record {key}: expected an object")
        for name in ("video_id", "creator", "creator_id", "description", "url",
                     "thumbnail_url", "collection_name", "post_type"):
            if not isinstance(row.get(name), str):
                raise ValueError(f"Record {key}: expected text field {name}")
        post = POST_RE.fullmatch(row["url"].rstrip("/"))
        if not post or not row["url"].startswith("https://") or post[3] != key or row["video_id"] != key:
            raise ValueError(f"Record {key}: expected matching TikTok URL and video ID")
        if row["post_type"] not in ("video", "photo") or post[2] != row["post_type"]:
            raise ValueError(f"Record {key}: post_type must match the URL")
        name = COLLECTION_ALIASES.get(row["collection_name"], row["collection_name"])
        if name not in COLLECTIONS:
            raise ValueError(f"Record {key}: unmapped collection {row['collection_name']!r}")
        records.append({"url": row["url"], "bookmark": {
            "collections": [name], "tags": [], "source_record": dict(row)}})
    return records

LINK_RE = re.compile(r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktokv?\.com/[^\s<>\"\\]+", re.I)
POST_RE = re.compile(r"https?://(?:www\.|m\.)?tiktok\.com/@([^/?#]+)/((?:video|photo))/(\d+)", re.I)

SHARE_RE = re.compile(r"https?://(?:www\.)?tiktokv?\.com/share/video/(\d+)", re.I)


def links(text: str) -> list[str]:
    # JSON exports sometimes escape slashes. We only extract URLs, never execute content.
    text = text.replace(r"\/", "/")
    result = {}
    for match in LINK_RE.finditer(text):
        url = match.group().rstrip("),.!>'}")
        post = POST_RE.match(url)
        if post:
            url = f"https://www.tiktok.com/@{post[1]}/{post[2]}/{post[3]}"
        elif share := SHARE_RE.match(url):
            url = f"https://www.tiktokv.com/share/video/{share[1]}/"
        result.setdefault(identity(url), url)
    return list(result.values())


def identity(url: str) -> str:
    post = POST_RE.match(url)
    if post:
        return post[3]
    share = SHARE_RE.match(url)
    return share[1] if share else url
