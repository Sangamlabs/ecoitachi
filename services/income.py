"""Daily income claims: /interestbank, /interestasset, /stockinterest.

Each source pays a per-24h rate on top of the current balance/value:

- bank  : current bank balance
- asset : cached asset portfolio value (``asset_value``)
- stock : cached stock portfolio value (``stocks_value``)

Income is computed lazily from the ``<source>_income_last_at`` timestamp.
If a user never claims, full 24h windows keep stacking; claiming pays out
every unclaimed day at once and then resets the clock.
"""

from __future__ import annotations

import time
from typing import Any

from database import income as income_db, users as users_db
from services import economy, settings as settings_service, transaction as tx_service

DAY_SECONDS = 86_400

BANK = "bank"
ASSET = "asset"
STOCK = "stock"

LAST_FIELD_BY_SOURCE = {
    BANK: "bank_income_last_at",
    ASSET: "asset_income_last_at",
    STOCK: "stock_income_last_at",
}

RATE_FIELD_BY_SOURCE = {
    BANK: "bank_rate_percent",
    ASSET: "asset_rate_percent",
    STOCK: "stock_rate_percent",
}

TX_TYPE_BY_SOURCE = {
    BANK: tx_service.INTEREST_CLAIM,
    ASSET: tx_service.ASSET_INCOME_CLAIM,
    STOCK: tx_service.STOCK_INCOME_CLAIM,
}

LABEL_BY_SOURCE = {
    BANK: "Bank Interest",
    ASSET: "Asset Income",
    STOCK: "Stock Interest",
}

EMOJI_BY_SOURCE = {
    BANK: "🏦",
    ASSET: "🏠",
    STOCK: "📈",
}


class IncomeError(Exception):
    """Raised when a daily income claim cannot be processed."""


async def source_value(user_id: int, source: str) -> int:
    """Return the current base used to compute the source's daily income."""
    if source == BANK:
        balance = await economy.get_balance(user_id)
        return balance["bank"]
    user = await users_db.get_user(user_id)
    if user is None:
        return 0
    if source == ASSET:
        return int(user.get("asset_value") or 0)
    if source == STOCK:
        return int(user.get("stocks_value") or 0)
    return 0


async def get_status(user_id: int) -> dict[str, Any]:
    """Per-source status used to build help text (value, rate, next payout)."""
    config = await settings_service.get_income_config()
    user = await users_db.get_user(user_id)
    now = int(time.time())
    status: dict[str, Any] = {}
    for source in (BANK, ASSET, STOCK):
        last = int((user or {}).get(LAST_FIELD_BY_SOURCE[source]) or 0)
        next_in = 0 if last == 0 else max(0, DAY_SECONDS - (now - last))
        status[source] = {
            "value": await source_value(user_id, source),
            "rate": float(config[RATE_FIELD_BY_SOURCE[source]]),
            "next_in": next_in,
        }
    return status


async def claim(user_id: int, source: str) -> dict[str, Any]:
    """Claim all unclaimed daily income for ``source``.

    Returns a dict describing the payout.  A zero ``amount`` with
    ``started=True`` means tracking began now; ``already_claimed=True``
    means a concurrent request won the race.
    """
    if source not in LAST_FIELD_BY_SOURCE:
        raise IncomeError("Unknown income source.")

    config = await settings_service.get_income_config()
    rate = float(config[RATE_FIELD_BY_SOURCE[source]])
    last_field = LAST_FIELD_BY_SOURCE[source]
    now = int(time.time())

    user = await users_db.get_user(user_id)
    if user is None:
        raise IncomeError("Account not found. Use /start first.")
    last = int(user.get(last_field) or 0)

    if last == 0:
        await users_db.set_user_field(user_id, last_field, now)
        return {
            "amount": 0,
            "days": 0,
            "value": await source_value(user_id, source),
            "rate": rate,
            "next_in": DAY_SECONDS,
            "started": True,
            "already_claimed": False,
        }

    elapsed = now - last
    days = elapsed // DAY_SECONDS
    if days <= 0:
        return {
            "amount": 0,
            "days": 0,
            "value": await source_value(user_id, source),
            "rate": rate,
            "next_in": DAY_SECONDS - elapsed,
            "started": False,
            "already_claimed": False,
        }

    amount = (int(await source_value(user_id, source) * rate) // 100) * days
    if amount <= 0:
        await income_db.advance_last_claim(user_id, last_field, last, now)
        return {
            "amount": 0,
            "days": days,
            "value": await source_value(user_id, source),
            "rate": rate,
            "next_in": DAY_SECONDS,
            "started": False,
            "already_claimed": False,
        }

    won = await income_db.advance_last_claim(user_id, last_field, last, now)
    if not won:
        return {
            "amount": 0,
            "days": 0,
            "value": await source_value(user_id, source),
            "rate": rate,
            "next_in": DAY_SECONDS,
            "started": False,
            "already_claimed": True,
        }

    before = await economy.get_balance(user_id)
    await economy.add_wallet(user_id, amount, from_transaction=TX_TYPE_BY_SOURCE[source])
    after = await economy.get_balance(user_id)
    await tx_service.record(
        user_id=user_id,
        ttype=TX_TYPE_BY_SOURCE[source],
        amount=amount,
        balance_before=before["wallet"],
        balance_after=after["wallet"],
        metadata={"source": source, "days": days, "value": await source_value(user_id, source), "rate": rate},
    )
    return {
        "amount": amount,
        "days": days,
        "value": await source_value(user_id, source),
        "rate": rate,
        "next_in": DAY_SECONDS,
        "started": False,
        "already_claimed": False,
    }
