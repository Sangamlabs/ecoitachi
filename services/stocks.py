"""Stock / crypto market service.

Prices evolve on a scheduler using a volatility-driven random walk, and every
tick is recorded into a price-history collection.  Holdings are valued at the
current market price.  All money movement goes through the economy engine.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from database import stocks as stocks_db
from services import economy, tax as tax_service, transaction as tx_service
from services.economy import EconomyError
from utils.money import MoneyError, multiply

logger = logging.getLogger(__name__)


async def ensure_market() -> None:
    await stocks_db.ensure_default_assets()


async def list_market() -> list[dict[str, Any]]:
    await ensure_market()
    return await stocks_db.list_active_assets()


async def get_asset(symbol: str) -> dict[str, Any]:
    asset = await stocks_db.get_asset(symbol)
    if asset is None:
        raise EconomyError(f"Unknown or inactive asset: <code>{symbol}</code>")
    return asset


async def add_asset(symbol: str, name: str, base_price: int, volatility: float) -> str:
    """List a new asset (or re-list an inactive one). Returns 'created'/'reactivated'."""
    symbol = symbol.upper()
    existing = await stocks_db.get_asset_any(symbol)
    now = int(time.time())
    fields = {
        "name": name,
        "base_price": base_price,
        "volatility": volatility,
        "price": base_price,
        "open_price": base_price,
        "high_price": base_price,
        "low_price": base_price,
        "change": 0,
        "change_percent": 0.0,
        "is_active": True,
        "updated_at": now,
    }
    if existing is not None:
        if existing.get("is_active"):
            raise EconomyError(f"<code>{symbol}</code> is already listed on the market.")
        await stocks_db.update_asset(symbol, fields)
        return "reactivated"
    await stocks_db.create_asset({"symbol": symbol, "volume": 0, "created_at": now, **fields})
    return "created"


async def deactivate_asset(symbol: str) -> dict[str, Any]:
    """Remove an asset from the market. Raises if it is not listed."""
    asset = await stocks_db.get_asset(symbol)
    if asset is None:
        raise EconomyError(f"<code>{symbol.upper()}</code> is not listed on the market.")
    await stocks_db.update_asset(symbol, {"is_active": False, "updated_at": int(time.time())})
    return asset


async def update_market_prices() -> int:
    """Advance every asset price by one random-walk tick. Returns tick count."""
    assets = await stocks_db.get_all_assets()
    now = int(time.time())
    ticks = 0
    for asset in assets:
        if not asset.get("is_active", True):
            continue
        price = int(asset.get("price", asset.get("base_price", 1000)))
        volatility = float(asset.get("volatility", 0.02))
        base = int(asset.get("base_price", price) or price)
        drift = max(-1.0, min(1.0, (base - price) / max(base, 1))) * 0.01
        change = int(price * random.gauss(drift, volatility))
        new_price = max(100, price + change)
        change_pct = (new_price - price) / max(price, 1) * 100
        high = max(asset.get("high_price", new_price), new_price)
        low = min(asset.get("low_price", new_price), new_price)
        await stocks_db.update_price(
            asset["symbol"], new_price, new_price - price, change_pct, high, low, now
        )
        await stocks_db.insert_history(asset["symbol"], new_price, now)
        ticks += 1
    return ticks


def _parse_quantity(raw: str) -> float:
    try:
        qty = float(raw.strip())
    except (ValueError, AttributeError):
        raise EconomyError("Invalid quantity. Use numbers like <code>0.01</code>.")
    if not (qty > 0) or qty != qty or qty in (float("inf"), float("-inf")):
        raise EconomyError("Invalid quantity.")
    return round(qty, 6)


async def buy_stock(user_id: int, symbol: str, qty_raw: str) -> dict[str, Any]:
    """Buy an asset: validate, cost it at current price, move money, hold."""
    qty = _parse_quantity(qty_raw)
    asset = await get_asset(symbol)
    price = int(asset.get("price", 0))
    cost = multiply(price, qty)
    if cost <= 0:
        raise MoneyError("Purchase cost must be positive.")

    tax = await tax_service.system_tax_amount("stocks", cost)
    total = cost + tax
    before = await economy.get_balance(user_id)
    await economy.remove_wallet(user_id, total, spend=True)
    if tax > 0:
        await tax_service.collect(user_id, tax)
    await stocks_db.add_holding(user_id, asset["symbol"], qty)

    tx_id = await tx_service.record(
        user_id=user_id,
        ttype=tx_service.STOCK_BUY,
        amount=total,
        balance_before=before["wallet"],
        balance_after=before["wallet"] - total,
        metadata={"symbol": asset["symbol"], "quantity": qty, "price": price, "tax": tax},
    )
    return {"symbol": asset["symbol"], "quantity": qty, "cost": cost, "tax": tax, "total": total, "price": price, "tx_id": tx_id}


async def grant_stock(
    user_id: int,
    symbol: str,
    qty_raw: str,
    *,
    promo_id: str | None = None,
    promo_code: str | None = None,
) -> dict[str, Any]:
    """Grant a stock holding to a user for free (used by the promo engine).

    Validates the stock exists and is active, then moves the holding through
    the stock data layer (never mutated directly by callers).
    """
    qty = _parse_quantity(qty_raw)
    asset = await get_asset(symbol)
    if asset is None:
        raise EconomyError(f"Stock <code>{symbol.upper()}</code> is not available.")
    await stocks_db.add_holding(user_id, asset["symbol"], qty)
    price = int(asset.get("price", 0))
    tx_id = await tx_service.record(
        user_id=user_id,
        ttype=tx_service.PROMO_STOCK,
        amount=0,
        balance_before=0,
        balance_after=0,
        metadata={
            "symbol": asset["symbol"],
            "quantity": qty,
            "price": price,
            "promo_id": promo_id,
            "promo_code": promo_code,
            "source": "PROMO",
        },
    )
    return {
        "symbol": asset["symbol"],
        "quantity": qty,
        "price": price,
        "tx_id": tx_id,
    }


async def revoke_grant_stock(user_id: int, symbol: str, quantity: float) -> None:
    """Compensating removal of a promo-granted stock holding."""
    await stocks_db.remove_holding(user_id, symbol.upper(), quantity)


async def sell_stock(user_id: int, symbol: str, qty_raw: str) -> dict[str, Any]:
    """Sell holdings at current price; credits go to the wallet."""
    qty = _parse_quantity(qty_raw)
    asset = await get_asset(symbol)
    price = int(asset.get("price", 0))
    holding = await stocks_db.get_holding(user_id, asset["symbol"])
    if holding is None or float(holding.get("quantity", 0)) < qty:
        raise EconomyError(f"You do not own {qty} of <code>{asset['symbol']}</code>.")

    value = multiply(price, qty)
    if not await stocks_db.remove_holding(user_id, asset["symbol"], qty):
        raise EconomyError("Insufficient holdings to sell.")

    tax = await tax_service.system_tax_amount("stocks", value)
    received = value - tax
    before = await economy.get_balance(user_id)
    await economy.add_wallet(user_id, received, earn=True)
    if tax > 0:
        await tax_service.collect(user_id, tax)
    tx_id = await tx_service.record(
        user_id=user_id,
        ttype=tx_service.STOCK_SELL,
        amount=received,
        balance_before=before["wallet"],
        balance_after=before["wallet"] + received,
        metadata={"symbol": asset["symbol"], "quantity": qty, "price": price, "tax": tax},
    )
    return {"symbol": asset["symbol"], "quantity": qty, "value": value, "tax": tax, "received": received, "price": price, "tx_id": tx_id}


async def portfolio(user_id: int) -> dict[str, Any]:
    """Current holdings valued at live prices."""
    holdings = await stocks_db.get_user_holdings(user_id)
    rows: list[dict[str, Any]] = []
    total_value = 0
    total_cost = 0
    for h in holdings:
        asset = await stocks_db.get_asset(h["symbol"])
        if asset is None:
            continue
        price = int(asset.get("price", 0))
        value = multiply(price, h["quantity"])
        total_value += value
        total_cost += int(float(h.get("quantity", 0)) * asset.get("base_price", 0))
        rows.append(
            {
                "symbol": asset["symbol"],
                "quantity": round(float(h["quantity"]), 6),
                "price": price,
                "value": value,
                "change_percent": asset.get("change_percent", 0.0),
            }
        )
    await users_db_touch_stocks(user_id, total_value)
    return {"rows": rows, "total_value": total_value, "total_cost": total_cost}


async def users_db_touch_stocks(user_id: int, total_value: int) -> None:
    from database import users as users_db
    from database.mongo import mongo

    await mongo.db[users_db.COLLECTION].update_one(
        {"user_id": user_id}, {"$set": {"stocks_value": total_value}}
    )


async def refresh_all_stock_values() -> None:
    """Recompute cached stocks_value for all users (used after price ticks)."""
    from database import users as users_db
    from database.mongo import mongo

    cursor = mongo.db[users_db.COLLECTION].find({}, {"user_id": 1})
    async for user in cursor:
        try:
            await portfolio(user["user_id"])
        except Exception:
            logger.exception("failed to refresh stock value for %s", user["user_id"])
