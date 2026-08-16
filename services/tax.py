"""Tax pool and monthly tax distribution service.

Collected withdrawal taxes accumulate in a pool.  At month end the pool is
distributed to the monthly Top-10 earners using percentages stored in the
settings collection (not hardcoded).  Distribution is idempotent.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from database import users as users_db
from database.mongo import mongo
from services import settings as settings_service, transaction as tx_service

logger = logging.getLogger(__name__)

POOL_COLLECTION = "tax_pool"
DIST_COLLECTION = "tax_distributions"


async def get_pool_size() -> int:
    doc = await mongo.db[POOL_COLLECTION].find_one({"key": "pool"})
    return int(doc.get("balance", 0)) if doc else 0


async def collect(user_id: int, amount: int) -> None:
    """Add tax to the pool with an audit trail."""
    if amount <= 0:
        return
    await mongo.db[POOL_COLLECTION].update_one(
        {"key": "pool"}, {"$inc": {"balance": amount}}, upsert=True
    )
    logger.info("tax collected: user=%s amount=%s", user_id, amount)


async def get_system_tax(system: str) -> float:
    """Return the configured tax rate (percent) for a transaction system."""
    config = await settings_service.get_system_taxes()
    return float(config.get(system, 0.0))


async def system_tax_amount(system: str, gross: int) -> int:
    """Compute the tax to collect for a system transaction of ``gross``."""
    return int(gross * await get_system_tax(system)) // 100


async def _month_key(ts: int) -> str:
    return time.strftime("%Y-%m", time.gmtime(ts))


async def already_distributed(month: str) -> bool:
    return (
        await mongo.db[DIST_COLLECTION].find_one({"month": month}, {"_id": 0, "month": 1})
        is not None
    )


async def distribute_monthly(now: int | None = None) -> dict[str, Any] | None:
    """Run the end-of-month tax pool distribution. Idempotent per month."""
    now = int(time.time()) if now is None else int(now)
    month = await _month_key(now - 1)  # distribute the month that just ended
    if await already_distributed(month):
        logger.info("tax distribution for %s already done; skipping", month)
        return None
    return await _distribute(now, month, manual=False)


async def distribute_manual(now: int | None = None) -> dict[str, Any] | None:
    """Manually distribute the current tax pool to the monthly Top-10 earners.

    Unlike :func:`distribute_monthly`, a manual run is not blocked by the
    monthly idempotency guard and does not mark the month as distributed, so
    the automatic month-end distribution still runs.  Every manual run is
    audited under its own unique key.
    """
    now = int(time.time()) if now is None else int(now)
    label = f"manual-{now}"
    return await _distribute(now, label, manual=True)


async def _distribute(now: int, label: str, manual: bool) -> dict[str, Any] | None:
    """Share the pool across the monthly Top-10 earners. Shared by both the
    automatic month-end job and the manual /dtax command."""
    config = await settings_service.get_tax_distribution()
    if not config.get("enabled"):
        await mongo.db[DIST_COLLECTION].insert_one(
            {"month": label, "pool": 0, "distributed": False, "manual": manual,
             "reason": "disabled", "at": now}
        )
        return None
    percentages = config.get("percentages", [])
    pool = await get_pool_size()
    if pool <= 0 or not percentages:
        await mongo.db[DIST_COLLECTION].insert_one(
            {"month": label, "pool": 0, "distributed": False, "manual": manual,
             "reason": "empty_pool", "at": now}
        )
        return None

    # Monthly Top-10 by monthly_earnings (excluding banned users).
    cursor = (
        mongo.db[users_db.COLLECTION]
        .find({"is_banned": False})
        .sort([("monthly_earnings", -1)])
        .limit(10)
    )
    top = [doc async for doc in cursor]
    if not top:
        return None

    # Compute the shared "rank" percentages; top index 0 = rank 1.
    total_percent = sum(percentages)
    results = []
    for idx, user_doc in enumerate(top[: len(percentages)]):
        share = int(pool * percentages[idx] / 100) if total_percent else 0
        if share <= 0:
            continue
        await users_db.inc(user_doc["user_id"], {"wallet": share, "total_earned": share, "monthly_earnings": share})
        await tx_service.record(
            user_id=user_doc["user_id"],
            ttype=tx_service.TAX_REWARD,
            amount=share,
            balance_before=user_doc.get("wallet", 0),
            balance_after=user_doc.get("wallet", 0) + share,
            metadata={"rank": idx + 1, "month": label, "manual": manual},
        )
        results.append({"rank": idx + 1, "user_id": user_doc["user_id"], "amount": share})

    distributed = sum(r["amount"] for r in results)
    await mongo.db[POOL_COLLECTION].update_one(
        {"key": "pool"}, {"$set": {"balance": pool - distributed}}
    )
    await mongo.db[DIST_COLLECTION].insert_one(
        {
            "month": label,
            "pool": pool,
            "distributed": distributed,
            "manual": manual,
            "results": results,
            "at": now,
        }
    )
    logger.info(
        "tax distribution %s: pool=%s distributed=%s recipients=%s manual=%s",
        label,
        pool,
        distributed,
        len(results),
        manual,
    )
    return {"month": label, "pool": pool, "distributed": distributed, "results": results, "manual": manual}
