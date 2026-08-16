"""Cooldown system.

Durations are read from the centralized settings collection (configurable by
admins).  The per-user state is tracked in memory for speed; durations and
expiry live in MongoDB-backed storage with a TTL index so restarts do not
leak stale cooldowns forever.
"""

from __future__ import annotations

import logging
import time

from database import games as games_db

logger = logging.getLogger(__name__)


class CooldownManager:
    """Tracks cooldown state per (game, user) using MongoDB state + in-memory cache."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], int] = {}

    async def check(self, game: str, user_id: int) -> int:
        """Return remaining seconds, or 0 when no cooldown is active."""
        cached = self._cache.get((game, user_id), 0)
        if cached:
            remaining = cached - time.time()
            if remaining > 0:
                return int(remaining)
            self._cache.pop((game, user_id), None)

        doc = await games_db.get_cooldown(game, user_id)
        if doc:
            remaining = doc.get("expires_at", 0) - time.time()
            if remaining > 0:
                self._cache[(game, user_id)] = doc["expires_at"]
                return int(remaining)
            await games_db.clear_cooldown(game, user_id)
        return 0

    async def apply(self, game: str, user_id: int, duration: int) -> None:
        """Start or refresh the cooldown for ``duration`` seconds."""
        expires_at = int(time.time()) + duration
        await games_db.set_cooldown(game, user_id, duration)
        self._cache[(game, user_id)] = expires_at

    async def clear(self, game: str, user_id: int) -> None:
        self._cache.pop((game, user_id), None)
        await games_db.clear_cooldown(game, user_id)


cooldown_manager = CooldownManager()
