from __future__ import annotations

import logging
import os

import httpx
from aiogram import Bot
from aiogram.types import ReplyParameters
from arq import Retry
from arq.connections import RedisSettings

from . import pipeline
from .config import get_settings
from .db import Store
from .fetch import PermanentError
from .storage import Storage

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("worker")
MAX_TRIES = 3


async def startup(ctx):
    s = get_settings()
    ctx["s"] = s
    ctx["bot"] = Bot(s.telegram_bot_token)
    ctx["http"] = httpx.AsyncClient(timeout=120)
    ctx["storage"] = Storage(s)
    ctx["store"] = Store(s.data_dir / "index.sqlite3")


async def shutdown(ctx):
    await ctx["http"].aclose()
    await ctx["bot"].session.close()


async def _reply(ctx, chat_id: int, message_id: int, text: str):
    await ctx["bot"].send_message(
        chat_id, text, link_preview_options={"is_disabled": True},
        reply_parameters=ReplyParameters(message_id=message_id, allow_sending_without_reply=True),
    )


def _format(r: pipeline.Result) -> str:
    if r.duplicate:
        return f"Already saved: {r.notion_url}"
    lines = [f"✅ {r.title}", f"{r.kind}" + (f" · {', '.join(r.tags[:5])}" if r.tags else "")]
    lines += [f"📍 {p}" for p in r.places[:5]]
    lines += [f"⚠️ {w}" for w in r.warnings]
    lines.append(r.notion_url)
    return "\n".join(lines)


async def process_tiktok(ctx, user_id: int, chat_id: int, message_id: int, url: str):
    s = ctx["s"]
    profile = s.profiles[user_id]
    try:
        result = await pipeline.run(s, ctx["http"], ctx["storage"], ctx["store"], profile, url)
    except PermanentError as e:
        await _reply(ctx, chat_id, message_id, f"❌ Can't save {url}\n{str(e)[:300]}")
        return
    except Exception as e:  # noqa: BLE001
        log.exception("job failed (try %s): %s", ctx["job_try"], url)
        if ctx["job_try"] < MAX_TRIES:
            raise Retry(defer=60 * ctx["job_try"]) from e
        await _reply(ctx, chat_id, message_id, f"❌ Failed after {MAX_TRIES} tries: {url}\n{type(e).__name__}: {str(e)[:300]}")
        return
    await _reply(ctx, chat_id, message_id, _format(result))


class WorkerSettings:
    functions = [process_tiktok]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    max_jobs = int(os.environ.get("WORKER_CONCURRENCY", "2"))
    job_timeout = 900
    max_tries = MAX_TRIES
