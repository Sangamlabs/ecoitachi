"""Daily income claim data access.

Each user tracks a ``<source>_income_last_at`` timestamp per income source.
Income is computed lazily on claim: the number of full 24h windows elapsed
since the last claim is multiplied by the daily rate.  The guarded update
below makes the claim idempotent under concurrent requests.
"""

from __future__ import annotations

from database import users as users_db
from database.mongo import mongo


async def advance_last_claim(user_id: int, field: str, expected: int, now: int) -> bool:
    """Atomically move ``field`` from ``expected`` to ``now``.

    Returns False when another request already claimed (the timestamp moved),
    which signals the caller that the payout must be skipped.
    """
    result = await mongo.db[users_db.COLLECTION].update_one(
        {"user_id": user_id, field: expected},
        {"$set": {field: now, "updated_at": now}},
    )
    return result.modified_count == 1
