"""Centralized group configuration service.

Per-chat feature toggles are stored as overrides in MongoDB and merged over
global defaults.  Handlers never hardcode chat restrictions — they go through
the gate in :mod:`utils.chat`.  Adding a new setting here (plus the admin
command map in ``handlers/admin.py``) is enough to make it configurable;
existing handlers need no changes.
"""

from __future__ import annotations

from typing import Any

from database import group_config as group_config_db

DEFAULT_GROUP_CONFIG: dict[str, Any] = {
    "group_enabled": True,
    "economy_enabled": True,
    "games_enabled": True,
    "leaderboard_enabled": True,
    "admin_commands_enabled": True,
}

# /setchat setting name -> config key
SETTING_ALIASES = {
    "group": "group_enabled",
    "economy": "economy_enabled",
    "games": "games_enabled",
    "leaderboard": "leaderboard_enabled",
    "admin": "admin_commands_enabled",
}


async def ensure_indexes() -> None:
    await group_config_db.ensure_indexes()


async def get_group_config(chat_id: int) -> dict[str, Any]:
    """Return the effective config for a chat (defaults merged with overrides)."""
    merged = dict(DEFAULT_GROUP_CONFIG)
    doc = await group_config_db.get_doc(chat_id)
    if doc:
        merged.update({k: v for k, v in doc.items() if k in DEFAULT_GROUP_CONFIG})
    return merged


async def update_group_config(chat_id: int, **changes: Any) -> dict[str, Any]:
    """Persist the given overrides for a chat and return the merged config."""
    allowed = {k: v for k, v in changes.items() if k in DEFAULT_GROUP_CONFIG}
    if allowed:
        await group_config_db.upsert(chat_id, allowed)
    return await get_group_config(chat_id)


async def reset_group_config(chat_id: int) -> dict[str, Any]:
    """Remove all overrides for a chat, returning it to defaults."""
    await group_config_db.delete(chat_id)
    return await get_group_config(chat_id)


async def group_enabled(chat_id: int) -> bool:
    cfg = await get_group_config(chat_id)
    return bool(cfg.get("group_enabled", True))


async def feature_enabled(chat_id: int, feature: str) -> bool:
    key = SETTING_ALIASES.get(feature)
    if key is None:
        return True
    cfg = await get_group_config(chat_id)
    return bool(cfg.get(key, True))
