"""Global Battle Inventory Service.

Manages player inventories with unique item IDs, equipment slots,
and durability tracking.
"""

from __future__ import annotations

import logging
from typing import Any

from database import global_battle as gb_db
from . import items as items_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inventory Management
# ---------------------------------------------------------------------------

async def add_to_inventory(
    user_id: int,
    item_id: str,
    quantity: int = 1,
    durability_current: int | None = None,
) -> dict[str, Any]:
    """Add an item to user's inventory."""
    # Verify item exists
    item = await items_service.get_item(item_id)
    if not item:
        raise ValueError(f"Item {item_id} does not exist")

    # Determine durability
    if durability_current is None:
        max_durability = item.get("durability") or item.get("base_durability") or 0
        durability_current = max_durability

    await gb_db.add_inventory_item(user_id, item_id, quantity, durability_current)

    return {
        "item_id": item_id,
        "name": item.get("name", "Unknown"),
        "quantity": quantity,
        "durability_current": durability_current,
    }


async def remove_from_inventory(user_id: int, item_id: str, quantity: int = 1) -> bool:
    """Remove an item from user's inventory."""
    return await gb_db.remove_inventory_item(user_id, item_id, quantity)


async def get_user_inventory(user_id: int) -> list[dict[str, Any]]:
    """Get user's full inventory with item details."""
    inventory = await gb_db.get_inventory(user_id)
    
    # Enrich with item details
    enriched = []
    for inv_item in inventory:
        item = await items_service.get_item(inv_item["item_id"])
        if item:
            enriched.append({
                "item_id": inv_item["item_id"],
                "name": item.get("name", "Unknown"),
                "category": item.get("category", "unknown"),
                "rarity": item.get("rarity", "common"),
                "quantity": inv_item.get("quantity", 0),
                "durability_current": inv_item.get("durability_current", 0),
                "max_durability": item.get("durability") or item.get("base_durability", 0),
            })
    
    return enriched


async def get_inventory_by_category(user_id: int, category: str) -> list[dict[str, Any]]:
    """Get inventory items filtered by category."""
    inventory = await get_user_inventory(user_id)
    return [item for item in inventory if item.get("category") == category]


# ---------------------------------------------------------------------------
# Equipment Management
# ---------------------------------------------------------------------------

async def equip_weapon(user_id: int, item_id: str | None) -> dict[str, Any]:
    """Equip a weapon. Pass None to unequip."""
    if item_id is not None:
        item = await items_service.get_item(item_id)
        if not item or item.get("category") != "weapon":
            raise ValueError(f"{item_id} is not a valid weapon")
        
        # Verify ownership
        inv_item = await gb_db.get_inventory_item(user_id, item_id)
        if not inv_item or inv_item.get("quantity", 0) < 1:
            raise ValueError("You don't own this weapon")

    await gb_db.equip_weapon(user_id, item_id)
    return {"weapon_id": item_id}


async def equip_armor(user_id: int, item_id: str | None) -> dict[str, Any]:
    """Equip armor. Pass None to unequip."""
    if item_id is not None:
        item = await items_service.get_item(item_id)
        if not item or item.get("category") != "armor":
            raise ValueError(f"{item_id} is not a valid armor")
        
        # Verify ownership
        inv_item = await gb_db.get_inventory_item(user_id, item_id)
        if not inv_item or inv_item.get("quantity", 0) < 1:
            raise ValueError("You don't own this armor")

    await gb_db.equip_armor(user_id, item_id)
    return {"armor_id": item_id}


async def equip_special(user_id: int, item_id: str | None) -> dict[str, Any]:
    """Equip a special weapon. Pass None to unequip."""
    if item_id is not None:
        special = await gb_db.get_special_weapon(item_id)
        if not special:
            raise ValueError(f"{item_id} is not a valid special weapon")
        
        # Verify ownership
        inv_item = await gb_db.get_inventory_item(user_id, item_id)
        if not inv_item or inv_item.get("quantity", 0) < 1:
            raise ValueError("You don't own this special weapon")

    await gb_db.equip_special(user_id, item_id)
    return {"special_id": item_id}


async def get_equipped(user_id: int) -> dict[str, Any]:
    """Get currently equipped items with details."""
    equipment = await gb_db.get_equipment(user_id)
    if not equipment:
        return {"weapon": None, "armor": None, "special": None}

    result = {}
    
    if equipment.get("weapon_id"):
        weapon = await items_service.get_weapon(equipment["weapon_id"])
        if weapon:
            result["weapon"] = {
                "item_id": weapon["item_id"],
                "name": weapon["name"],
                "damage": weapon.get("damage", 0),
                "durability": weapon.get("durability", 0),
            }
    
    if equipment.get("armor_id"):
        armor = await items_service.get_armor(equipment["armor_id"])
        if armor:
            result["armor"] = {
                "item_id": armor["item_id"],
                "name": armor["name"],
                "defense": armor.get("defense", 0),
                "durability": armor.get("base_durability", 0),
            }
    
    if equipment.get("special_id"):
        special = await gb_db.get_special_weapon(equipment["special_id"])
        if special:
            result["special"] = {
                "special_id": special["special_id"],
                "name": special["name"],
                "damage": special.get("damage", 0),
                "effect": special.get("effect", ""),
            }
    
    return result


# ---------------------------------------------------------------------------
# Durability Management
# ---------------------------------------------------------------------------

async def repair_item(user_id: int, item_id: str) -> dict[str, Any]:
    """Fully repair an item in inventory."""
    item = await items_service.get_item(item_id)
    if not item:
        raise ValueError(f"Item {item_id} does not exist")

    max_durability = item.get("durability") or item.get("base_durability", 0)
    if max_durability <= 0:
        return {"message": "Item has no durability to repair"}

    await gb_db.set_item_durability(user_id, item_id, max_durability)
    return {"item_id": item_id, "durability_restored": max_durability}


async def damage_item(user_id: int, item_id: str, amount: int) -> dict[str, Any]:
    """Damage an item's durability."""
    if amount <= 0:
        return {"durability_current": 0}

    inv_item = await gb_db.get_inventory_item(user_id, item_id)
    if not inv_item:
        raise ValueError("Item not in inventory")

    current = inv_item.get("durability_current", 0)
    new_durability = max(0, current - amount)
    
    await gb_db.set_item_durability(user_id, item_id, new_durability)
    
    broken = new_durability <= 0
    return {
        "item_id": item_id,
        "durability_current": new_durability,
        "broken": broken,
    }