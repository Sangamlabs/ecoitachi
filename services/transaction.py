"""Transaction engine.

Every financial operation produces an audit record with a unique id, balance
snapshot and metadata.  Transaction types are constants to avoid typos.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from database import transactions as tx_db

logger = logging.getLogger(__name__)

# Transaction types
PAY = "PAY"
DEPOSIT = "DEPOSIT"
WITHDRAW = "WITHDRAW"
INTEREST = "INTEREST"
TAX = "TAX"
STOCK_BUY = "STOCK_BUY"
STOCK_SELL = "STOCK_SELL"
GAME_BET = "GAME_BET"
GAME_WIN = "GAME_WIN"
GAME_LOSS = "GAME_LOSS"
ADMIN_GIVE = "ADMIN_GIVE"
ADMIN_REMOVE = "ADMIN_REMOVE"
TAX_REWARD = "TAX_REWARD"
REWARD = "REWARD"
ROB = "ROB"
ROBBED = "ROBBED"
ASSET_BUY = "ASSET_BUY"
ASSET_SELL = "ASSET_SELL"
ASSET_LISTING_BUY = "ASSET_LISTING_BUY"
ASSET_LISTING_SALE = "ASSET_LISTING_SALE"
INTEREST_CLAIM = "INTEREST_CLAIM"
ASSET_INCOME_CLAIM = "ASSET_INCOME_CLAIM"
STOCK_INCOME_CLAIM = "STOCK_INCOME_CLAIM"


def new_transaction_id() -> str:
    return uuid.uuid4().hex[:16]


async def record(
    *,
    user_id: int,
    ttype: str,
    amount: int,
    balance_before: int,
    balance_after: int,
    metadata: dict[str, Any] | None = None,
    transaction_id: str | None = None,
) -> str:
    """Insert one transaction and return its id."""
    tx_id = transaction_id or new_transaction_id()
    doc = {
        "transaction_id": tx_id,
        "user_id": user_id,
        "type": ttype,
        "amount": amount,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "metadata": metadata or {},
        "created_at": int(time.time()),
    }
    await tx_db.insert_transaction(doc)
    return tx_id


async def get_recent(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return await tx_db.recent_by_user(user_id, limit)


async def get_recent_transfers(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    """Last ``limit`` PAY transfer logs (sent or received) for a user."""
    return await tx_db.recent_transfers_by_user(user_id, limit)
