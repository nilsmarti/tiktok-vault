"""Minimal Notion client: file uploads (single + multi-part), page creation, block builders."""
from __future__ import annotations

import asyncio
import datetime as dt
import math
from pathlib import Path

import httpx

from .ai import Extraction

API = "https://api.notion.com/v1"
# Pinned: keeps `database_id` parents working (2025-09-03+ switched to data sources).
NOTION_VERSION = "2022-06-28"
SINGLE_PART_MAX = 20 * 1024 * 1024
PART_SIZE = 10 * 1024 * 1024
TEXT_MAX = 1900


class Notion:
    def __init__(self, http: httpx.AsyncClient, token: str):
        self.http = http
        self.token = token
        self._limit: int | None | bool = False  # False = not fetched yet

    def _headers(self, json_body: bool = True) -> dict:
        h = {"Authorization": f"Bearer {self.token}", "Notion-Version": NOTION_VERSION}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    async def _req(self, method: str, path: str, *, json_body: bool = True, **kw) -> dict:
        for attempt in range(5):
            r = await self.http.request(method, f"{API}{path}", headers=self._headers(json_body),
                                        timeout=300, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(float(r.headers.get("Retry-After", 2 ** attempt)))
                continue
            if r.is_error:
                raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:500]}")
            return r.json()
        r.raise_for_status()
        return r.json()

    async def max_upload_bytes(self) -> int | None:
        if self._limit is False:
            me = await self._req("GET", "/users/me")
            self._limit = (me.get("bot", {}).get("workspace_limits", {}) or {}).get("max_file_upload_size_in_bytes")
        return self._limit  # type: ignore[return-value]

    async def upload(self, path: Path, content_type: str) -> str:
        size = path.stat().st_size
        if size <= SINGLE_PART_MAX:
            fu = await self._req("POST", "/file_uploads", json={"filename": path.name, "content_type": content_type})
            await self._req("POST", f"/file_uploads/{fu['id']}/send", json_body=False,
                            files={"file": (path.name, path.read_bytes(), content_type)})
            return fu["id"]
        parts = math.ceil(size / PART_SIZE)
        fu = await self._req("POST", "/file_uploads", json={
            "mode": "multi_part", "number_of_parts": parts,
            "filename": path.name, "content_type": content_type})
        with path.open("rb") as f:
            for n in range(1, parts + 1):
                await self._req("POST", f"/file_uploads/{fu['id']}/send", json_body=False,
                                files={"file": (path.name, f.read(PART_SIZE), content_type)},
                                data={"part_number": str(n)})
        await self._req("POST", f"/file_uploads/{fu['id']}/complete", json={})
        return fu["id"]

    async def create_page(self, database_id: str, properties: dict, children: list[dict],
                          cover_upload_id: str | None = None) -> dict:
        body: dict = {"parent": {"database_id": database_id}, "properties": properties,
                      "children": children[:100]}
        if cover_upload_id:
            body["cover"] = {"type": "file_upload", "file_upload": {"id": cover_upload_id}}
        page = await self._req("POST", "/pages", json=body)
        for i in range(100, len(children), 100):
            await self._req("PATCH", f"/blocks/{page['id']}/children", json={"children": children[i:i + 100]})
        return page


# ---------- block / property builders ----------

def rt(text: str | None, link: str | None = None) -> list[dict]:
    text = text or ""
    chunks = [text[i:i + TEXT_MAX] for i in range(0, len(text), TEXT_MAX)]
    return [{"type": "text", "text": {"content": c, **({"link": {"url": link}} if link else {})}} for c in chunks]


def _block(kind: str, payload: dict) -> dict:
    return {"object": "block", "type": kind, kind: payload}


def paragraph(text: str, link: str | None = None) -> dict:
    return _block("paragraph", {"rich_text": rt(text, link)})


def bullet(text: str, link: str | None = None) -> dict:
    return _block("bulleted_list_item", {"rich_text": rt(text, link)})


def heading(text: str) -> dict:
    return _block("heading_3", {"rich_text": rt(text)})


def toggle(title: str, children: list[dict]) -> dict:
    return _block("toggle", {"rich_text": rt(title), "children": children[:100]})


def media_block(kind: str, *, upload_id: str | None = None, url: str | None = None) -> dict:
    if upload_id:
        return _block(kind, {"type": "file_upload", "file_upload": {"id": upload_id}})
    return _block(kind, {"type": "external", "external": {"url": url}})


def build_properties(ex: Extraction, meta: dict, *, is_slideshow: bool, archive_uri: str) -> dict:
    creator = meta.get("uploader") or meta.get("uploader_id") or ""
    posted = None
    if (d := meta.get("upload_date")) and len(d) == 8:
        posted = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    props = {
        "Name": {"title": rt(ex.title[:200])},
        "URL": {"url": meta.get("webpage_url")},
        "Creator": {"rich_text": rt(f"@{creator}" if creator else "")},
        "Kind": {"select": {"name": ex.kind}},
        "Tags": {"multi_select": [{"name": t} for t in ex.tags]},
        "Summary": {"rich_text": rt(ex.summary)},
        "Places": {"rich_text": rt("; ".join(p.label for p in ex.places))},
        "Map": {"url": ex.places[0].maps_url if ex.places else None},
        "Saved": {"date": {"start": dt.date.today().isoformat()}},
        "Status": {"select": {"name": "Inbox"}},
        "Format": {"select": {"name": "Slideshow" if is_slideshow else "Video"}},
        "Confidence": {"number": round(ex.confidence, 2)},
        "Archive": {"rich_text": rt(archive_uri)},
    }
    if posted:
        props["Posted"] = {"date": {"start": posted}}
    return props


def build_body(ex: Extraction, meta: dict, transcript: str, media_blocks: list[dict]) -> list[dict]:
    blocks: list[dict] = list(media_blocks)
    caption = meta.get("description") or ""
    if caption:
        blocks.append(_block("quote", {"rich_text": rt(caption)}))
    if ex.summary:
        blocks.append(paragraph(ex.summary))
    if ex.places:
        blocks.append(heading("Places"))
        blocks += [bullet(p.label, p.maps_url) for p in ex.places]
    if ex.products:
        blocks.append(heading("Products"))
        blocks += [bullet(f"{p.brand} – {p.name}" if p.brand else p.name) for p in ex.products]
    c = ex.creative
    if c and (c.medium or c.techniques or c.palette):
        blocks.append(heading("Creative notes"))
        if c.medium:
            blocks.append(bullet(f"Medium: {c.medium}"))
        if c.techniques:
            blocks.append(bullet(f"Techniques: {', '.join(c.techniques)}"))
        if c.palette:
            blocks.append(bullet(f"Palette: {', '.join(c.palette)}"))
    if ex.on_screen_text:
        blocks.append(toggle("On-screen text", [bullet(t) for t in ex.on_screen_text]))
    if transcript:
        paras = [transcript[i:i + TEXT_MAX] for i in range(0, len(transcript), TEXT_MAX)]
        blocks.append(toggle("Transcript", [paragraph(p) for p in paras]))
    if url := meta.get("webpage_url"):
        blocks.append(paragraph("Original post", url))
    return blocks
