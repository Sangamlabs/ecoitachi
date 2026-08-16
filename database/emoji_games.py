"""Emoji game (single + duel) session data access layer.

Sessions persist in MongoDB so a bot restart cannot lose locked bets or
re-pay a settled duel.  Settlement transitions are atomic (``status`` guard)
so a session can never pay twice.
"""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

SESSIONS = "emoji_game_sessions"


async def ensure_indexes() -> None:
    sessions = mongo.db[SESSIONS]
    await sessions.create_index("session_id", unique=True)
    await sessions.create_index(
        "game_id", unique=True, partialFilterExpression={"mode": "duel"}
    )
    await sessions.create_index("player1_id")
    await sessions.create_index("player2_id")
    await sessions.create_index([("mode", 1), ("status", 1)])


async def insert_session(doc: dict[str, Any]) -> None:
    await mongo.db[SESSIONS].insert_one(doc)


async def get_session(session_id: str) -> dict[str, Any] | None:
    return await mongo.db[SESSIONS].find_one({"session_id": session_id})


async def get_duel(game_id: str) -> dict[str, Any] | None:
    return await mongo.db[SESSIONS].find_one({"mode": "duel", "game_id": game_id})


async def find_active(user_id: int, mode: str | None = None) -> dict[str, Any] | None:
    """Return an active session the user is part of (as p1 or p2)."""
    query: dict[str, Any] = {
        "status": {"$in": ["waiting", "active"]},
        "$or": [{"player1_id": user_id}, {"player2_id": user_id}],
    }
    if mode:
        query["mode"] = mode
    return await mongo.db[SESSIONS].find_one(query)


async def set_message(session_id: str, message_id: int) -> None:
    await mongo.db[SESSIONS].update_one(
        {"session_id": session_id}, {"$set": {"message_id": message_id}}
    )


async def settle_single(
    session_id: str, *, outcome: str, payout: int, profit: int, player_result: int
) -> bool:
    """Atomically mark a single-player session settled (active -> settled)."""
    result = await mongo.db[SESSIONS].update_one(
        {"session_id": session_id, "status": "active"},
        {
            "$set": {
                "status": "settled",
                "outcome": outcome,
                "payout": payout,
                "profit": profit,
                "player1_result": player_result,
                "settled_at": int(time.time()),
                "completed_at": int(time.time()),
            }
        },
    )
    return result.modified_count == 1


async def settle_duel(
    session_id: str,
    *,
    player1_result: int,
    player2_result: int,
    winner_id: int | None,
    loser_id: int | None,
    outcome: str,
    payout: int,
    profit: int,
) -> bool:
    """Atomically mark a duel session settled (active -> settled)."""
    result = await mongo.db[SESSIONS].update_one(
        {"session_id": session_id, "status": "active"},
        {
            "$set": {
                "status": "settled",
                "player1_result": player1_result,
                "player2_result": player2_result,
                "winner_id": winner_id,
                "loser_id": loser_id,
                "outcome": outcome,
                "payout": payout,
                "profit": profit,
                "settled_at": int(time.time()),
                "completed_at": int(time.time()),
            }
        },
    )
    return result.modified_count == 1


async def try_join(game_id: str, player2: dict[str, Any]) -> dict[str, Any] | None:
    """Atomically transition a waiting duel to active on join.

    Guards against double-join, self-join and expired lobbies.  Returns the
    updated session, or None when the lobby cannot be joined.
    """
    now = int(time.time())
    result = await mongo.db[SESSIONS].update_one(
        {
            "mode": "duel",
            "game_id": game_id,
            "status": "waiting",
            "player1_id": {"$ne": player2["player2_id"]},
            "expires_at": {"$gt": now},
        },
        {
            "$set": {
                "status": "active",
                "player2_id": player2["player2_id"],
                "player2_username": player2.get("player2_username"),
                "player2_name": player2.get("player2_name", "Player 2"),
                "joined_at": now,
                "started_at": now,
            }
        },
    )
    if result.modified_count != 1:
        return None
    return await get_duel(game_id)


async def mark_expired(session_id: str) -> bool:
    """Atomically mark a waiting duel expired (guards double-refund)."""
    result = await mongo.db[SESSIONS].update_one(
        {"session_id": session_id, "status": "waiting"},
        {
            "$set": {
                "status": "expired",
                "outcome": "expired",
                "completed_at": int(time.time()),
            }
        },
    )
    return result.modified_count == 1


async def find_expired_duels() -> list[dict[str, Any]]:
    now = int(time.time())
    cursor = mongo.db[SESSIONS].find(
        {"mode": "duel", "status": "waiting", "expires_at": {"$lte": now}}
    )
    return [doc async for doc in cursor]


async def count_active_duels() -> int:
    return await mongo.db[SESSIONS].count_documents(
        {"mode": "duel", "status": {"$in": ["waiting", "active"]}}
    )
