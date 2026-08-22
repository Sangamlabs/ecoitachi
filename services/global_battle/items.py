"""Global Battle Items Service.

Manages weapon, armor, utility, and special weapon definitions.
All items have unique IDs and are stored in global_items collection.
"""

from __future__ import annotations

import logging
from typing import Any

from database import global_battle as gb_db

logger = logging.getLogger(__name__)

# Item categories
CATEGORY_WEAPON = "weapon"
CATEGORY_ARMOR = "armor"
CATEGORY_UTILITY = "utility"
CATEGORY_SPECIAL = "special"

CATEGORIES = (CATEGORY_WEAPON, CATEGORY_ARMOR, CATEGORY_UTILITY, CATEGORY_SPECIAL)


# ---------------------------------------------------------------------------
# Weapon Definitions
# ---------------------------------------------------------------------------

async def create_weapon(
    item_id: str,
    name: str,
    rarity: str,
    damage: int,
    accuracy: float,
    fire_rate: int,
    ammo: int,
    durability: int,
    price: int,
    description: str = "",
    active: bool = True,
) -> str:
    """Create a new weapon definition."""
    doc = {
        "item_id": item_id,
        "name": name,
        "category": CATEGORY_WEAPON,
        "rarity": rarity,
        "damage": damage,
        "accuracy": accuracy,
        "fire_rate": fire_rate,
        "ammo": ammo,
        "durability": durability,
        "price": price,
        "description": description,
        "active": active,
    }
    return await gb_db.create_item(doc)


async def get_weapon(item_id: str) -> dict[str, Any] | None:
    """Get a weapon by ID."""
    item = await gb_db.get_item(item_id)
    if item and item.get("category") == CATEGORY_WEAPON:
        return item
    return None


async def list_weapons(active_only: bool = True) -> list[dict[str, Any]]:
    """List all weapons."""
    return await gb_db.get_items_by_category(CATEGORY_WEAPON, active_only)


async def update_weapon(item_id: str, **fields: Any) -> bool:
    """Update a weapon definition."""
    return await gb_db.update_item(item_id, **fields)


async def delete_weapon(item_id: str) -> bool:
    """Delete a weapon definition."""
    return await gb_db.delete_item(item_id)


# ---------------------------------------------------------------------------
# Armor Definitions
# ---------------------------------------------------------------------------

async def create_armor(
    item_id: str,
    name: str,
    rarity: str,
    defense: int,
    base_durability: int,
    price: int,
    description: str = "",
    active: bool = True,
) -> str:
    """Create a new armor definition."""
    doc = {
        "item_id": item_id,
        "name": name,
        "category": CATEGORY_ARMOR,
        "rarity": rarity,
        "defense": defense,
        "base_durability": base_durability,
        "price": price,
        "description": description,
        "active": active,
    }
    return await gb_db.create_item(doc)


async def get_armor(item_id: str) -> dict[str, Any] | None:
    """Get an armor by ID."""
    item = await gb_db.get_item(item_id)
    if item and item.get("category") == CATEGORY_ARMOR:
        return item
    return None


async def list_armor(active_only: bool = True) -> list[dict[str, Any]]:
    """List all armor."""
    return await gb_db.get_items_by_category(CATEGORY_ARMOR, active_only)


async def update_armor(item_id: str, **fields: Any) -> bool:
    """Update an armor definition."""
    return await gb_db.update_item(item_id, **fields)


async def delete_armor(item_id: str) -> bool:
    """Delete an armor definition."""
    return await gb_db.delete_item(item_id)


# ---------------------------------------------------------------------------
# Utility Items
# ---------------------------------------------------------------------------

async def create_utility(
    item_id: str,
    name: str,
    rarity: str,
    effect: str,
    value: int,
    price: int,
    description: str = "",
    active: bool = True,
) -> str:
    """Create a new utility item definition."""
    doc = {
        "item_id": item_id,
        "name": name,
        "category": CATEGORY_UTILITY,
        "rarity": rarity,
        "effect": effect,
        "value": value,
        "price": price,
        "description": description,
        "active": active,
    }
    return await gb_db.create_item(doc)


async def get_utility(item_id: str) -> dict[str, Any] | None:
    """Get a utility item by ID."""
    item = await gb_db.get_item(item_id)
    if item and item.get("category") == CATEGORY_UTILITY:
        return item
    return None


async def list_utility(active_only: bool = True) -> list[dict[str, Any]]:
    """List all utility items."""
    return await gb_db.get_items_by_category(CATEGORY_UTILITY, active_only)


# ---------------------------------------------------------------------------
# Special Weapons
# ---------------------------------------------------------------------------

async def create_special_weapon(
    special_id: str,
    name: str,
    description: str,
    damage: int,
    effect: str,
    active: bool = True,
) -> str:
    """Create a new special weapon definition."""
    doc = {
        "special_id": special_id,
        "name": name,
        "description": description,
        "damage": damage,
        "effect": effect,
        "active": active,
    }
    return await gb_db.create_special_weapon(doc)


async def list_special_weapons(active_only: bool = True) -> list[dict[str, Any]]:
    """List all special weapons."""
    return await gb_db.list_special_weapons(active_only)


async def update_special_weapon(special_id: str, **fields: Any) -> bool:
    """Update a special weapon."""
    return await gb_db.update_special_weapon(special_id, **fields)


async def delete_special_weapon(special_id: str) -> bool:
    """Delete a special weapon."""
    return await gb_db.delete_special_weapon(special_id)


async def get_special_weapon(special_id: str) -> dict[str, Any] | None:
    """Get a special weapon by ID."""
    return await gb_db.get_special_weapon(special_id)


# ---------------------------------------------------------------------------
# General Item Helpers
# ---------------------------------------------------------------------------

async def get_item(item_id: str) -> dict[str, Any] | None:
    """Get any item by ID."""
    return await gb_db.get_item(item_id)


async def list_all_items(active_only: bool = True) -> list[dict[str, Any]]:
    """List all items across categories."""
    return await gb_db.list_all_items(active_only)


async def get_item_price(item_id: str) -> int | None:
    """Get the price of an item."""
    item = await get_item(item_id)
    return item.get("price") if item else None


async def is_item_active(item_id: str) -> bool:
    """Check if an item is active and exists."""
    item = await get_item(item_id)
    return bool(item and item.get("active", True))


async def get_item_category(item_id: str) -> str | None:
    """Get the category of an item."""
    item = await get_item(item_id)
    return item.get("category") if item else None


async def get_store_preview(user_id: int) -> dict[str, Any]:
    """Get store preview with user's GB balance."""
    from . import currency as currency_service
    from . import store as store_service
    
    gb_balance = await currency_service.get_gb_balance(user_id)
    
    weapons = await store_service.get_store_by_category("weapon")
    armor = await store_service.get_store_by_category("armor")
    utility = await store_service.get_store_by_category("utility")
    
    return {
        "gb_balance": gb_balance,
        "weapons": weapons,
        "armor": armor,
        "utility": utility,
    }