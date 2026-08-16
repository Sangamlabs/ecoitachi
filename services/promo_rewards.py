"""Promo reward registry.

Each reward type (currency / stock / asset) is handled by a small handler that
knows how to parse its reward token, validate the referenced asset, grant the
reward through the owning service, and compensate an already-granted reward when
a later reward in the bundle fails.  Money always flows through the central
economy engine and every grant is recorded by the transaction engine.
"""

from __future__ import annotations

import logging
import math
from html import escape as html_escape

from database import assets as assets_db
from services import assets as assets_service
from services import economy, stocks as stocks_service
from services import transaction as tx_service
from services.assets import AssetError
from services.economy import EconomyError
from utils.money import MoneyError, format_money, parse_amount

logger = logging.getLogger(__name__)

ALIASES = {"rs": "currency", "money": "currency", "cash": "currency"}


class PromoRewardError(Exception):
    """A promo reward problem that is safe to show to the user."""


def format_qty(value: float) -> str:
    """Render a decimal quantity without trailing zeros (1.0 -> '1')."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def describe_reward(reward: dict) -> str:
    """One-line HTML display for a reward spec (used in admin info)."""
    kind = reward.get("type")
    if kind == "currency":
        return f"💰 {format_money(int(reward.get('amount', 0)))}"
    if kind == "stock":
        return f"📈 {html_escape(str(reward.get('symbol', '')))} × {format_qty(float(reward.get('quantity', 0)))}"
    if kind == "asset":
        return f"🏠 {html_escape(str(reward.get('asset_id', '')))} × {format_qty(float(reward.get('quantity', 0)))}"
    return f"🎁 {html_escape(str(reward))}"


class CurrencyRewardHandler:
    reward_type = "currency"

    def parse(self, value: str) -> dict:
        raw = str(value).strip()
        try:
            amount = parse_amount(raw)
        except MoneyError as exc:
            raise PromoRewardError(f"Invalid currency amount: <code>{html_escape(raw)}</code>.") from exc
        if amount <= 0:
            raise PromoRewardError("Currency reward must be positive.")
        return {"type": "currency", "amount": amount}

    async def validate(self, reward: dict, user_id: int | None) -> None:
        if int(reward.get("amount", 0)) <= 0:
            raise PromoRewardError("Currency reward must be positive.")

    async def grant(self, reward: dict, user_id: int, promo: dict) -> dict:
        amount = int(reward["amount"])
        await economy.add_wallet(user_id, amount, earn=True)
        tx_id = await tx_service.record(
            user_id=user_id,
            ttype=tx_service.PROMO_CURRENCY,
            amount=amount,
            balance_before=0,
            balance_after=amount,
            metadata={
                "amount": amount,
                "promo_id": promo["promo_id"],
                "promo_code": promo["code"],
                "source": "PROMO",
            },
        )
        return {
            "type": "currency",
            "amount": amount,
            "detail": "",
            "tx_id": tx_id,
            "description": f"💰 {format_money(amount)}",
        }

    async def revoke(self, granted: dict, user_id: int) -> None:
        await economy.remove_wallet(user_id, int(granted["amount"]), spend=False)


class StockRewardHandler:
    reward_type = "stock"

    def parse(self, value: str) -> dict:
        parts = str(value).split(":", 1)
        if len(parts) != 2:
            raise PromoRewardError("Stock reward needs <code>symbol:quantity</code>.")
        symbol = "".join(ch for ch in parts[0].strip().upper() if ch.isalnum())
        if not symbol:
            raise PromoRewardError("Stock reward needs a valid symbol.")
        try:
            qty = float(parts[1].strip())
        except (ValueError, TypeError):
            raise PromoRewardError("Stock reward quantity must be a number.")
        if not math.isfinite(qty) or qty <= 0:
            raise PromoRewardError("Stock reward quantity must be positive.")
        qty = round(qty, 6)
        return {"type": "stock", "symbol": symbol, "quantity": qty}

    async def validate(self, reward: dict, user_id: int | None) -> None:
        try:
            await stocks_service.get_asset(reward["symbol"])
        except Exception as exc:
            raise PromoRewardError(
                f"Stock <code>{html_escape(reward['symbol'])}</code> is not available."
            ) from exc

    async def grant(self, reward: dict, user_id: int, promo: dict) -> dict:
        result = await stocks_service.grant_stock(
            user_id,
            reward["symbol"],
            str(reward["quantity"]),
            promo_id=promo["promo_id"],
            promo_code=promo["code"],
        )
        return {
            "type": "stock",
            "amount": result["quantity"],
            "detail": result["symbol"],
            "tx_id": result["tx_id"],
            "description": f"📈 {result['symbol']} × {format_qty(result['quantity'])}",
        }

    async def revoke(self, granted: dict, user_id: int) -> None:
        await stocks_service.revoke_grant_stock(user_id, granted["detail"], float(granted["amount"]))


class AssetRewardHandler:
    reward_type = "asset"

    def parse(self, value: str) -> dict:
        parts = str(value).split(":", 1)
        if len(parts) != 2:
            raise PromoRewardError("Asset reward needs <code>asset_id:quantity</code>.")
        ref = parts[0].strip()
        if not ref:
            raise PromoRewardError("Asset reward needs a valid asset id.")
        try:
            qty = float(parts[1].strip())
        except (ValueError, TypeError):
            raise PromoRewardError("Asset reward quantity must be a number.")
        if not math.isfinite(qty) or qty <= 0:
            raise PromoRewardError("Asset reward quantity must be positive.")
        return {"type": "asset", "asset_id": ref, "quantity": qty}

    async def validate(self, reward: dict, user_id: int | None) -> None:
        asset = await assets_db.get_asset_by_id(reward["asset_id"])
        if asset is None:
            asset = await assets_db.get_asset(reward["asset_id"])
        if asset is None or not asset.get("is_active") or not asset.get("is_tradeable"):
            raise PromoRewardError(
                f"Asset <code>{html_escape(reward['asset_id'])}</code> is not available."
            )

    async def grant(self, reward: dict, user_id: int, promo: dict) -> dict:
        result = await assets_service.grant_asset(
            user_id,
            reward["asset_id"],
            str(reward["quantity"]),
            promo_id=promo["promo_id"],
            promo_code=promo["code"],
        )
        return {
            "type": "asset",
            "amount": result["quantity"],
            "detail": result["asset_id"],
            "tx_id": result["tx_id"],
            "description": f"🏠 {result['asset_id']} × {format_qty(result['quantity'])}",
        }

    async def revoke(self, granted: dict, user_id: int) -> None:
        await assets_service.revoke_grant_asset(user_id, granted["detail"], float(granted["amount"]))


class PromoRewardRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, object] = {}
        for handler in (CurrencyRewardHandler(), StockRewardHandler(), AssetRewardHandler()):
            self.register(handler)

    def register(self, handler) -> None:
        self._handlers[handler.reward_type] = handler

    def handler_for(self, reward_type: str):
        return self._handlers.get(reward_type)


registry = PromoRewardRegistry()

_SAFE_ERRORS = (PromoRewardError, EconomyError, AssetError, MoneyError)


def parse_reward_tokens(tokens: list[str]) -> list[dict]:
    """Parse ``rs:500 stock:BTC:0.01 asset:AST-00021:1`` style tokens."""
    rewards: list[dict] = []
    for token in tokens:
        prefix, _, value = token.partition(":")
        key = ALIASES.get(prefix.strip().lower(), prefix.strip().lower())
        handler = registry.handler_for(key)
        if handler is None:
            raise PromoRewardError(f"Unknown reward type: <code>{html_escape(prefix.strip())}</code>.")
        rewards.append(handler.parse(value))
    if not rewards:
        raise PromoRewardError("Provide at least one reward.")
    return rewards


async def validate_bundle(rewards: list[dict]) -> None:
    """Pre-flight: every reward must reference a currently available asset."""
    for reward in rewards:
        handler = registry.handler_for(reward["type"])
        if handler is None:
            raise PromoRewardError(f"Unknown reward type: <code>{html_escape(str(reward['type']))}</code>.")
        await handler.validate(reward, None)


async def grant_bundle(rewards: list[dict], user_id: int, promo: dict) -> list[dict]:
    """Grant every reward atomically-ish with compensating rollback.

    All rewards are validated up-front, then granted in order.  If any grant
    fails, every already-granted reward is compensated (currency/spend reverse)
    before the error propagates — a redemption never half-succeeds.
    """
    for reward in rewards:
        handler = registry.handler_for(reward["type"])
        if handler is None:
            raise PromoRewardError(f"Unknown reward type: <code>{html_escape(str(reward['type']))}</code>.")
        await handler.validate(reward, user_id)

    granted: list[dict] = []
    try:
        for reward in rewards:
            handler = registry.handler_for(reward["type"])
            result = await handler.grant(reward, user_id, promo)
            result.setdefault("type", reward["type"])
            granted.append(result)
    except _SAFE_ERRORS:
        await _rollback(granted, user_id)
        raise
    except Exception:
        logger.exception("promo grant failed mid-bundle")
        await _rollback(granted, user_id)
        raise PromoRewardError("Could not grant the promo rewards. Please try again later.")
    return granted


async def _rollback(granted: list[dict], user_id: int) -> None:
    for result in reversed(granted):
        handler = registry.handler_for(result["type"])
        if handler is None:
            continue
        try:
            await handler.revoke(result, user_id)
        except Exception:
            logger.exception("failed to roll back granted reward type=%s", result["type"])
