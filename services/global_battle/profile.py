"""Global Battle Profile & Stats Service.

Handles player profiles, XP/levels, stat points, and derived stats.
"""

from __future__ import annotations

import logging
from typing import Any

from database import global_battle as gb_db
from services import settings as settings_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# XP / Level
# ---------------------------------------------------------------------------

async def get_level_config() -> dict[str, Any]:
    """Get level curve configuration."""
    cfg = await settings_service.get_global_battle_config()
    return cfg.get("level_curve", {"base_xp": 1000, "multiplier": 1.5})


async def calculate_xp_for_level(level: int) -> int:
    """Calculate total XP required to reach a given level."""
    if level <= 1:
        return 0
    cfg = await get_level_config()
    base = cfg.get("base_xp", 1000)
    mult = cfg.get("multiplier", 1.5)
    # Sum of geometric series: base * (mult^(level-1) - 1) / (mult - 1)
    if mult == 1:
        return base * (level - 1)
    return int(base * (mult ** (level - 1) - 1) / (mult - 1))


async def get_xp_to_next_level(current_xp: int) -> tuple[int, int]:
    """Return (current_level, xp_to_next_level)."""
    cfg = await get_level_config()
    base = cfg.get("base_xp", 1000)
    mult = cfg.get("multiplier", 1.5)

    level = 1
    xp_accum = 0
    while True:
        if level == 1:
            need = base
        else:
            need = int(base * (mult ** (level - 1)))
        if current_xp < xp_accum + need:
            return level, max(0, xp_accum + need - current_xp)
        xp_accum += need
        level += 1
        if level > 1000:  # safety cap
            return level, 0


async def add_xp(user_id: int, amount: int) -> dict[str, Any]:
    """Add XP to user, handle level-ups, return new state."""
    if amount <= 0:
        return {"leveled_up": False, "old_level": 1, "new_level": 1}

    profile = await gb_db.get_profile(user_id)
    if not profile:
        profile = await gb_db.get_or_create_profile(user_id)

    old_xp = profile.get("xp", 0)
    old_level = profile.get("level", 1)
    new_xp = old_xp + amount

    # Calculate new level
    new_level, _ = await get_xp_to_next_level(new_xp)

    updates = {"xp": new_xp}
    leveled_up = False
    if new_level > old_level:
        updates["level"] = new_level
        leveled_up = True

    await gb_db.update_profile(user_id, **updates)

    return {
        "leveled_up": leveled_up,
        "old_level": old_level,
        "new_level": new_level,
        "xp_gained": amount,
        "total_xp": new_xp,
    }


# ---------------------------------------------------------------------------
# Stat Points
# ---------------------------------------------------------------------------

async def get_stat_config() -> dict[str, Any]:
    """Get stat scaling configuration."""
    cfg = await settings_service.get_global_battle_config()
    return {
        "base_hp": cfg.get("base_hp", 100),
        "hp_per_stat": cfg.get("hp_per_stat", 5),
        "melee_scaling": cfg.get("melee_scaling", 1.0),
        "ability_scaling": cfg.get("ability_scaling", 1.0),
        "durability_scaling": cfg.get("durability_scaling", 1.0),
    }


async def calculate_max_hp(user_id: int) -> int:
    """Calculate max HP from health stat."""
    cfg = await get_stat_config()
    profile = await gb_db.get_profile(user_id)
    if not profile:
        return cfg["base_hp"]
    health_stat = profile.get("health_stat", 0)
    return cfg["base_hp"] + health_stat * cfg["hp_per_stat"]


async def calculate_melee_damage(user_id: int, base_damage: int = 10) -> int:
    """Calculate melee damage from melee stat."""
    cfg = await get_stat_config()
    profile = await gb_db.get_profile(user_id)
    if not profile:
        return base_damage
    melee_stat = profile.get("melee_stat", 0)
    return int(base_damage + melee_stat * cfg["melee_scaling"])


async def calculate_ability_bonus(user_id: int) -> dict[str, float]:
    """Calculate ability-based bonuses (crit chance, crit damage, etc.)."""
    cfg = await get_stat_config()
    profile = await gb_db.get_profile(user_id)
    if not profile:
        return {"crit_chance": 0.0, "crit_damage": 0.0, "damage_buff": 0.0}
    ability_stat = profile.get("ability_stat", 0)
    scaling = cfg["ability_scaling"]
    return {
        "crit_chance": min(0.5, ability_stat * scaling * 0.01),  # max 50%
        "crit_damage": 1.0 + ability_stat * scaling * 0.02,
        "damage_buff": ability_stat * scaling * 0.01,
    }


async def calculate_effective_durability(user_id: int, armor_base_durability: int = 0) -> int:
    """Calculate effective armor durability including stat bonus."""
    cfg = await get_stat_config()
    profile = await gb_db.get_profile(user_id)
    if not profile:
        return armor_base_durability
    dur_stat = profile.get("durability_stat", 0)
    return armor_base_durability + int(dur_stat * cfg["durability_scaling"])


async def add_stat_point(user_id: int, stat: str) -> dict[str, Any]:
    """Add a stat point to user profile. stat: health|melee|ability|durability."""
    valid_stats = {"health", "melee", "ability", "durability"}
    if stat not in valid_stats:
        raise ValueError(f"Invalid stat: {stat}. Must be one of {valid_stats}")

    field = f"{stat}_stat"
    # Use inc_profile_field to atomically increment
    await gb_db.inc_profile_field(user_id, field, 1)

    # Return updated stats
    new_val = (await gb_db.get_profile(user_id)).get(field, 1)
    return {
        "stat": stat,
        "new_value": new_val,
        "message": f"{stat.title()} stat increased to {new_val}.",
    }


# ---------------------------------------------------------------------------
# Battle Rewards
# ---------------------------------------------------------------------------

async def grant_battle_rewards(
    user_id: int,
    *,
    xp: int = 0,
    gb_coins: int = 0,
    stat_points: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Grant battle rewards to a player."""
    from . import currency as currency_service

    results = {}

    if xp > 0:
        xp_result = await add_xp(user_id, xp)
        results["xp"] = xp_result

    if gb_coins > 0:
        await currency_service.add_gb_coins(user_id, gb_coins)
        results["gb_coins"] = gb_coins

    if stat_points:
        for stat, count in stat_points.items():
            for _ in range(count):
                await add_stat_point(user_id, stat)
        results["stat_points"] = stat_points

    return results