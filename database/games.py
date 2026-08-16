"""Game session and cooldown data access layer.

Active game sessions live in MongoDB so a bot restart cannot corrupt or
duplicate in-flight economy games.
"""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

SESSIONS = "game_sessions"
COOLDOWNS = "game_cooldowns"


async def ensure_indexes() -> None:
    sessions = mongo.db[SESSIONS]
    await sessions.create_index("game_id", unique=True)
    await sessions.create_index("user_id")
    await sessions.create_index([("game", 1), ("status", 1)])
    cooldowns = mongo.db[COOLDOWNS]
    await cooldowns.create_index([("game", 1), ("user_id", 1)], unique=True)
    await cooldowns.create_index("expires_at", expireAfterSeconds=0)


async def insert_session(doc: dict[str, Any]) -> None:
    await mongo.db[SESSIONS].insert_one(doc)


async def bind_message(game_id: str, message_id: int) -> None:
    """Attach the inline board's message id to a session (set after sending)."""
    await mongo.db[SESSIONS].update_one(
        {"game_id": game_id}, {"$set": {"message_id": message_id}}
    )


async def get_session(game_id: str) -> dict[str, Any] | None:
    return await mongo.db[SESSIONS].find_one({"game_id": game_id})


async def get_active_session(user_id: int, game: str) -> dict[str, Any] | None:
    return await mongo.db[SESSIONS].find_one(
        {"user_id": user_id, "game": game, "status": "active"}
    )


async def settle_session(
    game_id: str, outcome: str, payout: int, meta: dict[str, Any] | None = None
) -> bool:
    """Atomically mark a session settled.

    Returns False if the session was already settled (prevents double payout).
    """
    result = await mongo.db[SESSIONS].update_one(
        {"game_id": game_id, "status": "active"},
        {
            "$set": {
                "status": outcome,
                "payout": payout,
                "settled_at": int(time.time()),
                "meta": meta or {},
            }
        },
    )
    return result.modified_count == 1


async def find_expired_games(game: str, max_age: int) -> list[dict[str, Any]]:
    cutoff = int(time.time()) - max_age
    cursor = mongo.db[SESSIONS].find(
        {"game": game, "status": "active", "created_at": {"$lt": cutoff}}
    )
    return [doc async for doc in cursor]


async def get_cooldown(game: str, user_id: int) -> dict[str, Any] | None:
    return await mongo.db[COOLDOWNS].find_one({"game": game, "user_id": user_id})


async def set_cooldown(game: str, user_id: int, duration: int) -> None:
    """Set or refresh a cooldown; TTL index removes expired entries."""
    expires_at = int(time.time()) + duration
    await mongo.db[COOLDOWNS].update_one(
        {"game": game, "user_id": user_id},
        {"$set": {"expires_at": expires_at, "duration": duration}},
        upsert=True,
    )


async def clear_cooldown(game: str, user_id: int) -> None:
    await mongo.db[COOLDOWNS].delete_one({"game": game, "user_id": user_id})
