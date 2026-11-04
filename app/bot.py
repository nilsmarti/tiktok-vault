"""Telegram entrypoint: share a TikTok to the bot, it queues a job for the sender's profile."""
from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message
from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

from .config import get_settings

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
TIKTOK_RE = re.compile(r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/\S+", re.I)

dp = Dispatcher()


@dp.message(CommandStart())
async def start(m: Message):
    profiles = get_settings().profiles
    if m.from_user.id not in profiles:
        await m.answer(f"Not set up for you yet. Your Telegram user ID is {m.from_user.id}.")
        return
    await m.answer(f"Hi {profiles[m.from_user.id].name}! Share TikTok links here (Share → Telegram → this bot) "
                   "and I'll archive them and file them in your Notion.")


@dp.message(F.text | F.caption)
async def on_message(m: Message, arq: ArqRedis):
    profiles = get_settings().profiles
    if not m.from_user or m.from_user.id not in profiles:
        return  # silently ignore strangers
    text = m.text or m.caption or ""
    urls = list(dict.fromkeys(u.rstrip(").,!>\"'") for u in TIKTOK_RE.findall(text)))
    if not urls:
        await m.reply("Send me a TikTok link (videos and photo slideshows).")
        return
    for url in urls:
        await arq.enqueue_job("process_tiktok", m.from_user.id, m.chat.id, m.message_id, url,
                              _job_id=f"{m.from_user.id}:{url}")
    await m.reply(f"⏳ Queued {len(urls)} post{'s' if len(urls) > 1 else ''}.")


async def main():
    s = get_settings()
    dp["arq"] = await create_pool(RedisSettings.from_dsn(s.redis_url))
    await dp.start_polling(Bot(s.telegram_bot_token), allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
