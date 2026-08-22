"""Stats handler - shows real EcoItachi economy statistics."""

from __future__ import annotations

import time

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from config import config
from database import users as users_db
from database import stocks as stocks_db
from database import assets as assets_db
from database.mongo import mongo
from handlers.common import safe_handler
from services import tax as tax_service
from utils.messages import stats_text
from utils.sender import reply_html

import logging
logger = logging.getLogger(__name__)

# Bot start time for uptime calculation
_BOT_START_TIME = time.time()


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def register(app: Client) -> None:
    @app.on_message(filters.command("stats"))
    @safe_handler
    async def cmd_stats(client: Client, message: Message):
        # Gather real statistics from database
        users = await users_db.count_users()
        total_wallet = await users_db.aggregate_totals("wallet")
        total_bank = await users_db.aggregate_totals("bank")
        tax_pool = await tax_service.get_pool_size()
        transactions = await mongo.db["transactions"].count_documents({})
        active_stocks = len(await stocks_db.list_active_assets())
        active_assets = await assets_db.count_assets(active_only=True)
        groups = await mongo.db["group_config"].count_documents({})
        uptime_str = _format_uptime(time.time() - _BOT_START_TIME)

        # Measure current ping
        start_time = time.time()
        sent = await message.reply("📊 Fetching stats...")
        ping_ms = (time.time() - start_time) * 1000

        text = stats_text(
            users=users,
            groups=groups,
            total_wallet=total_wallet,
            total_bank=total_bank,
            tax_pool=tax_pool,
            transactions=transactions,
            active_stocks=active_stocks,
            active_assets=active_assets,
            uptime_str=uptime_str,
            ping_ms=ping_ms,
        )

        if config.STATS_URL:
            try:
                await client.send_photo(
                    chat_id=message.chat.id,
                    photo=config.STATS_URL,
                    caption=text,
                    has_spoiler=True,
                    parse_mode=ParseMode.HTML,
                )
                await sent.delete()
                return
            except Exception as exc:
                logger.warning("stats image send failed (url=%s): %s", config.STATS_URL, exc)

        await sent.edit(text, parse_mode=ParseMode.HTML)