"""Telegram entrypoint: share a TikTok to the bot, it queues a job for the sender's profile."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from .config import get_settings
from .imports import collection_records, identity, links
from .bookmarks import parse_bookmark

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

dp = Dispatcher()


@dp.message(CommandStart())
async def start(m: Message):
    profiles = get_settings().profiles
    if m.from_user.id not in profiles:
        await m.answer(f"Not set up for you yet. Your Telegram user ID is {m.from_user.id}.")
        return
    if profiles[m.from_user.id].notion_mode == "moodboard":
        await m.answer("Send your shortcut message with selected_collection:, link:, and optional tags:. "
                       "Or send your sorted collection JSON to import it. "
                       "I download the TikTok myself; attachements_link is ignored.")
        return
    await m.answer(f"Hi {profiles[m.from_user.id].name}! Share TikTok links here (Share → Telegram → this bot) "
                   "and I'll archive them and file them in your Notion. "
                   "You can also send a favorites JSON or TXT export to import links.")


@dp.message((F.text | F.caption) & ~F.document)
async def on_message(m: Message, arq: ArqRedis):
    profiles = get_settings().profiles
    if not m.from_user or m.from_user.id not in profiles:
        return  # silently ignore strangers
    text = m.text or m.caption or ""
    if profiles[m.from_user.id].notion_mode == "moodboard":
        try:
            bookmark = parse_bookmark(text)
        except ValueError as exc:
            await m.reply(f"Can't queue this bookmark: {exc}")
            return
        job = await arq.enqueue_job("process_tiktok", m.from_user.id, m.chat.id, m.message_id, bookmark.url,
                              bookmark=bookmark.context(), _job_id=job_id(m.from_user.id, bookmark.url, m.message_id),
                              _expires=30 * 86400)
        await m.reply("⏳ Queued 1 bookmark." if job else
                      "This message is already queued or processed. Send it as a new message to check its saved page.")
        return
    urls = links(text)
    if not urls:
        await m.reply("Send me a TikTok link (videos and photo slideshows).")
        return
    queued = 0
    for url in urls:
        job = await arq.enqueue_job("process_tiktok", m.from_user.id, m.chat.id, m.message_id, url,
                              _job_id=job_id(m.from_user.id, url, m.message_id), _expires=30 * 86400)
        queued += int(job is not None)
    await m.reply(f"⏳ Queued {queued} post{'s' if queued != 1 else ''}." if queued else
                  "This message is already queued or processed. Send it as a new message to check its saved pages.")


def job_id(user_id: int, url: str, message_id: int) -> str:
    # Redis retains completed jobs for an hour. Scope this delivery ID to the
    # Telegram message so re-sharing a saved post can reach durable dedupe and
    # return its Notion link. Re-delivery of the same message is still suppressed.
    return f"{user_id}:{message_id}:" + hashlib.sha256(identity(url).encode()).hexdigest()


@dp.message(F.document)
async def import_document(m: Message, bot: Bot, arq: ArqRedis):
    if not m.from_user or m.from_user.id not in get_settings().profiles:
        return
    strict = get_settings().profiles[m.from_user.id].notion_mode == "moodboard"
    doc = m.document
    if not doc.file_name or not doc.file_name.lower().endswith((".json", ".txt")):
        await m.reply("Send the favorites JSON or TXT file from your TikTok data export.")
        return
    if doc.file_size is None or doc.file_size > 20 * 1024 * 1024:
        await m.reply("Please send a JSON or TXT file smaller than 20 MiB.")
        return
    data = await bot.download(doc)
    try:
        text = data.read().decode("utf-8-sig")
        urls = ([json.dumps(row, ensure_ascii=False) for row in collection_records(text)]
                if strict else links(text))
    except UnicodeDecodeError:
        await m.reply("The import file must use UTF-8 text encoding.")
        return
    except ValueError as exc:
        await m.reply(f"Can't import this collection JSON: {str(exc)[:500]}")
        return
    if not urls:
        await m.reply("No TikTok links found in this file.")
        return
    import_key = "favorites:" + uuid.uuid4().hex
    async with arq.pipeline(transaction=True) as pipe:
        for offset in range(0, len(urls), 1000):
            pipe.rpush(import_key, *urls[offset:offset + 1000])
        pipe.expire(import_key, 30 * 86400)
        await pipe.execute()
    await arq.enqueue_job("import_favorites", m.from_user.id, m.chat.id, m.message_id,
                          import_key, structured=strict, _expires=30 * 86400)
    await m.reply(f"⏳ Import accepted: {len(urls)} unique links. Results will arrive as posts finish.")


async def main():
    s = get_settings()
    dp["arq"] = await create_pool(RedisSettings.from_dsn(s.redis_url))
    await dp.start_polling(Bot(s.telegram_bot_token), allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
