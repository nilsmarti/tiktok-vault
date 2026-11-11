"""Extract post links from TikTok JSON or text exports without fetching them."""
from __future__ import annotations

import re

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
