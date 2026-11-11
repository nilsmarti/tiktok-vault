from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

VIDEO_MODES = {"auto", "upload", "external"}


@dataclass(frozen=True)
class Profile:
    telegram_user_id: int
    name: str  # slug, used as S3 prefix
    notion_token: str
    notion_database_id: str
    notion_properties: dict[str, str] = field(default_factory=dict)
    focus: str = ""  # hint for the LLM: what this person saves stuff for
    video_mode: str = "auto"  # auto | upload (into Notion) | external (public S3 URL)


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    redis_url: str
    data_dir: Path
    # S3-compatible storage
    s3_endpoint: str
    s3_bucket: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str
    public_media_base_url: str | None  # e.g. https://media.example.ch/tiktok-vault
    # Vision/extraction LLM (OpenAI-compatible: OpenRouter, vLLM, Ollama, ...)
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    # Speech-to-text (OpenAI-compatible /audio/transcriptions). Empty = skip.
    stt_base_url: str | None
    stt_api_key: str
    stt_model: str
    tiktok_cookies_file: str | None
    max_frames: int
    profiles: dict[int, Profile]


def _env(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name) or default
    if required and not val:
        raise RuntimeError(f"Missing required env var {name}")
    return val


def _load_profiles(path: Path) -> dict[int, Profile]:
    raw = yaml.safe_load(os.path.expandvars(path.read_text())) or {}
    profiles: dict[int, Profile] = {}
    for name, p in (raw.get("profiles") or {}).items():
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise ValueError(f"profile name {name!r} must be a lowercase slug")
        for key in ("telegram_user_id", "notion_token", "notion_database_id"):
            val = str(p.get(key, ""))
            if not val or val.startswith("$"):
                raise ValueError(f"profile {name}: {key} missing or env var not set")
        mode = p.get("video_mode", "auto")
        if mode not in VIDEO_MODES:
            raise ValueError(f"profile {name}: video_mode must be one of {VIDEO_MODES}")
        uid = int(p["telegram_user_id"])
        if uid in profiles:
            raise ValueError(f"duplicate telegram_user_id: {uid}")
        mapping = p.get("notion_properties", {})
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValueError(f"profile {name}: notion_properties must map property names to strings")
        profiles[uid] = Profile(
            telegram_user_id=uid,
            name=name,
            notion_token=p["notion_token"],
            notion_database_id=p["notion_database_id"].replace("-", ""),
            notion_properties=mapping,
            focus=p.get("focus", ""),
            video_mode=mode,
        )
    if not profiles:
        raise ValueError(f"no profiles defined in {path}")
    return profiles


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", required=True),
        redis_url=_env("REDIS_URL", "redis://redis:6379/0"),
        data_dir=data_dir,
        s3_endpoint=_env("S3_ENDPOINT", required=True),
        s3_bucket=_env("S3_BUCKET", "tiktok-vault"),
        s3_access_key=_env("S3_ACCESS_KEY", required=True),
        s3_secret_key=_env("S3_SECRET_KEY", required=True),
        s3_region=_env("S3_REGION", "garage"),
        public_media_base_url=_env("PUBLIC_MEDIA_BASE_URL"),
        llm_base_url=_env("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
        llm_api_key=_env("LLM_API_KEY", required=True),
        llm_model=_env("LLM_MODEL", "google/gemini-2.5-flash"),
        stt_base_url=_env("STT_BASE_URL"),
        stt_api_key=_env("STT_API_KEY", "none"),
        stt_model=_env("STT_MODEL", "Systran/faster-whisper-large-v3"),
        tiktok_cookies_file=_env("TIKTOK_COOKIES_FILE"),
        max_frames=int(_env("MAX_FRAMES", "10")),
        profiles=_load_profiles(Path(_env("PROFILES_FILE", "/config/profiles.yaml"))),
    )
