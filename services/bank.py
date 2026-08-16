"""Bank service - deposit / withdraw with full transaction + tax logging.

Business logic sits here; handlers only parse input and format output.
"""

from __future__ import annotations

import logging
from typing import Any

from database import bank as bank_db
from services import economy, transaction as tx_service, tax as tax_service
from services.economy import EconomyError
from utils.money import MoneyError

logger = logging.getLogger(__name__)


async def get_bank_view(user_id: int) -> dict[str, Any]:
    """Bank overview: balances + current settings + tax pool size."""
    settings = await bank_db.get_bank_settings()
    pool = await tax_service.get_pool_size()
    balance = await economy.get_balance(user_id)
    return {"settings": settings, "tax_pool": pool, "balance": balance}


async def deposit(user_id: int, amount: int) -> dict[str, Any]:
    """Move RS wallet → bank and log the transaction."""
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    before = await economy.get_balance(user_id)
    result = await economy.deposit(user_id, amount)
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.DEPOSIT,
        amount=amount,
        balance_before=before["wallet"],
        balance_after=result["wallet"],
        metadata={"bank_after": result["bank"]},
    )
    return result


async def withdraw(user_id: int, amount: int) -> dict[str, Any]:
    """Move RS bank → wallet, charging the configured tax.

    The tax is captured into the pool (not deleted) and both WITHDRAW and TAX
    transactions are recorded.
    """
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    settings = await bank_db.get_bank_settings()
    tax_rate = float(settings.get("withdrawal_tax_rate", 5.0))
    before = await economy.get_balance(user_id)
    result = await economy.withdraw(user_id, amount, tax_rate)

    if result["tax"] > 0:
        await tax_service.collect(user_id, result["tax"])
        await tx_service.record(
            user_id=user_id,
            ttype=tx_service.TAX,
            amount=result["tax"],
            balance_before=before["bank"],
            balance_after=result["bank"],
            metadata={"tax_rate": tax_rate, "gross": amount},
        )
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.WITHDRAW,
        amount=result["received"],
        balance_before=before["bank"],
        balance_after=result["bank"],
        metadata={"gross": amount, "tax": result["tax"]},
    )
    return result


async def set_interest_rate(rate: float, actor_id: int) -> None:
    if not (0 <= rate <= 100):
        raise EconomyError("Interest rate must be between 0 and 100%.")
    await bank_db.set_interest_rate(rate)
    logger.info("interest rate set to %s%% by admin %s", rate, actor_id)


async def set_tax_rate(rate: float, actor_id: int) -> None:
    if not (0 <= rate <= 100):
        raise EconomyError("Tax rate must be between 0 and 100%.")
    await bank_db.set_withdrawal_tax_rate(rate)
    logger.info("tax rate set to %s%% by admin %s", rate, actor_id)


async def get_bank_settings() -> dict[str, Any]:
    return await bank_db.get_bank_settings()
