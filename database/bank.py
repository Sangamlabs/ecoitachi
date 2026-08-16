"""Bank data access - interest state and settings for the bank system."""

from __future__ import annotations

from typing import Any

from database.mongo import mongo

COLLECTION = "users"
BANK_SETTINGS = "bank_settings"


async def ensure_indexes() -> None:
    bank = mongo.db[BANK_SETTINGS]
    await bank.create_index("key", unique=True)


async def get_bank_settings() -> dict[str, Any]:
    """Return current bank settings merged over defaults."""
    defaults: dict[str, Any] = {
        "interest_rate": 2.0,
        "interest_interval_hours": 24,
        "withdrawal_tax_rate": 5.0,
    }
    doc = await mongo.db[BANK_SETTINGS].find_one({"key": "current"})
    if doc:
        defaults.update({k: v for k, v in doc.items() if k not in ("_id", "key")})
    return defaults


async def update_bank_settings(**changes: Any) -> None:
    await mongo.db[BANK_SETTINGS].update_one(
        {"key": "current"}, {"$set": {**changes, "key": "current"}}, upsert=True
    )


async def set_interest_rate(rate: float) -> None:
    await update_bank_settings(interest_rate=float(rate))


async def set_withdrawal_tax_rate(rate: float) -> None:
    await update_bank_settings(withdrawal_tax_rate=float(rate))


async def claim_interest(
    user_id: int, bank_balance: int, rate: float, now: int
) -> int | None:
    """Atomically claim the user's interest payout.

    Returns the interest amount paid, or None if already paid this period.
    The ``last_interest_at`` guard makes the operation idempotent.
    """
    interest = int(bank_balance * rate) // 100
    if interest <= 0:
        return None
    result = await mongo.db[COLLECTION].update_one(
        {
            "user_id": user_id,
            "$or": [{"last_interest_at": None}, {"last_interest_at": {"$lt": now}}],
        },
        {
            "$inc": {"bank": interest, "total_interest_earned": interest, "monthly_earnings": interest},
            "$set": {"last_interest_at": now},
        },
    )
    if result.modified_count == 0:
        return None
    return interest
