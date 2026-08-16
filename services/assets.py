"""Assets Market core service.

Owns every business rule for the asset economy: validation, buy/sell flows,
portfolio/P/L, admin lifecycle operations and market statistics.  All money
moves through the central economy engine and every financial operation is
recorded by the transaction engine.  Handlers stay thin.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from database import assets as assets_db
from database import asset_holdings as holdings_db
from database import asset_listings as listings_db
from database import transactions as tx_db
from database import users as users_db
from services import economy, settings as settings_service
from services import transaction as tx_service
from services.economy import InsufficientBalance
from utils.money import format_money, multiply, percentage

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = {
    "name",
    "description",
    "category",
    "emoji",
    "base_price",
    "price",
    "volatility",
    "min_quantity",
    "max_quantity",
    "quantity_step",
    "allow_fractional",
    "is_tradeable",
    "is_active",
}

_BOOL_VALUES = {
    "1": True, "0": False, "true": True, "false": False,
    "on": True, "off": False, "yes": True, "no": False,
}


class AssetError(Exception):
    """User-facing asset market failure."""


class AssetMarketDisabled(AssetError):
    pass


class InsufficientHoldings(AssetError):
    pass


class ListingUnavailable(AssetError):
    pass


def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    value = _BOOL_VALUES.get(str(raw).strip().lower())
    if value is None:
        raise AssetError("Value must be <code>true</code> or <code>false</code>.")
    return value


def _parse_field_value(field: str, raw: Any) -> Any:
    if field == "allow_fractional":
        return _parse_bool(raw)
    if field == "is_tradeable":
        return _parse_bool(raw)
    if field == "is_active":
        return _parse_bool(raw)
    if field in ("name", "description", "category", "emoji"):
        if isinstance(raw, bool):
            raise AssetError(f"Invalid value for {field}.")
        return str(raw).strip()
    if field in ("base_price", "price"):
        try:
            value = int(float(str(raw)))
        except (ValueError, TypeError):
            raise AssetError(f"Invalid numeric value for {field}.")
        if value <= 0:
            raise AssetError(f"{field} must be greater than 0.")
        return value
    if field in ("min_quantity", "max_quantity", "quantity_step"):
        try:
            value = float(str(raw))
        except (ValueError, TypeError):
            raise AssetError(f"Invalid numeric value for {field}.")
        if not math.isfinite(value) or value <= 0:
            raise AssetError(f"{field} must be a positive number.")
        return round(value, 6)
    if field == "volatility":
        try:
            value = float(str(raw))
        except (ValueError, TypeError):
            raise AssetError("Invalid volatility.")
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise AssetError("Volatility must be between 0 and 1.")
        return value
    raise AssetError(f"Field <code>{field}</code> is not editable.")


async def ensure_market() -> None:
    await assets_db.ensure_default_assets()


async def market_config() -> dict[str, Any]:
    return await settings_service.get_asset_market_config()


async def require_market() -> dict[str, Any]:
    cfg = await market_config()
    if not cfg.get("enabled", True):
        raise AssetMarketDisabled("The Assets Market is currently disabled.")
    return cfg


async def get_asset(symbol: str) -> dict[str, Any]:
    asset = await assets_db.get_asset(symbol)
    if asset is None:
        raise AssetError(f"Unknown asset: <code>{symbol.upper()}</code>.")
    return asset


async def get_active_asset(symbol: str) -> dict[str, Any]:
    asset = await get_asset(symbol)
    if not asset.get("is_active", True):
        raise AssetError(f"<code>{asset['symbol']}</code> is not listed on the market.")
    return asset


async def _get_tradeable(symbol: str) -> dict[str, Any]:
    asset = await get_active_asset(symbol)
    if not asset.get("is_tradeable", True):
        raise AssetError(f"<code>{asset['symbol']}</code> is not currently tradeable.")
    return asset


def parse_quantity(raw: str, asset: dict[str, Any]) -> float:
    """Validate and normalize a purchase/sale quantity against asset limits."""
    if raw is None:
        raise AssetError("Quantity is required.")
    raw = str(raw).strip()
    try:
        qty = float(raw)
    except (ValueError, TypeError):
        raise AssetError("Invalid quantity.")
    if not math.isfinite(qty) or qty <= 0:
        raise AssetError("Quantity must be a positive number.")

    allow_fractional = bool(asset.get("allow_fractional", False))
    step = float(asset.get("quantity_step", 1.0) or 1.0)
    if not allow_fractional:
        if qty != round(qty):
            raise AssetError("This asset can only be traded in whole units.")
        qty = int(qty)
    else:
        qty = round(qty / step) * step
        qty = round(qty, 6)
        if qty <= 0:
            raise AssetError("Quantity must be a positive number.")

    min_q = float(asset.get("min_quantity", 1.0) or 1.0)
    if qty < min_q - 1e-9:
        raise AssetError(f"Minimum quantity is <b>{min_q:g}</b>.")
    max_q = asset.get("max_quantity")
    if max_q and qty > float(max_q) + 1e-9:
        raise AssetError(f"Maximum quantity per order is <b>{max_q:g}</b>.")
    return qty


def price_for(asset: dict[str, Any], cfg: dict[str, Any], direction: str) -> int:
    """Buy/sell execution price (applies configured multipliers)."""
    base = int(asset.get("price", 0))
    if direction == "buy":
        mult = float(cfg.get("buy_price_multiplier", 1.0))
    else:
        mult = float(cfg.get("sell_price_multiplier", 1.0))
    return int(base * mult)


def multiply_price(price: int, qty: float) -> int:
    """Exact integer sub-unit cost of ``qty`` units at ``price``."""
    return multiply(price, qty)


async def _volume_inc(symbol: str) -> None:
    from database.mongo import mongo

    await mongo.db[assets_db.ASSETS].update_one(
        {"symbol": symbol.upper()}, {"$inc": {"volume": 1}}
    )


async def buy(
    user_id: int, symbol: str, qty_raw: str
) -> dict[str, Any]:
    """Buy an asset from the market at the current price (atomic)."""
    cfg = await require_market()
    asset = await _get_tradeable(symbol)
    qty = parse_quantity(qty_raw, asset)
    price = price_for(asset, cfg, "buy")
    cost = multiply(price, qty)
    fee = percentage(cost, float(cfg.get("buy_fee_percent", 0.0)))
    total = cost + fee
    if total <= 0:
        raise AssetError("Purchase cost must be positive.")

    max_holding = asset.get("max_holding")
    if max_holding:
        holding = await holdings_db.get_holding(user_id, asset["asset_id"])
        existing = float(holding.get("quantity", 0)) if holding else 0.0
        if existing + qty > float(max_holding) + 1e-9:
            raise AssetError(f"Maximum holding for <code>{asset['symbol']}</code> is <b>{max_holding:g}</b>.")

    before = await economy.get_balance(user_id)
    try:
        await economy.remove_wallet(user_id, total, spend=True)
    except InsufficientBalance:
        raise AssetError(
            f"Insufficient wallet balance. Needed <b>{format_money(total)}</b>."
        )
    await holdings_db.add_holding(user_id, asset["asset_id"], asset["symbol"], qty, cost, price)
    await _volume_inc(asset["symbol"])
    tx_id = await tx_service.record(
        user_id=user_id,
        ttype=tx_service.ASSET_BUY,
        amount=total,
        balance_before=before["wallet"],
        balance_after=before["wallet"] - total,
        metadata={
            "asset_id": asset["asset_id"],
            "symbol": asset["symbol"],
            "quantity": qty,
            "price": price,
            "total_value": cost,
            "fee": fee,
        },
    )
    await refresh_user_asset_value(user_id)
    return {
        "symbol": asset["symbol"],
        "name": asset.get("name", ""),
        "emoji": asset.get("emoji", ""),
        "quantity": qty,
        "price": price,
        "cost": cost,
        "fee": fee,
        "total": total,
        "tx_id": tx_id,
    }


async def sell(
    user_id: int, symbol: str, qty_raw: str
) -> dict[str, Any]:
    """Sell owned quantity back to the market at the current price (atomic)."""
    cfg = await require_market()
    asset = await _get_tradeable(symbol)
    holding = await holdings_db.get_holding(user_id, asset["asset_id"])
    owned = float(holding.get("quantity", 0)) if holding else 0.0

    raw = str(qty_raw).strip() if qty_raw is not None else ""
    try:
        qty = float(raw)
    except (ValueError, TypeError):
        raise AssetError("Invalid quantity.")
    if not math.isfinite(qty) or qty <= 0:
        raise AssetError("Quantity must be a positive number.")
    if not bool(asset.get("allow_fractional", False)) and qty != round(qty):
        raise AssetError("This asset can only be traded in whole units.")
    if qty > owned + 1e-9:
        raise InsufficientHoldings(
            f"You only own <b>{owned:g}</b> of <code>{asset['symbol']}</code>."
        )

    price = price_for(asset, cfg, "sell")
    value = multiply(price, qty)
    fee = percentage(value, float(cfg.get("sell_fee_percent", 0.0)))
    received = value - fee

    if not await holdings_db.remove_holding(user_id, asset["asset_id"], qty, price):
        raise InsufficientHoldings(
            f"You only own <b>{owned:g}</b> of <code>{asset['symbol']}</code>."
        )

    before = await economy.get_balance(user_id)
    await economy.add_wallet(user_id, received, earn=False)
    await _volume_inc(asset["symbol"])
    tx_id = await tx_service.record(
        user_id=user_id,
        ttype=tx_service.ASSET_SELL,
        amount=received,
        balance_before=before["wallet"],
        balance_after=before["wallet"] + received,
        metadata={
            "asset_id": asset["asset_id"],
            "symbol": asset["symbol"],
            "quantity": qty,
            "price": price,
            "total_value": value,
            "fee": fee,
        },
    )
    await refresh_user_asset_value(user_id)
    return {
        "symbol": asset["symbol"],
        "name": asset.get("name", ""),
        "emoji": asset.get("emoji", ""),
        "quantity": qty,
        "price": price,
        "value": value,
        "fee": fee,
        "received": received,
        "tx_id": tx_id,
    }


async def portfolio(user_id: int) -> dict[str, Any]:
    """Current holdings valued at live market prices with P/L."""
    holdings = await holdings_db.get_user_holdings(user_id)
    rows: list[dict[str, Any]] = []
    total_value = 0
    total_invested = 0
    for h in holdings:
        asset = await assets_db.get_asset(h["symbol"])
        if asset is None:
            continue
        price = int(asset.get("price", 0))
        qty = float(h.get("quantity", 0))
        value = multiply(price, qty)
        invested = int(h.get("total_invested", 0))
        pnl = value - invested
        pnl_percent = (pnl / invested * 100) if invested else 0.0
        rows.append(
            {
                "symbol": asset["symbol"],
                "name": asset.get("name", ""),
                "emoji": asset.get("emoji", ""),
                "quantity": qty,
                "average_buy_price": float(h.get("average_buy_price", 0)),
                "price": price,
                "value": value,
                "pnl": pnl,
                "pnl_percent": pnl_percent,
                "is_active": asset.get("is_active", True),
            }
        )
        total_value += value
        total_invested += invested
    await refresh_user_asset_value(user_id)
    return {
        "rows": rows,
        "total_value": total_value,
        "total_invested": total_invested,
        "total_pnl": total_value - total_invested,
    }


async def live_asset_value(user_id: int) -> int:
    """Sum of a user's holdings at current live market prices."""
    holdings = await holdings_db.get_user_holdings(user_id)
    total = 0
    for h in holdings:
        asset = await assets_db.get_asset(h["symbol"])
        if asset and asset.get("is_active", True):
            total += multiply(int(asset.get("price", 0)), float(h.get("quantity", 0)))
    return total


async def refresh_user_asset_value(user_id: int) -> int:
    total = await live_asset_value(user_id)
    await users_db.set_user_field(user_id, "asset_value", total)
    return total


async def refresh_all_asset_values() -> None:
    """Recompute cached asset_value for every user (after market ticks)."""
    cursor = mongo_users_all()
    async for user in cursor:
        try:
            await refresh_user_asset_value(user["user_id"])
        except Exception:
            logger.exception("failed to refresh asset value for %s", user["user_id"])


def mongo_users_all():
    from database.mongo import mongo

    return mongo.db[users_db.COLLECTION].find({}, {"user_id": 1})


# ---------------------------------------------------------------------------
# Admin lifecycle
# ---------------------------------------------------------------------------

async def create_asset(
    actor_id: int,
    symbol: str,
    name: str,
    category: str,
    base_price: int,
    volatility: float,
    description: str = "",
    emoji: str = "📦",
    fractional: bool | None = None,
    min_quantity: float | None = None,
    max_quantity: float | None = None,
    quantity_step: float | None = None,
) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    name = str(name or "").strip()
    category = str(category or "").strip().upper()
    if not symbol or not symbol.isalnum() or len(symbol) > 10:
        raise AssetError("Symbol must be 1-10 letters/numbers (no spaces).")
    if not name:
        raise AssetError("Asset name is required.")
    if category not in assets_db.CATEGORIES:
        raise AssetError(f"Invalid category. Valid: {', '.join(assets_db.CATEGORIES)}")
    if base_price <= 0:
        raise AssetError("Base price must be greater than 0.")
    if not math.isfinite(volatility) or not 0 <= volatility <= 1:
        raise AssetError("Volatility must be between 0 and 1.")
    if await assets_db.get_asset(symbol) is not None:
        raise AssetError(f"An asset with symbol <code>{symbol}</code> already exists.")

    doc = {
        **assets_db.DEFAULT_MARKET,
        "asset_id": assets_db.new_asset_id(),
        "symbol": symbol,
        "name": name,
        "category": category,
        "description": description,
        "emoji": emoji,
        "base_price": int(base_price),
        "price": int(base_price),
        "open_price": int(base_price),
        "high_price": int(base_price),
        "low_price": int(base_price),
        "change": 0,
        "change_percent": 0.0,
        "volatility": float(volatility),
        "volume": 0,
        "is_active": True,
        "is_tradeable": True,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    if fractional is not None:
        doc["allow_fractional"] = bool(fractional)
    if min_quantity is not None:
        doc["min_quantity"] = round(float(min_quantity), 6)
    if max_quantity is not None:
        doc["max_quantity"] = round(float(max_quantity), 6)
    if quantity_step is not None:
        doc["quantity_step"] = round(float(quantity_step), 6)
    await assets_db.insert_asset(doc)
    await audit(actor_id, "ADD_ASSET", symbol, None, {"price": doc["price"], "volatility": volatility})
    return doc


async def update_asset_fields(
    actor_id: int, symbol: str, fields: dict[str, Any], *, action: str = "EDIT_ASSET"
) -> dict[str, Any]:
    """Apply validated edits to an asset.  Ownership balances are never touched."""
    asset = await get_asset(symbol)
    changes: dict[str, Any] = {}
    log: dict[str, Any] = {}
    for field, raw in fields.items():
        if field not in EDITABLE_FIELDS:
            raise AssetError(f"Field <code>{field}</code> is not editable.")
        value = _parse_field_value(field, raw)
        old = asset.get(field)
        if old == value:
            continue
        changes[field] = value
        log[field] = {"old": old, "new": value}

    if "category" in changes:
        if changes["category"].upper() not in assets_db.CATEGORIES:
            raise AssetError(f"Invalid category. Valid: {', '.join(assets_db.CATEGORIES)}")
        changes["category"] = changes["category"].upper()
    if "min_quantity" in changes and "max_quantity" not in changes:
        max_q = asset.get("max_quantity")
        if max_q and changes["min_quantity"] > float(max_q):
            raise AssetError("min_quantity cannot exceed max_quantity.")
    if "max_quantity" in changes and "min_quantity" not in changes:
        min_q = asset.get("min_quantity", 1.0)
        if float(min_q) > changes["max_quantity"]:
            raise AssetError("min_quantity cannot exceed max_quantity.")
    if "base_price" in changes and "price" not in changes:
        changes["price"] = changes["base_price"]

    if "price" in changes:
        new_price = changes["price"]
        old_price = int(asset.get("price", 0))
        changes["change"] = new_price - old_price
        changes["change_percent"] = (new_price - old_price) / old_price * 100 if old_price else 0.0
        await assets_db.insert_price_history(symbol, new_price, int(time.time()))

    if not changes:
        return asset
    await assets_db.update_asset(symbol, changes)
    await audit(actor_id, action, symbol, log, None)
    return await get_asset(symbol)


async def deactivate_asset(actor_id: int, symbol: str) -> dict[str, Any]:
    """Delist an asset.  Existing user holdings are kept (untradeable)."""
    asset = await get_asset(symbol)
    if not asset.get("is_active", True):
        raise AssetError(f"<code>{symbol}</code> is already delisted.")
    await assets_db.update_asset(symbol, {"is_active": False, "is_tradeable": False})
    await audit(actor_id, "REMOVE_ASSET", symbol, {"is_active": True}, {"is_active": False})
    return asset


async def restore_asset(actor_id: int, symbol: str) -> dict[str, Any]:
    asset = await get_asset(symbol)
    if asset.get("is_active", True):
        raise AssetError(f"<code>{symbol}</code> is already active.")
    await assets_db.update_asset(symbol, {"is_active": True, "is_tradeable": True})
    await audit(actor_id, "RESTORE_ASSET", symbol, {"is_active": False}, {"is_active": True})
    return asset


async def set_price(actor_id: int, symbol: str, price: int) -> dict[str, Any]:
    asset = await get_asset(symbol)
    if price <= 0:
        raise AssetError("Price must be greater than 0.")
    old_price = int(asset.get("price", 0))
    change = price - old_price
    change_percent = (change / old_price * 100) if old_price else 0.0
    await assets_db.update_asset(
        symbol,
        {
            "price": price,
            "change": change,
            "change_percent": change_percent,
            "high_price": max(int(asset.get("high_price", price)), price),
            "low_price": min(int(asset.get("low_price", price)) or price, price),
        },
    )
    await assets_db.insert_price_history(symbol, price, int(time.time()))
    await audit(actor_id, "MANUAL_PRICE_CHANGE", symbol, {"price": old_price}, {"price": price})
    return await get_asset(symbol)


async def set_volatility(actor_id: int, symbol: str, volatility: float) -> dict[str, Any]:
    asset = await get_asset(symbol)
    if not math.isfinite(volatility) or not 0 <= volatility <= 1:
        raise AssetError("Volatility must be between 0 and 1.")
    await assets_db.update_asset(symbol, {"volatility": float(volatility)})
    await audit(
        actor_id, "VOLATILITY_CHANGE", symbol,
        {"volatility": asset.get("volatility")}, {"volatility": volatility},
    )
    return await get_asset(symbol)


async def audit(
    actor_id: int, action: str, symbol: str,
    old_value: Any = None, new_value: Any = None,
) -> None:
    await assets_db.insert_admin_log(
        {
            "admin_id": actor_id,
            "action": action,
            "symbol": symbol.upper(),
            "old_value": old_value,
            "new_value": new_value,
            "timestamp": int(time.time()),
        }
    )


# ---------------------------------------------------------------------------
# Lookups / statistics
# ---------------------------------------------------------------------------

async def list_paged(
    active_only: bool = True,
    category: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = 10,
) -> dict[str, Any]:
    page = max(1, page)
    offset = (page - 1) * per_page
    total = await assets_db.count_assets(active_only=active_only, category=category)
    assets = await assets_db.list_assets(
        active_only=active_only, category=category, search=search,
        limit=per_page, offset=offset,
    )
    return {
        "assets": assets,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, math.ceil(total / per_page)) if total else 1,
    }


async def market_stats() -> dict[str, Any]:
    total = await assets_db.count_assets(active_only=False)
    active = await assets_db.count_assets(active_only=True)
    market = await assets_db.list_assets(active_only=True, limit=10_000)
    gainers = losers = unchanged = 0
    for a in market:
        ch = float(a.get("change_percent", 0) or 0)
        if ch > 0:
            gainers += 1
        elif ch < 0:
            losers += 1
        else:
            unchanged += 1
    total_market_value = await holdings_db.aggregate_current_value()
    total_volume = await tx_db.sum_amount_by_types([tx_service.ASSET_BUY, tx_service.ASSET_SELL])
    total_volume += await listings_db.aggregate_listing_volume()
    return {
        "total": total,
        "active": active,
        "inactive": total - active,
        "total_market_value": total_market_value,
        "total_volume": total_volume,
        "gainers": gainers,
        "losers": losers,
        "unchanged": unchanged,
        "categories": len(assets_db.CATEGORIES),
    }


async def admin_stats() -> dict[str, Any]:
    total = await assets_db.count_assets(active_only=False)
    active = await assets_db.count_assets(active_only=True)
    holders = await holdings_db.count_holders()
    holdings_count = await holdings_db.count_holdings()
    total_value = await holdings_db.aggregate_current_value()
    total_invested = await holdings_db.aggregate_invested()
    total_volume = await tx_db.sum_amount_by_types([tx_service.ASSET_BUY, tx_service.ASSET_SELL])
    total_volume += await listings_db.aggregate_listing_volume()
    return {
        "total": total,
        "active": active,
        "inactive": total - active,
        "holders": holders,
        "holdings": holdings_count,
        "total_value": total_value,
        "total_invested": total_invested,
        "total_pnl": total_value - total_invested,
        "total_volume": total_volume,
        "top": await holdings_db.top_assets_by_value(5),
        "most_held": await holdings_db.most_held_assets(5),
    }


async def asset_owners(symbol: str, page: int = 1, per_page: int = 10) -> dict[str, Any]:
    asset = await get_asset(symbol)
    page = max(1, page)
    offset = (page - 1) * per_page
    rows = await holdings_db.holdings_for_asset(asset["asset_id"], limit=per_page, offset=offset)
    owners = []
    for h in rows:
        user = await users_db.get_user(h["user_id"])
        owners.append(
            {
                "user_id": h["user_id"],
                "username": (user or {}).get("username"),
                "quantity": float(h.get("quantity", 0)),
                "value": int(h.get("current_value", 0)),
            }
        )
    total = await mongo_count_asset_holders(asset["asset_id"])
    return {
        "symbol": asset["symbol"],
        "owners": owners,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, math.ceil(total / per_page)) if total else 1,
    }


async def mongo_count_asset_holders(asset_id: str) -> int:
    from database.mongo import mongo

    return await mongo.db[holdings_db.HOLDINGS].count_documents(
        {"asset_id": asset_id, "quantity": {"$gt": holdings_db.EPSILON}}
    )


async def format_money_wrap(value: int) -> str:
    return format_money(value)


__all__ = [
    "AssetError", "AssetMarketDisabled", "InsufficientHoldings", "ListingUnavailable",
    "EDITABLE_FIELDS", "ensure_market", "get_asset", "get_active_asset", "parse_quantity",
    "buy", "sell", "portfolio", "live_asset_value", "refresh_user_asset_value",
    "refresh_all_asset_values", "create_asset", "update_asset_fields", "deactivate_asset",
    "restore_asset", "set_price", "set_volatility", "audit", "list_paged", "market_stats",
    "admin_stats", "asset_owners", "price_for", "multiply_price", "market_config",
    "require_market",
]
