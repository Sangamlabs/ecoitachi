"""Assets Market price engine.

Periodically updates every listed asset's price with a volatility-based random
walk, tracks 24h open/high/low/change, inserts price-history candles and
refreshes every user's cached asset value afterwards.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from database import assets as assets_db
from database import asset_holdings as holdings_db
from services import assets as asset_service
from services import settings as settings_service

logger = logging.getLogger(__name__)

_last_tick = 0.0


async def tick(force: bool = False) -> dict[str, Any]:
    """Run one market tick (no-op if it has not been long enough)."""
    global _last_tick
    cfg = await settings_service.get_asset_market_config()
    if not cfg.get("enabled", True):
        return {"ticked": False, "reason": "disabled"}

    interval = float(cfg.get("tick_interval_minutes", 2) or 2) * 60
    now = time.time()
    if not force and now - _last_tick < interval:
        return {"ticked": False, "reason": "cooldown"}

    _last_tick = now
    retention = max(1, int(cfg.get("price_history_retention", 500) or 500))
    assets = await assets_db.list_assets(active_only=True, limit=10_000)
    if not assets:
        return {"ticked": True, "updated": 0}

    ts = int(now)
    updated = 0
    for asset in assets:
        await _step_asset(asset, ts, retention)
        updated += 1
    await asset_service.refresh_all_asset_values()
    return {"ticked": True, "updated": updated}


async def _step_asset(asset: dict[str, Any], ts: int, retention: int) -> None:
    symbol = asset["symbol"]
    prev = int(asset.get("price", 0))
    volatility = float(asset.get("volatility", 0.02) or 0.02)
    drift = random.uniform(-1, 1) * volatility
    new_price = max(1, int(round(prev * (1 + drift))))
    change = new_price - prev
    change_percent = (change / prev * 100) if prev else 0.0

    update: dict[str, Any] = {
        "price": new_price,
        "change": change,
        "change_percent": change_percent,
        "volume": asset.get("volume", 0),
    }
    if asset.get("open_price") is None:
        update["open_price"] = prev
    if new_price > int(asset.get("high_price", new_price)):
        update["high_price"] = new_price
    if int(asset.get("low_price", new_price)) == 0 or new_price < int(asset.get("low_price", new_price)):
        update["low_price"] = new_price
    update["updated_at"] = ts

    await assets_db.update_asset(symbol, update)
    await assets_db.insert_price_history(symbol, new_price, ts)
    await _refresh_holding_values(asset, new_price, ts)
    await assets_db.prune_price_history(symbol, retention)


async def _refresh_holding_values(asset: dict[str, Any], new_price: int, ts: int) -> None:
    """Keep every holding's cached current_value aligned with the new price."""
    from database.mongo import mongo

    await mongo.db[holdings_db.HOLDINGS].update_many(
        {"asset_id": asset["asset_id"], "quantity": {"$gt": holdings_db.EPSILON}},
        [{"$set": {"current_value": {"$multiply": [new_price, "$quantity"]}, "updated_at": ts}}],
    )


async def force_tick() -> dict[str, Any]:
    return await tick(force=True)


async def set_tick_interval(minutes: float) -> dict[str, Any]:
    minutes = float(minutes)
    if minutes <= 0:
        raise asset_service.AssetError("Tick interval must be greater than 0.")
    await settings_service.update_asset_market_config(tick_interval_minutes=minutes)
    return await settings_service.get_asset_market_config()


async def get_config() -> dict[str, Any]:
    return await settings_service.get_asset_market_config()


async def set_config(**changes: Any) -> dict[str, Any]:
    return await settings_service.update_asset_market_config(**changes)
