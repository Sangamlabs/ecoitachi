"""Central economy engine.

No handler, game module or admin command may modify wallet/bank balances
directly.  Every financial change flows through this service using atomic
MongoDB updates with balance guards so concurrent commands cannot double-spend.
"""

from __future__ import annotations

import logging
from typing import Any

from database import users as users_db
from database.mongo import mongo
from utils.money import MoneyError, format_money, percentage

logger = logging.getLogger(__name__)


class EconomyError(Exception):
    """Base class for economy failures (safe to show to users)."""


class InsufficientBalance(EconomyError):
    def __init__(self, needed: int, balance: int) -> None:
        super().__init__(
            f"Insufficient balance. You have {format_money(balance)}, "
            f"needed {format_money(needed)}."
        )
        self.needed = needed
        self.balance = balance


class UserNotFound(EconomyError):
    pass


class FrozenUser(EconomyError):
    pass


class BannedUser(EconomyError):
    pass


async def _require_user(user_id: int) -> dict[str, Any]:
    user = await users_db.get_user(user_id)
    if user is None:
        raise UserNotFound("User not registered. Ask them to start the bot.")
    if user.get("is_banned"):
        raise BannedUser("This user is banned from the economy.")
    return user


async def ensure_active(user: dict[str, Any]) -> None:
    if user.get("is_banned"):
        raise BannedUser("You are banned from the economy.")
    if user.get("is_frozen"):
        raise FrozenUser("Your account is frozen. Contact an admin.")


async def get_balance(user_id: int) -> dict[str, int]:
    """Return wallet / bank / net worth for a user (creating if needed)."""
    user = await users_db.get_or_create_user(user_id)
    return {
        "wallet": user.get("wallet", 0),
        "bank": user.get("bank", 0),
        "net_worth": user.get("wallet", 0) + user.get("bank", 0),
    }


async def add_wallet(
    user_id: int,
    amount: int,
    *,
    earn: bool = True,
    from_transaction: str | None = None,
) -> dict[str, int]:
    """Add UN to a user's wallet atomically. ``amount`` must be positive."""
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    user = await _require_user(user_id)
    await ensure_active(user)
    inc: dict[str, int] = {"wallet": amount}
    if earn:
        inc["total_earned"] = amount
    if earn:
        inc["monthly_earnings"] = amount
    await users_db.inc(user_id, inc)
    balance = await users_db.get_user(user_id)
    return {"wallet": balance["wallet"], "bank": balance["bank"]}


async def remove_wallet(
    user_id: int,
    amount: int,
    *,
    spend: bool = True,
    from_transaction: str | None = None,
) -> dict[str, int]:
    """Atomically remove UN from a user's wallet.

    Raises :class:`InsufficientBalance` when the wallet cannot cover ``amount``.
    """
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    user = await _require_user(user_id)
    await ensure_active(user)
    inc: dict[str, int] = {"wallet": -amount}
    if spend:
        inc["total_spent"] = amount
    result = await mongo_db_update_guarded(user_id, amount, inc)
    if result is None:
        raise InsufficientBalance(amount, user.get("wallet", 0))
    return {"wallet": result.get("wallet", 0), "bank": result.get("bank", 0)}


async def transfer(
    sender_id: int,
    receiver_id: int,
    amount: int,
    *,
    tax: int = 0,
) -> dict[str, Any]:
    """Move UN between two users atomically and durably.

    The sender is charged ``amount + tax`` (tax goes to the tax pool) while
    the receiver is credited ``amount``.  Deduction uses a balance guard; the
    credit is written with retry, and a failed credit refunds the sender so
    no money is ever lost or created silently.

    Returns metadata: ``{sender, receiver, amount, tax, ...}``.
    """
    if sender_id == receiver_id:
        raise EconomyError("You cannot pay yourself.")
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    if tax < 0:
        raise MoneyError("Tax cannot be negative.")
    total = amount + tax
    sender = await _require_user(sender_id)
    receiver = await _require_user(receiver_id)
    await ensure_active(sender)
    await ensure_active(receiver)

    result = await mongo_db_update_guarded(sender_id, total, {"wallet": -total, "total_spent": total})
    if result is None:
        raise InsufficientBalance(total, sender.get("wallet", 0))

    credited = False
    try:
        await users_db.inc(receiver_id, {"wallet": amount, "total_earned": amount})
        credited = True
    except Exception:
        logger.exception("crediting %s failed; refunding sender %s", receiver_id, sender_id)
        await users_db.inc(sender_id, {"wallet": total})
    if not credited:
        raise EconomyError("Payment failed; money was refunded. Try again.")

    if tax > 0:
        from services import tax as tax_service

        await tax_service.collect(sender_id, tax)

    return {
        "sender": sender_id,
        "receiver": receiver_id,
        "amount": amount,
        "tax": tax,
        "total": total,
        "sender_wallet": (await users_db.get_user(sender_id)).get("wallet", 0),
        "receiver_wallet": (await users_db.get_user(receiver_id)).get("wallet", 0),
    }


async def admin_give(user_id: int, amount: int, actor_id: int) -> int:
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    await users_db.inc(user_id, {"wallet": amount, "total_earned": amount, "monthly_earnings": amount})
    return amount


async def admin_remove(user_id: int, amount: int, actor_id: int) -> None:
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    user = await _require_user(user_id)
    result = await mongo_db_update_guarded(user_id, amount, {"wallet": -amount, "total_spent": amount})
    if result is None:
        raise InsufficientBalance(amount, user.get("wallet", 0))


async def get_user_economy_snapshot(user_id: int) -> dict[str, Any]:
    """Read-only snapshot of a user's economy for security dumps / recovery."""
    user = await users_db.get_user(user_id)
    if user is None:
        return {"wallet": None, "bank": None, "stocks": {}, "assets": {}}

    from database import asset_holdings as ah_db, stocks as stocks_db

    stocks: dict[str, Any] = {}
    for holding in await stocks_db.get_user_holdings(user_id):
        stocks[holding["symbol"]] = holding.get("quantity", 0)

    assets: dict[str, Any] = {}
    for holding in await ah_db.get_user_holdings(user_id):
        assets[holding["asset_id"]] = {
            "quantity": holding.get("quantity", 0),
            "cost": holding.get("total_invested", 0),
        }

    return {
        "wallet": int(user.get("wallet", 0)),
        "bank": int(user.get("bank", 0)),
        "stocks": stocks,
        "assets": assets,
    }


async def set_user_balance(user_id: int, field: str, value: int) -> None:
    """Atomically set one balance field (``wallet`` / ``bank``) to ``value``.

    Used by manual security recovery to restore a saved snapshot.  Guards
    against negative balances and unknown fields.
    """
    if field not in ("wallet", "bank"):
        raise MoneyError(f"Unknown balance field: {field}")
    value = int(value)
    if value < 0:
        raise MoneyError("Balance cannot be negative.")
    if await users_db.get_user(user_id) is None:
        raise UserNotFound("User not registered. Ask them to start the bot.")
    await users_db.set_user_field(user_id, field, value)


async def set_user_stock(user_id: int, symbol: str, quantity: float) -> None:
    """Restore an exact stock holding quantity (used by dump recovery)."""
    from database import stocks as stocks_db

    await stocks_db.set_holding_quantity(user_id, symbol, quantity)


async def set_user_asset(user_id: int, asset_id: str, quantity: float) -> None:
    """Restore an exact asset holding quantity (used by dump recovery)."""
    from database import asset_holdings as ah_db

    await ah_db.set_holding_quantity(user_id, asset_id, quantity)


async def mongo_db_update_guarded(user_id: int, amount: int, inc: dict[str, int]):
    """Run a guarded atomic wallet update and return the updated user doc."""
    return await mongo.db[users_db.COLLECTION].find_one_and_update(
        {"user_id": user_id, "wallet": {"$gte": amount}},
        {"$inc": inc, "$set": {"updated_at": int(__import__("time").time())}},
        return_document=True,
    )


async def deposit(user_id: int, amount: int) -> dict[str, int]:
    """Wallet → Bank (atomic, guarded on wallet)."""
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    user = await _require_user(user_id)
    await ensure_active(user)
    result = await mongo.db[users_db.COLLECTION].find_one_and_update(
        {"user_id": user_id, "wallet": {"$gte": amount}},
        {
            "$inc": {"wallet": -amount, "bank": amount, "total_deposited": amount},
            "$set": {"updated_at": int(__import__("time").time())},
        },
        return_document=True,
    )
    if result is None:
        raise InsufficientBalance(amount, user.get("wallet", 0))
    return {"wallet": result.get("wallet", 0), "bank": result.get("bank", 0)}


async def bank_debit(user_id: int, amount: int) -> dict[str, Any]:
    """Atomically remove UN from a user's bank (no tax) with a balance guard.

    Used by theft-style mechanics (e.g. rob) that must debit the victim's bank
    directly instead of routing through a taxed withdrawal.
    """
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    user = await _require_user(user_id)
    result = await mongo.db[users_db.COLLECTION].find_one_and_update(
        {"user_id": user_id, "bank": {"$gte": amount}},
        {"$inc": {"bank": -amount}, "$set": {"updated_at": int(__import__("time").time())}},
        return_document=True,
    )
    if result is None:
        raise InsufficientBalance(amount, user.get("bank", 0))
    return {"wallet": result.get("wallet", 0), "bank": result.get("bank", 0)}


async def withdraw(user_id: int, amount: int, tax_rate: float) -> dict[str, Any]:
    """Bank → Wallet applying withdrawal tax.

    Returns ``{gross, tax, received, wallet, bank}``.  The tax is captured into
    the pool by the bank service (see services.bank / services.tax).
    """
    if amount <= 0:
        raise MoneyError("Amount must be positive.")
    user = await _require_user(user_id)
    await ensure_active(user)
    tax = percentage(amount, tax_rate)
    received = amount - tax

    result = await mongo.db[users_db.COLLECTION].find_one_and_update(
        {"user_id": user_id, "bank": {"$gte": amount}},
        {
            "$inc": {
                "bank": -amount,
                "wallet": received,
                "total_withdrawn": amount,
                "total_tax_paid": tax,
            },
            "$set": {"updated_at": int(__import__("time").time())},
        },
        return_document=True,
    )
    if result is None:
        raise InsufficientBalance(amount, user.get("bank", 0))
    return {
        "gross": amount,
        "tax": tax,
        "received": received,
        "wallet": result.get("wallet", 0),
        "bank": result.get("bank", 0),
    }
