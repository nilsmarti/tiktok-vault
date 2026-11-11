"""Telegram entrypoint: share a TikTok to the bot, it queues a job for the sender's profile."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from .config import get_settings
from .imports import identity, links

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

dp = Dispatcher()


@dp.message(CommandStart())
async def start(m: Message):
    profiles = get_settings().profiles
    if m.from_user.id not in profiles:
        await m.answer(f"Not set up for you yet. Your Telegram user ID is {m.from_user.id}.")
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
    urls = links(text)
    if not urls:
        await m.reply("Send me a TikTok link (videos and photo slideshows).")
        return
    for url in urls:
        await arq.enqueue_job("process_tiktok", m.from_user.id, m.chat.id, m.message_id, url,
                              _job_id=job_id(m.from_user.id, url), _expires=30 * 86400)
    await m.reply(f"⏳ Queued {len(urls)} post{'s' if len(urls) > 1 else ''}.")


def job_id(user_id: int, url: str) -> str:
    return f"{user_id}:" + hashlib.sha256(identity(url).encode()).hexdigest()


@dp.message(F.document)
async def import_document(m: Message, bot: Bot, arq: ArqRedis):
    if not m.from_user or m.from_user.id not in get_settings().profiles:
        return
    doc = m.document
    if not doc.file_name or not doc.file_name.lower().endswith((".json", ".txt")):
        await m.reply("Send the favorites JSON or TXT file from your TikTok data export.")
        return
    if doc.file_size is None or doc.file_size > 20 * 1024 * 1024:
        await m.reply("Please send a JSON or TXT file smaller than 20 MiB.")
        return
    data = await bot.download(doc)
    try:
        urls = links(data.read().decode("utf-8-sig"))
    except UnicodeDecodeError:
        await m.reply("The import file must use UTF-8 text encoding.")
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
                          import_key, _expires=30 * 86400)
    await m.reply(f"⏳ Import accepted: {len(urls)} unique links. Results will arrive as posts finish.")


async def main():
    s = get_settings()
    dp["arq"] = await create_pool(RedisSettings.from_dsn(s.redis_url))
    await dp.start_polling(Bot(s.telegram_bot_token), allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
