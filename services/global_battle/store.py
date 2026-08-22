"""Global Battle Store Service.

Manages the global store, purchases, and item grants.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from database import global_battle as gb_db
from services import transaction as tx_service
from . import currency as currency_service
from . import inventory as inventory_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

async def get_store_items(active_only: bool = True) -> list[dict[str, Any]]:
    """Get all purchasable items from the store."""
    from . import items as items_service
    
    # Get all active items except special weapons
    all_items = await items_service.list_all_items(active_only=True)
    
    # Filter out special weapons (they're not purchasable in store)
    store_items = [item for item in all_items if item.get("category") != "special"]
    
    if not active_only:
        return store_items
    
    return [item for item in store_items if item.get("active", True)]


async def get_store_by_category(category: str, active_only: bool = True) -> list[dict[str, Any]]:
    """Get store items filtered by category."""
    store_items = await get_store_items(active_only)
    return [item for item in store_items if item.get("category") == category]


# ---------------------------------------------------------------------------
# Purchase
# ---------------------------------------------------------------------------

async def purchase_item(user_id: int, item_id: str, quantity: int = 1) -> dict[str, Any]:
    """Purchase an item from the store using GB coins."""
    from . import items as items_service
    from . import currency as currency_service
    from database import global_battle as gb_db
    
    # Verify item exists and is purchasable - check both items and special weapons
    item = await items_service.get_item(item_id)
    is_special = False
    
    if not item:
        # Check if it's a special weapon
        special = await gb_db.get_special_weapon(item_id)
        if special:
            item = special
            is_special = True
        else:
            raise ValueError(f"Item {item_id} does not exist")
    
    if not item.get("active", True):
        raise ValueError("Item is not available for purchase")
    
    if is_special or item.get("category") == "special":
        raise ValueError("Special weapons cannot be purchased from the store")
    
    price = item.get("price", 0)
    if price <= 0:
        raise ValueError("Item has no price set")
    
    total_price = price * quantity
    
    # Check GB balance
    gb_balance = await currency_service.get_gb_balance(user_id)
    if gb_balance < total_price:
        from services.economy import InsufficientBalance
        raise InsufficientBalance(total_price, gb_balance)
    
    # Deduct GB coins
    await currency_service.remove_gb_coins(user_id, total_price)
    
    # Grant item to inventory
    await inventory_service.add_to_inventory(user_id, item_id, quantity)
    
    # Record transaction
    tx_id = uuid.uuid4().hex[:16]
    from services import transaction as tx_service
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.GB_PURCHASE,
        amount=total_price,
        balance_before=gb_balance,
        balance_after=gb_balance - total_price,
        metadata={
            "item_id": item_id,
            "item_name": item.get("name", "Unknown"),
            "quantity": quantity,
            "unit_price": price,
        },
        transaction_id=tx_id,
    )
    
    logger.info("User %s purchased %s x%d for %d GB", user_id, item_id, quantity, total_price)
    
    return {
        "item_id": item_id,
        "name": item.get("name", "Unknown"),
        "quantity": quantity,
        "total_price": total_price,
        "tx_id": tx_id,
        "new_gb_balance": gb_balance - total_price,
    }


# ---------------------------------------------------------------------------
# Store Preview
# ---------------------------------------------------------------------------

async def get_store_preview(user_id: int) -> dict[str, Any]:
    """Get store preview with user's GB balance."""
    gb_balance = await currency_service.get_gb_balance(user_id)
    
    weapons = await get_store_by_category("weapon")
    armor = await get_store_by_category("armor")
    utility = await get_store_by_category("utility")
    
    return {
        "gb_balance": gb_balance,
        "weapons": weapons,
        "armor": armor,
        "utility": utility,
    }