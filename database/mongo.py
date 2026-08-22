"""Async MongoDB connection manager (Motor) and index bootstrap."""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient

from config import config

logger = logging.getLogger(__name__)


class Mongo:
    """Thin async MongoDB connection wrapper.

    The single ``db`` attribute is shared by every data-access module.
    """

    def __init__(self) -> None:
        self._client: AsyncIOMotorClient | None = None
        self.db = None  # type: ignore[assignment]

    async def connect(self) -> None:
        """Create the client and build indexes."""
        self._client = AsyncIOMotorClient(
            config.MONGO_URI,
            tz_aware=True,
            serverSelectionTimeoutMS=5000,
        )
        self.db = self._client[config.MONGO_DB_NAME]
        await self.db.command("ping")
        await self._ensure_indexes()
        logger.info("Connected to MongoDB database '%s'", config.MONGO_DB_NAME)

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()
            logger.info("MongoDB connection closed")

    async def _ensure_indexes(self) -> None:
        from database import (
            admins,
            asset_holdings,
            asset_listings,
            assets,
            bank,
            emoji_games,
            games,
            global_battle,
            loans,
            promos,
            stocks,
            transactions,
            users,
        )

        await users.ensure_indexes()
        await transactions.ensure_indexes()
        await admins.ensure_indexes()
        await stocks.ensure_indexes()
        await assets.ensure_indexes()
        await asset_holdings.ensure_indexes()
        await asset_listings.ensure_indexes()
        await games.ensure_indexes()
        await emoji_games.ensure_indexes()
        await bank.ensure_indexes()
        await loans.ensure_indexes()
        await promos.ensure_indexes()
        await global_battle.ensure_indexes()

        from services import broadcast as broadcast_service

        await broadcast_service.ensure_indexes()


mongo = Mongo()
