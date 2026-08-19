"""Leaderboard service.

Modular: categories are registered here so future leaderboards (e.g. monthly
earnings, total spent) can be added without touching handlers.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from database import users as users_db
from database import stocks as stocks_db
from database.mongo import mongo
from utils.money import multiply

logger = logging.getLogger(__name__)

Category = Callable[..., Coroutine[Any, Any, int]]
_registry: dict[str, Category] = {}


def register(name: str, func: Category) -> None:
    _registry[name] = func


def categories() -> list[str]:
    return list(_registry)


async def net_worth(user: dict[str, Any]) -> int:
    """Wallet + Bank + live stock value + live asset value − loan debt."""
    from database import loans as loans_db

    live_stocks = 0
    holdings = await stocks_db.get_user_holdings(user["user_id"])
    for h in holdings:
        asset = await stocks_db.get_asset(h["symbol"])
        if asset:
            live_stocks += multiply(int(asset.get("price", 0)), h["quantity"])
    from services import assets as asset_service

    live_assets = await asset_service.live_asset_value(user["user_id"])
    debt = await loans_db.get_outstanding(user["user_id"])
    return (
        int(user.get("wallet", 0))
        + int(user.get("bank", 0))
        + live_stocks
        + live_assets
        - debt
    )


async def monthly_earnings(user: dict[str, Any]) -> int:
    return int(user.get("monthly_earnings", 0))


register("net_worth", net_worth)
register("monthly_earnings", monthly_earnings)


# Users excluded from every leaderboard (set with /leaderban).
_ELIGIBLE = {"is_banned": False, "leaderboard_excluded": {"$ne": True}}


async def top_net_worth(limit: int = 10) -> list[dict[str, Any]]:
    """Return the top-N users by net worth with live stock valuation.

    Computing live stock value for every user is expensive, so we first get a
    broad pool ordered by (wallet + bank) and only value their stocks live.
    """
    cursor = (
        mongo.db[users_db.COLLECTION]
        .find(dict(_ELIGIBLE))
        .sort([("wallet", -1)])
        .limit(limit * 5)
    )
    candidates = [doc async for doc in cursor]
    valued = [(await net_worth(u), u) for u in candidates]
    valued.sort(key=lambda pair: pair[0], reverse=True)
    return [u for _, u in valued[:limit]]


async def top_monthly(limit: int = 10) -> list[dict[str, Any]]:
    cursor = (
        mongo.db[users_db.COLLECTION]
        .find(dict(_ELIGIBLE))
        .sort([("monthly_earnings", -1)])
        .limit(limit)
    )
    return [doc async for doc in cursor]


async def top_bank(limit: int = 10) -> list[dict[str, Any]]:
    """Return the top-N users by bank balance."""
    cursor = (
        mongo.db[users_db.COLLECTION]
        .find(dict(_ELIGIBLE))
        .sort([("bank", -1)])
        .limit(limit)
    )
    return [doc async for doc in cursor]


def name_of(user: dict[str, Any]) -> str:
    if user.get("username"):
        return user["username"]
    return user.get("first_name") or "Unknown"


async def apply_clearlb(amount: int, user_count: int, actor_id: int) -> dict[str, Any]:
    """Reset the wallet of each of the top ``user_count`` users to ``amount``.

    Used by the ``/clearlb`` admin command.  ``amount`` is the FINAL wallet
    balance, not a subtraction.  The leaderboard is read fresh and honors the
    ``/leaderban`` exclusions; the owner is always skipped.  Every wallet
    change goes through the existing atomic economy engine
    (:func:`economy.set_user_balance`) so bank/stocks/assets are untouched,
    balances can never go negative, and no second balance source exists.

    Every reset writes an audit transaction carrying ``balance_before`` and
    ``balance_after`` (``ADMIN_REMOVE`` when the wallet was reduced,
    ``ADMIN_GIVE`` when it was raised).  Users already at the target balance
    are skipped.  The database is re-read after each change to verify the
    resulting balance.

    ``amount`` must be >= 0 (0 is supported by the economy engine's
    zero-balance reset); ``user_count`` must be >= 1.

    Returns ``{"amount", "done": [...], "skipped": [...]}``.
    """
    from config import config
    from services import economy, transaction as tx_service

    if amount < 0:
        raise ValueError("amount must be >= 0")
    if user_count < 1:
        raise ValueError("user_count must be at least 1")

    top = await top_net_worth(user_count)
    done: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for user in top:
        user_id = user["user_id"]
        if user_id == config.OWNER_ID:
            skipped.append({"user_id": user_id, "reason": "owner"})
            continue
        try:
            before = await economy.get_balance(user_id)
            before_wallet = before["wallet"]
            if before_wallet == amount:
                skipped.append({"user_id": user_id, "reason": "already_at_target"})
                continue
            await economy.set_user_balance(user_id, "wallet", amount)
            # Fresh-read to verify the atomic update really landed.
            after = await economy.get_balance(user_id)
            after_wallet = after["wallet"]
            delta = after_wallet - before_wallet
            ttype = tx_service.ADMIN_GIVE if delta > 0 else tx_service.ADMIN_REMOVE
            tx_id = await tx_service.record(
                user_id=user_id,
                ttype=ttype,
                amount=abs(delta),
                balance_before=before_wallet,
                balance_after=after_wallet,
                metadata={"actor": actor_id, "reason": "clearlb", "reset": True},
            )
            done.append(
                {"user_id": user_id, "tx_id": tx_id, "before": before_wallet, "after": after_wallet}
            )
        except economy.EconomyError as exc:
            skipped.append({"user_id": user_id, "reason": str(exc)})

    return {"amount": amount, "done": done, "skipped": skipped}
