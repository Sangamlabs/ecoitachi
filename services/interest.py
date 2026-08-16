"""Bank interest processing.

Runs on a scheduler.  Every eligible user whose ``last_interest_at`` is older
than the configured interval receives interest once (atomic, idempotent claim).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from database import bank as bank_db, users as users_db
from database.mongo import mongo
from services import transaction as tx_service

logger = logging.getLogger(__name__)


async def process_due_interest(now: int | None = None) -> list[dict[str, Any]]:
    """Pay interest to every user whose 24h window has elapsed.

    Returns a list of ``{user_id, amount}`` payouts performed.
    """
    now = int(time.time()) if now is None else int(now)
    settings = await bank_db.get_bank_settings()
    rate = float(settings.get("interest_rate", 2.0))
    interval_hours = int(settings.get("interest_interval_hours", 24))
    if rate <= 0:
        return []

    cutoff = now - interval_hours * 3600
    cursor = mongo.db[users_db.COLLECTION].find(
        {
            "bank": {"$gt": 0},
            "$or": [
                {"last_interest_at": None},
                {"last_interest_at": {"$lte": cutoff}},
            ],
        },
        {"user_id": 1, "bank": 1, "wallet": 1},
    )
    paid: list[dict[str, Any]] = []
    async for user in cursor:
        interest = await bank_db.claim_interest(user["user_id"], user.get("bank", 0), rate, now)
        if interest:
            await tx_service.record(
                user_id=user["user_id"],
                ttype=tx_service.INTEREST,
                amount=interest,
                balance_before=user.get("wallet", 0),
                balance_after=user.get("wallet", 0),
                metadata={"bank_before": user.get("bank", 0)},
            )
            paid.append({"user_id": user["user_id"], "amount": interest})

    if paid:
        logger.info("interest paid to %s users (rate=%s%%)", len(paid), rate)
    return paid
