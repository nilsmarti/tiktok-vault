"""Parse Android shortcut messages without ever using supplied attachment URLs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .imports import links
from .moodboard import COLLECTIONS

FIELDS = re.compile(r"^\s*(selected_collection|link|attachements_link|attachments_link|tags)\s*:\s*(.*)$", re.I)


@dataclass(frozen=True)
class Bookmark:
    url: str
    collections: list[str]
    tags: list[str]

    def context(self) -> dict:
        return {"collections": self.collections, "tags": self.tags}


def _values(text: str) -> list[str]:
    text = text.strip()
    if not text or text == "[]":
        return []
    if text.startswith("[") and not re.match(r"^\[\d+\]\s*:", text):
        try:
            values = json.loads(text)
        except ValueError:
            raise ValueError('Use a plain name, numbered entries, or a JSON list of names') from None
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError("Expected a list of names")
    else:
        values = []
        for line in text.splitlines():
            value = re.sub(r"^\s*\[\d+\]\s*:\s*", "", line).strip()
            if value:
                values.append(value)
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def parse_bookmark(text: str) -> Bookmark:
    fields: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        match = FIELDS.match(line)
        if match:
            current = match[1].lower()
            if current in fields:
                raise ValueError(f"Duplicate field: {current}")
            fields[current] = [match[2]]
        elif current:
            fields[current].append(line)
        elif line.strip():
            raise ValueError("Start the message with selected_collection:")
    collections = _values("\n".join(fields.get("selected_collection", [])))
    if not collections or any(name not in COLLECTIONS for name in collections):
        raise ValueError("selected_collection must contain exact existing collection names")
    source = "\n".join(fields.get("link", [])).strip()
    urls = links(source)
    # A shortcut represents one source post; never silently split it into pages.
    if len(urls) != 1 or len(source.split()) != 1:
        raise ValueError("link must contain exactly one TikTok URL")
    parsed = urlsplit(source)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        raise ValueError("link must use HTTPS")
    if parsed.username or not any(host == d or host.endswith("." + d) for d in ("tiktok.com", "tiktokv.com")):
        raise ValueError("link must be a TikTok URL")
    return Bookmark(urls[0], collections, _values("\n".join(fields.get("tags", []))))
