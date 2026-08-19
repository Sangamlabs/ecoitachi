"""Promo code engine.

Owns every business rule: code normalization, expiry/limit parsing, promo
lifecycle (create / edit / disable), audit stats and the single atomic
redemption flow (reserve slot -> insert pending redemption -> grant bundle ->
mark completed, with full compensating rollback on any failure).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid

from pymongo.errors import DuplicateKeyError

from database import promos as promos_db
from database import users as users_db
from services.promo_rewards import (
    PromoRewardError,
    describe_reward,
    grant_bundle,
    parse_reward_tokens,
    validate_bundle,
)

logger = logging.getLogger(__name__)

CODE_MIN_LEN = 3
CODE_MAX_LEN = 20

LIFETIME_WORDS = {"lifetime", "unlimited", "forever", "never", "inf", "0"}

_UNIT_SECONDS = {
    "min": 60,
    "m": 60,
    "hr": 3600,
    "h": 3600,
    "day": 86400,
    "days": 86400,
    "d": 86400,
    "week": 604800,
    "wk": 604800,
    "w": 604800,
    "month": 2592000,
    "mon": 2592000,
    "mo": 2592000,
    "yr": 31536000,
    "y": 31536000,
}
_UNIT_LABELS = {
    "min": "minute",
    "hr": "hour",
    "day": "day",
    "week": "week",
    "month": "month",
    "yr": "year",
}
_CANONICAL = {"m": "min", "h": "hr", "days": "day", "d": "day", "wk": "week", "w": "week",
              "mon": "month", "mo": "month", "y": "yr"}
_DURATION_RE = re.compile(r"^(\d+)\s*(min|m|hr|h|day|days|d|week|wk|w|month|mon|mo|yr|y)$")


class PromoError(Exception):
    """Base promo error (safe to show)."""


class PromoNotFound(PromoError):
    pass


class PromoInactive(PromoError):
    pass


class PromoExpired(PromoError):
    pass


class PromoLimitReached(PromoError):
    pass


class PromoAlreadyUsed(PromoError):
    pass


class PromoInvalidArgument(PromoError):
    pass


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def normalize_code(raw) -> str:
    """Upper-case, alphanumeric-only, 3-20 chars."""
    code = "".join(ch for ch in str(raw).upper() if ch.isalnum())
    if not (CODE_MIN_LEN <= len(code) <= CODE_MAX_LEN):
        raise PromoInvalidArgument(
            f"Promo codes must be {CODE_MIN_LEN}-{CODE_MAX_LEN} letters/numbers."
        )
    return code


def _canonical(unit: str) -> str:
    return _CANONICAL.get(unit, unit)


def parse_duration(raw) -> tuple[int | None, str]:
    """Parse an expiry value into ``(expires_at, label)``.

    ``lifetime``/``unlimited``/``forever`` -> ``(None, "Lifetime")``.
    Anything else must be a number followed by min/hr/day/week/month/year.
    """
    text = str(raw).strip().lower()
    if text in LIFETIME_WORDS:
        return None, "Lifetime"
    match = _DURATION_RE.match(text)
    if not match:
        raise PromoInvalidArgument(
            "Invalid expiry. Use a number + min/hr/day/week/month/year or 'lifetime'."
        )
    n = int(match.group(1))
    if n <= 0:
        raise PromoInvalidArgument("Expiry must be positive.")
    unit = match.group(2)
    canonical = _canonical(unit)
    label = f"{n} {_UNIT_LABELS[canonical]}"
    if n != 1:
        label += "s"
    return int(time.time()) + n * _UNIT_SECONDS[unit], label


def parse_limit(raw) -> int | None:
    """Parse a redemption limit; ``None`` means unlimited."""
    text = str(raw).strip().lower()
    if text in ("unlimited", "inf", "0"):
        return None
    if text.isdigit():
        n = int(text)
        if n <= 0:
            raise PromoInvalidArgument("Limit must be positive or 'unlimited'.")
        return n
    raise PromoInvalidArgument("Invalid limit. Use a positive number or 'unlimited'.")


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


class PromoCodeCache:
    """In-memory index of active codes -> expires_at for the text detector.

    Only ever consulted as a pre-filter; the authoritative check always happens
    inside :func:`redeem`.  Invalidated on create/edit/disable/expiry and
    refreshed by the scheduler.
    """

    def __init__(self) -> None:
        self._codes: dict[str, int | None] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._codes.clear()
        self._loaded = False

    async def refresh(self) -> None:
        async with self._lock:
            rows = await promos_db.list_active_cache()
            self._codes = {r["normalized_code"]: r.get("expires_at") for r in rows}
            self._loaded = True

    async def candidates(self, tokens: list[str]) -> list[str]:
        """Return the subset of tokens that match a known active code."""
        if not tokens:
            return []
        if not self._loaded:
            await self.refresh()
        seen: set[str] = set()
        result: list[str] = []
        for token in tokens:
            if token in self._codes and token not in seen:
                seen.add(token)
                result.append(token)
        return result


cache = PromoCodeCache()


# --------------------------------------------------------------------------- #
# Promo lifecycle
# --------------------------------------------------------------------------- #


async def create_promo(actor: int, code, expiry_raw, limit_raw, reward_tokens) -> dict:
    normalized = normalize_code(code)
    if await promos_db.get_promo_by_code(normalized):
        raise PromoInvalidArgument(f"A promo with the code <code>{normalized}</code> already exists.")
    rewards = parse_reward_tokens(reward_tokens)
    await validate_bundle(rewards)
    expires_at, expiry_label = parse_duration(expiry_raw)
    max_redemptions = parse_limit(limit_raw)
    promo_id = f"PRM-{uuid.uuid4().hex[:8].upper()}"
    now = int(time.time())
    doc = {
        "_id": promo_id,
        "promo_id": promo_id,
        "code": normalized,
        "normalized_code": normalized,
        "rewards": rewards,
        "max_redemptions": max_redemptions,
        "redeemed_count": 0,
        "per_user_limit": 1,
        "expires_at": expires_at,
        "expiry_label": expiry_label,
        "is_active": True,
        "created_by": actor,
        "created_at": now,
        "updated_at": now,
    }
    await promos_db.insert_promo(doc)
    cache.invalidate()
    return doc


async def disable_promo(actor: int, code) -> dict:
    normalized = normalize_code(code)
    promo = await promos_db.get_promo_by_code(normalized)
    if promo is None:
        raise PromoNotFound(f"No promo found with code <code>{normalized}</code>.")
    await promos_db.update_promo(promo["_id"], {"is_active": False})
    cache.invalidate()
    return await promos_db.get_promo(promo["_id"])


EDITABLE_FIELDS = ("expiry", "limit", "max_redemptions", "active", "reward", "rewards")


async def edit_promo(actor: int, code, field, value_tokens) -> dict:
    normalized = normalize_code(code)
    promo = await promos_db.get_promo_by_code(normalized)
    if promo is None:
        raise PromoNotFound(f"No promo found with code <code>{normalized}</code>.")
    field = str(field).strip().lower()
    if field == "expiry":
        expires_at, label = parse_duration(" ".join(value_tokens))
        fields = {"expires_at": expires_at, "expiry_label": label}
    elif field in ("limit", "max_redemptions"):
        fields = {"max_redemptions": parse_limit(" ".join(value_tokens))}
    elif field == "active":
        value = " ".join(value_tokens).strip().lower()
        if value in ("on", "1", "true", "yes", "enable"):
            fields = {"is_active": True}
        elif value in ("off", "0", "false", "no", "disable"):
            fields = {"is_active": False}
        else:
            raise PromoInvalidArgument("Use 'on' or 'off' for the active field.")
    elif field in ("reward", "rewards"):
        if not value_tokens:
            raise PromoInvalidArgument("Provide reward tokens after 'reward'.")
        new_rewards = parse_reward_tokens(value_tokens)
        await validate_bundle(new_rewards)
        fields = {"rewards": new_rewards}
    else:
        raise PromoInvalidArgument(
            f"Unknown field. Editable fields: {', '.join(EDITABLE_FIELDS)}."
        )
    await promos_db.update_promo(promo["_id"], fields)
    cache.invalidate()
    return await promos_db.get_promo(promo["_id"])


async def get_promo_info(code) -> dict:
    normalized = normalize_code(code)
    promo = await promos_db.get_promo_by_code(normalized)
    if promo is None:
        raise PromoNotFound(f"No promo found with code <code>{normalized}</code>.")
    return promo


async def list_promos(status: str = "all", page: int = 1, per_page: int = 10):
    if status not in ("active", "expired", "inactive", "all"):
        raise PromoInvalidArgument("Status must be active, expired, inactive or all.")
    return await promos_db.list_promos(
        status, max(1, int(page)), max(1, min(50, int(per_page)))
    )


async def get_promo_stats(code) -> dict:
    promo = await get_promo_info(code)
    totals = await promos_db.aggregate_granted(promo["_id"])
    currency_total = sum(r["total"] for r in totals if r["type"] == "currency")
    stock_rows = [(r["detail"], r["total"]) for r in totals if r["type"] == "stock"]
    asset_rows = [(r["detail"], r["total"]) for r in totals if r["type"] == "asset"]
    last = await promos_db.latest_redemption(promo["_id"])
    return {
        "promo": promo,
        "total_redemptions": await promos_db.count_completed(promo["_id"]),
        "unique_users": await promos_db.unique_users(promo["_id"]),
        "remaining": (
            None
            if promo.get("max_redemptions") is None
            else max(0, int(promo["max_redemptions"]) - int(promo.get("redeemed_count", 0)))
        ),
        "currency_total": currency_total,
        "stock_rows": stock_rows,
        "asset_rows": asset_rows,
        "last_redeemed_at": last.get("redeemed_at") if last else None,
    }


# --------------------------------------------------------------------------- #
# Redemption
# --------------------------------------------------------------------------- #


async def redeem(user_id: int, code, chat_id: int | None = None) -> dict:
    """Redeem ``code`` for ``user_id`` atomically.

    Returns ``{"promo": doc, "granted": [...], "redemption_id": id}`` on success.
    Raises PromoNotFound / PromoInactive / PromoExpired / PromoLimitReached /
    PromoAlreadyUsed / PromoRewardError.  The total limit is consumed only on a
    successful redemption; failed attempts are fully rolled back.
    """
    normalized = normalize_code(code)
    promo = await promos_db.get_promo_by_code(normalized)
    if promo is None:
        raise PromoNotFound()
    now = int(time.time())
    if not promo.get("is_active"):
        raise PromoInactive()
    if promo.get("expires_at") is not None and now >= promo["expires_at"]:
        raise PromoExpired()

    reserved = await promos_db.reserve_slot(promo["_id"], now)
    if reserved is None:
        fresh = await promos_db.get_promo_by_code(normalized)
        if fresh is not None:
            if not fresh.get("is_active"):
                raise PromoInactive()
            if fresh.get("expires_at") is not None and now >= fresh["expires_at"]:
                raise PromoExpired()
        raise PromoLimitReached()

    await users_db.get_or_create_user(user_id)

    redemption_id = f"RD-{uuid.uuid4().hex[:10].upper()}"
    redemption = {
        "_id": redemption_id,
        "promo_id": promo["_id"],
        "promo_code": normalized,
        "user_id": user_id,
        "status": "pending",
        "chat_id": chat_id,
        "redeemed_at": now,
        "updated_at": now,
    }
    try:
        await promos_db.insert_redemption(redemption)
    except DuplicateKeyError:
        await promos_db.release_slot(promo["_id"])
        raise PromoAlreadyUsed()

    try:
        granted = await grant_bundle(promo["rewards"], user_id, promo)
    except Exception:
        await promos_db.delete_redemption(redemption_id)
        await promos_db.release_slot(promo["_id"])
        raise

    transaction_ids = [g["tx_id"] for g in granted if g.get("tx_id")]
    completed = {
        "status": "completed",
        "rewards_granted": [
            {
                "type": g["type"],
                "amount": g.get("amount", 0),
                "detail": g.get("detail", ""),
                "description": g.get("description", ""),
                "tx_id": g.get("tx_id"),
            }
            for g in granted
        ],
        "transaction_ids": transaction_ids,
        "completed_at": now,
        "updated_at": now,
    }
    try:
        await promos_db.update_redemption(redemption_id, completed)
    except Exception:
        logger.exception(
            "promo redemption update failed; retrying user=%s promo=%s",
            user_id,
            promo["_id"],
        )
        try:
            await promos_db.update_redemption(redemption_id, completed)
        except Exception:
            logger.exception(
                "promo redemption update retry failed user=%s promo=%s",
                user_id,
                promo["_id"],
            )
            # Money was already granted; do NOT roll back or delete the
            # redemption (that would allow a double grant).  The unique
            # (promo_id, user_id) index still blocks a second redemption.
            raise PromoRewardError(
                "Reward was granted but the confirmation record could not be "
                "saved. Please contact an admin."
            ) from None
    return {"promo": promo, "granted": granted, "redemption_id": redemption_id}


async def expire_overdue() -> int:
    """Disable promos past their expires_at and refresh the detector cache."""
    expired = await promos_db.find_expired_active()
    for promo in expired:
        await promos_db.update_promo(promo["_id"], {"is_active": False})
    if expired:
        cache.invalidate()
    return len(expired)


def reward_display(rewards: list[dict]) -> list[str]:
    """HTML lines describing a promo's reward bundle."""
    return [describe_reward(reward) for reward in rewards]
