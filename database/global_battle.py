"""Global Battle data access layer.

Collections for missions, profiles, items, inventory, matches, arenas, etc.
All writes use atomic MongoDB operations. Indexes are created at startup.
"""

from __future__ import annotations

import time
from typing import Any

from database.mongo import mongo

# Collection names
MISSIONS = "global_missions"
MISSION_PROGRESS = "global_mission_progress"
PROFILES = "global_profiles"
ITEMS = "global_items"
INVENTORY = "global_inventory"
EQUIPMENT = "global_equipment"
SPECIAL_WEAPONS = "global_special_weapons"
MATCHES = "global_matches"
MATCHMAKING = "global_matchmaking"
ARENAS = "global_arenas"
TRANSACTIONS = "global_transactions"
SETTINGS = "global_settings"
REWARDS = "global_rewards"


async def ensure_indexes() -> None:
    # Missions
    await mongo.db[MISSIONS].create_index("mission_id", unique=True)
    await mongo.db[MISSIONS].create_index("active")

    # Mission Progress
    await mongo.db[MISSION_PROGRESS].create_index(
        [("user_id", 1), ("mission_id", 1)], unique=True
    )
    await mongo.db[MISSION_PROGRESS].create_index("user_id")
    await mongo.db[MISSION_PROGRESS].create_index("completed")

    # Profiles
    await mongo.db[PROFILES].create_index("user_id", unique=True)
    await mongo.db[PROFILES].create_index("unique_uid", unique=True, sparse=True)
    await mongo.db[PROFILES].create_index("mission_unlocked")

    # Items
    await mongo.db[ITEMS].create_index("item_id", unique=True)
    await mongo.db[ITEMS].create_index("category")
    await mongo.db[ITEMS].create_index("active")

    # Inventory
    await mongo.db[INVENTORY].create_index(
        [("user_id", 1), ("item_id", 1)], unique=True
    )
    await mongo.db[INVENTORY].create_index("user_id")
    await mongo.db[INVENTORY].create_index("item_id")

    # Equipment
    await mongo.db[EQUIPMENT].create_index("user_id", unique=True)

    # Special Weapons
    await mongo.db[SPECIAL_WEAPONS].create_index("special_id", unique=True)

    # Matches
    await mongo.db[MATCHES].create_index("match_id", unique=True)
    await mongo.db[MATCHES].create_index("state")
    await mongo.db[MATCHES].create_index("arena_id")
    await mongo.db[MATCHES].create_index("expiry_at", expireAfterSeconds=0)
    await mongo.db[MATCHES].create_index("players.user_id")

    # Matchmaking
    await mongo.db[MATCHMAKING].create_index("user_id", unique=True)
    await mongo.db[MATCHMAKING].create_index("state")
    await mongo.db[MATCHMAKING].create_index("match_type")
    await mongo.db[MATCHMAKING].create_index("expiry_at", expireAfterSeconds=0)

    # Arenas
    await mongo.db[ARENAS].create_index("chat_id", unique=True)
    await mongo.db[ARENAS].create_index("state")

    # Transactions
    await mongo.db[TRANSACTIONS].create_index("transaction_id", unique=True)
    await mongo.db[TRANSACTIONS].create_index("user_id")
    await mongo.db[TRANSACTIONS].create_index("created_at")

    # Rewards
    await mongo.db[REWARDS].create_index("reward_id", unique=True)
    await mongo.db[REWARDS].create_index("active")

    # Settings
    await mongo.db[SETTINGS].create_index("key", unique=True)


# ---------------------------------------------------------------------------
# Missions
# ---------------------------------------------------------------------------

MISSION_DEFINITIONS = [
    {
        "mission_id": "daily",
        "name": "Daily Reward",
        "description": "Claim your daily free coins",
        "requirement_type": "command",
        "requirement_value": "daily",
        "active": True,
    },
    {
        "mission_id": "weekly",
        "name": "Weekly Reward",
        "description": "Claim your weekly free coins",
        "requirement_type": "command",
        "requirement_value": "weekly",
        "active": True,
    },
    {
        "mission_id": "monthly",
        "name": "Monthly Reward",
        "description": "Claim your monthly free coins",
        "requirement_type": "command",
        "requirement_value": "monthly",
        "active": True,
    },
    {
        "mission_id": "pay",
        "name": "Make a Payment",
        "description": "Send money to another player using /pay",
        "requirement_type": "command",
        "requirement_value": "pay",
        "active": True,
    },
    {
        "mission_id": "deposit",
        "name": "Bank Deposit",
        "description": "Deposit money into your bank using /deposit",
        "requirement_type": "command",
        "requirement_value": "deposit",
        "active": True,
    },
    {
        "mission_id": "withdraw",
        "name": "Bank Withdrawal",
        "description": "Withdraw money from your bank using /withdraw",
        "requirement_type": "command",
        "requirement_value": "withdraw",
        "active": True,
    },
    {
        "mission_id": "stock",
        "name": "Check Stock",
        "description": "View a stock using /stock",
        "requirement_type": "command",
        "requirement_value": "stock",
        "active": True,
    },
    {
        "mission_id": "buystock",
        "name": "Buy Stock",
        "description": "Purchase shares using /buystock",
        "requirement_type": "command",
        "requirement_value": "buystock",
        "active": True,
    },
    {
        "mission_id": "assets",
        "name": "View Assets",
        "description": "Browse the asset market using /assets",
        "requirement_type": "command",
        "requirement_value": "assets",
        "active": True,
    },
    {
        "mission_id": "game",
        "name": "Complete a Game",
        "description": "Finish any economy game (fly, mines, bet, colour, aviator, etc.)",
        "requirement_type": "game_complete",
        "requirement_value": "any",
        "active": True,
    },
]


async def init_missions() -> int:
    """Insert default missions if they don't exist. Returns count inserted."""
    inserted = 0
    for m in MISSION_DEFINITIONS:
        result = await mongo.db[MISSIONS].update_one(
            {"mission_id": m["mission_id"]},
            {"$setOnInsert": m},
            upsert=True,
        )
        if result.upserted_id:
            inserted += 1
    return inserted


async def get_all_missions(active_only: bool = True) -> list[dict[str, Any]]:
    query = {"active": True} if active_only else {}
    cursor = mongo.db[MISSIONS].find(query).sort("mission_id", 1)
    return [doc async for doc in cursor]


async def get_mission(mission_id: str) -> dict[str, Any] | None:
    return await mongo.db[MISSIONS].find_one({"mission_id": mission_id})


async def set_mission_active(mission_id: str, active: bool) -> bool:
    result = await mongo.db[MISSIONS].update_one(
        {"mission_id": mission_id}, {"$set": {"active": active}}
    )
    return result.modified_count == 1


# ---------------------------------------------------------------------------
# Mission Progress
# ---------------------------------------------------------------------------

async def get_user_progress(user_id: int) -> list[dict[str, Any]]:
    cursor = mongo.db[MISSION_PROGRESS].find({"user_id": user_id})
    return [doc async for doc in cursor]


async def get_mission_progress(user_id: int, mission_id: str) -> dict[str, Any] | None:
    return await mongo.db[MISSION_PROGRESS].find_one(
        {"user_id": user_id, "mission_id": mission_id}
    )


async def increment_mission_progress(user_id: int, mission_id: str) -> dict[str, Any]:
    """Atomically increment progress for a mission. Returns updated doc."""
    now = int(time.time())
    doc = await mongo.db[MISSION_PROGRESS].find_one_and_update(
        {"user_id": user_id, "mission_id": mission_id, "completed": False},
        {"$inc": {"progress": 1}, "$set": {"updated_at": now}},
        upsert=True,
        return_document=True,
    )
    return doc


async def complete_mission(user_id: int, mission_id: str) -> dict[str, Any] | None:
    """Mark mission as completed. Returns updated doc or None if already done."""
    now = int(time.time())
    doc = await mongo.db[MISSION_PROGRESS].find_one_and_update(
        {"user_id": user_id, "mission_id": mission_id, "completed": False},
        {"$set": {"completed": True, "completed_at": now, "updated_at": now}},
        return_document=True,
    )
    return doc


async def count_completed_missions(user_id: int) -> int:
    return await mongo.db[MISSION_PROGRESS].count_documents(
        {"user_id": user_id, "completed": True}
    )


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

DEFAULT_PROFILE = {
    "gb_coins": 0,
    "level": 1,
    "xp": 0,
    "xp_to_next": 1000,
    "health_stat": 0,
    "melee_stat": 0,
    "ability_stat": 0,
    "durability_stat": 0,
    "equipped_weapon_id": None,
    "equipped_armor_id": None,
    "equipped_special_id": None,
    "matches": 0,
    "wins": 0,
    "losses": 0,
    "mission_unlocked": False,
    "missions_completed": 0,
    "created_at": 0,
    "updated_at": 0,
}


async def get_or_create_profile(user_id: int, unique_uid: str | None = None) -> dict[str, Any]:
    now = int(time.time())
    set_on_insert = dict(DEFAULT_PROFILE, created_at=now, updated_at=now)
    if unique_uid:
        set_on_insert["unique_uid"] = unique_uid

    doc = await mongo.db[PROFILES].find_one_and_update(
        {"user_id": user_id},
        {"$setOnInsert": set_on_insert, "$set": {"updated_at": now}},
        upsert=True,
        return_document=True,
    )
    return doc


async def get_profile(user_id: int) -> dict[str, Any] | None:
    return await mongo.db[PROFILES].find_one({"user_id": user_id})


async def get_profile_by_uid(unique_uid: str) -> dict[str, Any] | None:
    return await mongo.db[PROFILES].find_one({"unique_uid": unique_uid.upper()})


async def update_profile(user_id: int, **fields: Any) -> dict[str, Any] | None:
    fields["updated_at"] = int(time.time())
    return await mongo.db[PROFILES].find_one_and_update(
        {"user_id": user_id},
        {"$set": fields},
        return_document=True,
    )


async def inc_profile_field(user_id: int, field: str, amount: int) -> dict[str, Any] | None:
    return await mongo.db[PROFILES].find_one_and_update(
        {"user_id": user_id},
        {"$inc": {field: amount}, "$set": {"updated_at": int(time.time())}},
        return_document=True,
    )


async def unlock_global_event(user_id: int) -> dict[str, Any] | None:
    # Ensure profile exists first
    await get_or_create_profile(user_id)
    return await mongo.db[PROFILES].find_one_and_update(
        {"user_id": user_id, "mission_unlocked": False},
        {"$set": {"mission_unlocked": True, "updated_at": int(time.time())}},
        return_document=True,
    )


async def is_global_unlocked(user_id: int) -> bool:
    doc = await mongo.db[PROFILES].find_one(
        {"user_id": user_id}, {"_id": 0, "mission_unlocked": 1}
    )
    return bool(doc and doc.get("mission_unlocked"))


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

async def create_item(doc: dict[str, Any]) -> str:
    await mongo.db[ITEMS].insert_one(doc)
    return doc["item_id"]


async def get_item(item_id: str) -> dict[str, Any] | None:
    return await mongo.db[ITEMS].find_one({"item_id": item_id})


async def get_items_by_category(category: str, active_only: bool = True) -> list[dict[str, Any]]:
    query = {"category": category}
    if active_only:
        query["active"] = True
    cursor = mongo.db[ITEMS].find(query).sort("item_id", 1)
    return [doc async for doc in cursor]


async def update_item(item_id: str, **fields: Any) -> bool:
    result = await mongo.db[ITEMS].update_one(
        {"item_id": item_id}, {"$set": fields}
    )
    return result.modified_count == 1


async def delete_item(item_id: str) -> bool:
    result = await mongo.db[ITEMS].delete_one({"item_id": item_id})
    return result.deleted_count == 1


async def list_all_items(active_only: bool = True) -> list[dict[str, Any]]:
    query = {"active": True} if active_only else {}
    cursor = mongo.db[ITEMS].find(query).sort("item_id", 1)
    return [doc async for doc in cursor]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

async def add_inventory_item(
    user_id: int, item_id: str, quantity: int = 1, durability_current: int | None = None
) -> None:
    now = int(time.time())
    item = await get_item(item_id)
    if not item:
        raise ValueError(f"Item {item_id} does not exist")

    max_durability = item.get("durability") or item.get("base_durability")
    if durability_current is None:
        durability_current = max_durability or 0

    await mongo.db[INVENTORY].update_one(
        {"user_id": user_id, "item_id": item_id},
        {
            "$inc": {"quantity": quantity},
            "$setOnInsert": {
                "user_id": user_id,
                "item_id": item_id,
                "durability_current": durability_current,
                "acquired_at": now,
            },
            "$set": {"updated_at": now},
        },
        upsert=True,
    )


async def remove_inventory_item(user_id: int, item_id: str, quantity: int = 1) -> bool:
    result = await mongo.db[INVENTORY].update_one(
        {"user_id": user_id, "item_id": item_id, "quantity": {"$gte": quantity}},
        {"$inc": {"quantity": -quantity}, "$set": {"updated_at": int(time.time())}},
    )
    if result.modified_count:
        await mongo.db[INVENTORY].delete_many(
            {"user_id": user_id, "item_id": item_id, "quantity": {"$lte": 0}}
        )
        return True
    return False


async def get_inventory(user_id: int) -> list[dict[str, Any]]:
    cursor = mongo.db[INVENTORY].find({"user_id": user_id, "quantity": {"$gt": 0}})
    return [doc async for doc in cursor]


async def get_inventory_item(user_id: int, item_id: str) -> dict[str, Any] | None:
    return await mongo.db[INVENTORY].find_one({"user_id": user_id, "item_id": item_id})


async def set_item_durability(user_id: int, item_id: str, durability: int) -> bool:
    result = await mongo.db[INVENTORY].update_one(
        {"user_id": user_id, "item_id": item_id},
        {"$set": {"durability_current": max(0, durability), "updated_at": int(time.time())}},
    )
    return result.modified_count == 1


# ---------------------------------------------------------------------------
# Equipment
# ---------------------------------------------------------------------------

async def get_equipment(user_id: int) -> dict[str, Any] | None:
    return await mongo.db[EQUIPMENT].find_one({"user_id": user_id})


async def equip_weapon(user_id: int, weapon_id: str | None) -> dict[str, Any] | None:
    return await mongo.db[EQUIPMENT].find_one_and_update(
        {"user_id": user_id},
        {"$set": {"weapon_id": weapon_id, "updated_at": int(time.time())}},
        upsert=True,
        return_document=True,
    )


async def equip_armor(user_id: int, armor_id: str | None) -> dict[str, Any] | None:
    return await mongo.db[EQUIPMENT].find_one_and_update(
        {"user_id": user_id},
        {"$set": {"armor_id": armor_id, "updated_at": int(time.time())}},
        upsert=True,
        return_document=True,
    )


async def equip_special(user_id: int, special_id: str | None) -> dict[str, Any] | None:
    return await mongo.db[EQUIPMENT].find_one_and_update(
        {"user_id": user_id},
        {"$set": {"special_id": special_id, "updated_at": int(time.time())}},
        upsert=True,
        return_document=True,
    )


# ---------------------------------------------------------------------------
# Special Weapons
# ---------------------------------------------------------------------------

async def create_special_weapon(doc: dict[str, Any]) -> str:
    await mongo.db[SPECIAL_WEAPONS].insert_one(doc)
    return doc["special_id"]


async def get_special_weapon(special_id: str) -> dict[str, Any] | None:
    return await mongo.db[SPECIAL_WEAPONS].find_one({"special_id": special_id})


async def list_special_weapons(active_only: bool = True) -> list[dict[str, Any]]:
    query = {"active": True} if active_only else {}
    cursor = mongo.db[SPECIAL_WEAPONS].find(query).sort("special_id", 1)
    return [doc async for doc in cursor]


async def grant_special_weapon(user_id: int, special_id: str) -> bool:
    """Grant a special weapon to user's inventory."""
    special = await get_special_weapon(special_id)
    if not special:
        return False
    await add_inventory_item(user_id, special_id, quantity=1)
    return True


async def update_special_weapon(special_id: str, **fields: Any) -> bool:
    result = await mongo.db[SPECIAL_WEAPONS].update_one(
        {"special_id": special_id}, {"$set": fields}
    )
    return result.modified_count == 1


async def delete_special_weapon(special_id: str) -> bool:
    result = await mongo.db[SPECIAL_WEAPONS].delete_one({"special_id": special_id})
    return result.deleted_count == 1


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

async def record_transaction(
    transaction_id: str,
    user_id: int,
    ttype: str,
    amount: int,
    balance_before: int,
    balance_after: int,
    metadata: dict[str, Any] | None = None,
) -> str:
    doc = {
        "transaction_id": transaction_id,
        "user_id": user_id,
        "type": ttype,
        "amount": amount,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "metadata": metadata or {},
        "created_at": int(time.time()),
    }
    await mongo.db[TRANSACTIONS].insert_one(doc)
    return transaction_id


async def get_recent_transactions(user_id: int, limit: int = 10) -> list[dict[str, Any]]:
    cursor = (
        mongo.db[TRANSACTIONS]
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    return [doc async for doc in cursor]