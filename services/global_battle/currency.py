"""Global Battle Currency System.

GB Coins — separate currency from RS.
Conversion: 10,000 RS = 100 GB (1 GB = 100 RS).
Rate is configurable in global_battle settings.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from database import global_battle as gb_db, users as users_db
from database.mongo import mongo
from services import economy, settings as settings_service, transaction as tx_service
from services.economy import EconomyError, InsufficientBalance, ensure_active
from utils.money import MoneyError, format_money
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)


class CurrencyError(EconomyError):
    """Currency conversion error."""


async def get_conversion_rate() -> int:
    """Return RS per GB (default 100)."""
    cfg = await settings_service.get_global_battle_config()
    return int(cfg.get("rs_per_gb", 100))


async def get_gb_balance(user_id: int) -> int:
    """Get user's GB coin balance."""
    profile = await gb_db.get_profile(user_id)
    if not profile:
        return 0
    return profile.get("gb_coins", 0)


async def add_gb_coins(user_id: int, amount: int, *, earn: bool = True) -> int:
    """Add GB coins to user's profile atomically."""
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    # Ensure profile exists
    await gb_db.get_or_create_profile(user_id)
    profile = await gb_db.inc_profile_field(user_id, "gb_coins", amount)
    return profile.get("gb_coins", 0) if profile else amount


async def remove_gb_coins(user_id: int, amount: int) -> int:
    """Remove GB coins from user's profile atomically (with balance guard)."""
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    # Use atomic update with balance guard
    result = await mongo.db[gb_db.PROFILES].find_one_and_update(
        {"user_id": user_id, "gb_coins": {"$gte": amount}},
        {"$inc": {"gb_coins": -amount}, "$set": {"updated_at": int(time.time())}},
        return_document=True,
    )
    if result is None:
        raise InsufficientBalance(amount, (await get_gb_balance(user_id)))
    return result.get("gb_coins", 0)


async def convert_rs_to_gb(user_id: int, rs_amount: int) -> dict[str, Any]:
    """
    Convert RS to GB coins atomically.

    Returns: {gb_received, rs_spent, tx_id, new_gb_balance, new_rs_wallet}
    """
    if rs_amount <= 0:
        raise MoneyError("Amount must be positive.")

    rate = await get_conversion_rate()
    if rs_amount % rate != 0:
        raise CurrencyError(f"Amount must be a multiple of {rate} RS (1 GB = {rate} RS).")

    gb_amount = rs_amount // rate

    # Verify user exists and is active
    user = await users_db.get_user(user_id)
    if not user:
        raise EconomyError("User not registered. Send /start to the bot to register.")
    await ensure_active(user)

    # Check RS wallet balance
    if user.get("wallet", 0) < rs_amount:
        raise InsufficientBalance(rs_amount, user.get("wallet", 0))

    # Atomic RS deduction + GB credit + transaction record
    # Use a two-phase approach with balance guards
    rs_before = user.get("wallet", 0)
    gb_before = await get_gb_balance(user_id)

    # Deduct RS from wallet
    await economy.remove_wallet(user_id, rs_amount, spend=True)

    # Credit GB coins
    await add_gb_coins(user_id, gb_amount, earn=True)

    # Record transaction
    tx_id = uuid.uuid4().hex[:16]
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.GB_CONVERT,
        amount=gb_amount,
        balance_before=gb_before,
        balance_after=gb_before + gb_amount,
        metadata={
            "rs_spent": rs_amount,
            "rs_before": rs_before,
            "rs_after": rs_before - rs_amount,
            "rate": rate,
        },
        transaction_id=tx_id,
    )

    # Also record RS side for audit
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.GB_CONVERT_RS,
        amount=rs_amount,
        balance_before=rs_before,
        balance_after=rs_before - rs_amount,
        metadata={"gb_received": gb_amount, "rate": rate},
        transaction_id=f"{tx_id}r",
    )

    logger.info("User %s converted %s RS to %s GB (rate: %s)", user_id, rs_amount, gb_amount, rate)

    return {
        "gb_received": gb_amount,
        "rs_spent": rs_amount,
        "rate": rate,
        "tx_id": tx_id,
        "new_gb_balance": gb_before + gb_amount,
        "new_rs_wallet": rs_before - rs_amount,
    }