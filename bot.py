"""UNOITACHI Bot — entry point.

Assembles the client, database, handlers and scheduler.  Handlers register
themselves via the centralized COMMAND_REGISTRY, so new modules can be added
without touching existing code.
"""

from __future__ import annotations

import asyncio

from pyrogram import Client

from config import config
from database.mongo import mongo
from logging_setup import get_logger, setup_logging
from scheduler.jobs import build_scheduler, job_summary

logger = get_logger("bot")

# Centralized command registration: add new handler modules here.
COMMAND_REGISTRY = [
    "handlers.start",
    "handlers.economy",
    "handlers.bank",
    "handlers.stocks",
    "handlers.assets",
    "handlers.asset_admin",
    "handlers.income",
    "handlers.games",
    "handlers.emoji_games",
    "handlers.blackjack",
    "handlers.emoji_admin",
    "handlers.rewards",
    "handlers.admin",
]


async def register_handlers(app: Client) -> None:
    for module_name in COMMAND_REGISTRY:
        module = __import__(module_name, fromlist=["register"])
        module.register(app)
        logger.info("registered handlers from %s", module_name)


async def main() -> None:
    setup_logging()
    config.validate()

    await mongo.connect()
    from services import settings as settings_service
    from services import group_config as group_config_service

    await settings_service.ensure_indexes()
    await group_config_service.ensure_indexes()
    from services import assets as asset_service

    await asset_service.ensure_market()

    app = Client(
        "unoitachi_bot",
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        bot_token=config.BOT_TOKEN,
        in_memory=True,
        workers=16,
    )

    await register_handlers(app)
    scheduler = build_scheduler()

    try:
        await app.start()
        logger.info("bot started as @%s", (await app.get_me()).username)
        scheduler.start()
        logger.info(job_summary(scheduler))
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("shutting down...")
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await app.stop()
        await mongo.close()
        logger.info("bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
