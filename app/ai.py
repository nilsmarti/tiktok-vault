"""Transcription + structured extraction via OpenAI-compatible endpoints
(OpenRouter, vLLM, Ollama, speaches/faster-whisper-server, ...)."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from urllib.parse import quote_plus

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .config import Profile, Settings

log = logging.getLogger(__name__)

KINDS = ("place", "recipe", "product", "inspiration", "tutorial", "other")


class Place(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    address: str | None = None
    city: str | None = None
    country: str | None = None
    category: str | None = None  # restaurant, bar, cafe, shop, museum, ...

    @property
    def label(self) -> str:
        return ", ".join(x for x in (self.name, self.address, self.city, self.country) if x)

    @property
    def maps_url(self) -> str:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(self.label)}"


class Product(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    brand: str | None = None


class Creative(BaseModel):
    model_config = ConfigDict(extra="ignore")
    medium: str | None = None
    techniques: list[str] = []
    palette: list[str] = []


class Extraction(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = "Untitled"
    summary: str = ""
    kind: str = "other"
    tags: list[str] = []
    language: str | None = None
    places: list[Place] = []
    products: list[Product] = []
    on_screen_text: list[str] = []
    creative: Creative | None = None
    confidence: float = 0.5

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        v = str(v or "other").strip().lower()
        return v if v in KINDS else "other"

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, v):
        out = []
        for t in v or []:
            t = " ".join(str(t).replace(",", " ").split()).lstrip("#").lower()[:60]
            if t and t not in out:
                out.append(t)
        return out[:10]

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5


SCHEMA_HINT = """{
  "title": "short descriptive title, max 80 chars, in the post's language",
  "summary": "1-3 sentences: what this is and why someone saved it",
  "kind": "one of: place | recipe | product | inspiration | tutorial | other",
  "tags": ["3-8 short lowercase tags"],
  "language": "ISO 639-1 code of the post",
  "places": [{"name": "", "address": null, "city": null, "country": null, "category": "restaurant|bar|cafe|shop|hotel|attraction|other"}],
  "products": [{"name": "", "brand": null}],
  "on_screen_text": ["important text overlays, verbatim"],
  "creative": {"medium": null, "techniques": [], "palette": []},
  "confidence": 0.0
}"""

SYSTEM = f"""You catalogue saved TikTok posts into someone's personal Notion knowledge base.
You get the caption, creator, an audio transcript (may be empty or just music lyrics) and frames in chronological order.

Return ONLY a JSON object with this shape:
{SCHEMA_HINT}

Rules:
- Read the frames carefully: venue names, addresses, prices, dish names and recipe steps are often only on screen.
- Copy names and addresses verbatim. Never invent an address; use null when unknown. Infer city/country only if clearly stated or shown.
- "places" only for concrete, named venues/locations. "products" only for concretely named items.
- "creative" is for art/design/fashion/craft inspiration (medium, techniques, colour palette); null otherwise.
- "on_screen_text": keep useful info (addresses, ingredients, steps, opening hours), skip filler.
- "confidence": how sure you are about the extracted facts (0-1).
"""


def _img_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}


def _parse(content: str) -> Extraction:
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return Extraction.model_validate(json.loads(content[start:end + 1]))


async def transcribe(http: httpx.AsyncClient, s: Settings, audio: Path) -> str:
    if not s.stt_base_url:
        return ""
    r = await http.post(
        f"{s.stt_base_url.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {s.stt_api_key}"},
        files={"file": (audio.name, audio.read_bytes(), "audio/mpeg")},
        data={"model": s.stt_model, "response_format": "json"},
        timeout=600,
    )
    r.raise_for_status()
    return (r.json().get("text") or "").strip()


async def extract(http: httpx.AsyncClient, s: Settings, profile: Profile, meta: dict,
                  transcript: str, frames: list[Path]) -> Extraction:
    caption = meta.get("description") or meta.get("title") or ""
    text = (
        f"Saved by: {profile.name}. What they typically save things for: {profile.focus or 'general'}\n"
        f"Creator: @{meta.get('uploader') or meta.get('uploader_id') or '?'}\n"
        f"Caption: {caption}\n"
        f"Sound: {meta.get('track') or '-'}\n"
        f"Transcript: {transcript[:12000] or '(none)'}\n"
        f"{len(frames)} frames attached in order."
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [{"type": "text", "text": text}, *[_img_part(f) for f in frames]]},
    ]
    last_err: Exception | None = None
    for _ in range(2):
        r = await http.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {s.llm_api_key}", "X-Title": "tiktok-vault"},
            json={"model": s.llm_model, "messages": messages, "temperature": 0.2,
                  "response_format": {"type": "json_object"}},
            timeout=300,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or ""
        try:
            return _parse(content)
        except (ValueError, ValidationError) as e:
            last_err = e
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"That was not valid ({e}). Return only the JSON object."}]
    raise RuntimeError(f"extraction failed: {last_err}")


def fallback_extraction(meta: dict) -> Extraction:
    caption = (meta.get("description") or meta.get("title") or "").strip()
    tags = [w for w in caption.split() if w.startswith("#")]
    return Extraction(title=(caption[:80] or f"TikTok {meta.get('id', '')}"),
                      summary="(automatic extraction failed – caption only)", tags=tags, confidence=0)
