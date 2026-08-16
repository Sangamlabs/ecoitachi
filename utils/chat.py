"""Chat type classification and the centralized chat gate.

Supported chat types: PRIVATE, GROUP, SUPERGROUP.  Every command handler runs
through :func:`check_gate` (via ``safe_handler``) so chat-level restrictions
live in ONE place — the group configuration service — instead of being
hardcoded across handlers.
"""

from __future__ import annotations

from pyrogram.enums import ChatType
from pyrogram.types import Message

from services import group_config as group_config_service
from utils.permissions import is_owner

UNSUPPORTED = "OTHER"

# feature name (safe_handler arg) -> group config key
_FEATURE_KEY = {
    "economy": "economy_enabled",
    "games": "games_enabled",
    "leaderboard": "leaderboard_enabled",
    "admin": "admin_commands_enabled",
}

_DISABLED_MSG = {
    "economy": "The economy is disabled in this group.",
    "games": "Games are disabled in this group.",
    "leaderboard": "The leaderboard is disabled in this group.",
    "admin": "Admin commands are disabled in this group.",
}


def chat_type(chat) -> str:
    """Classify a chat as ``PRIVATE``, ``GROUP``, ``SUPERGROUP`` or ``OTHER``."""
    t = getattr(chat, "type", None)
    if t is ChatType.PRIVATE:
        return "PRIVATE"
    if t is ChatType.GROUP:
        return "GROUP"
    if t is ChatType.SUPERGROUP:
        return "SUPERGROUP"
    return UNSUPPORTED


def is_group(chat) -> bool:
    return chat_type(chat) in ("GROUP", "SUPERGROUP")


async def check_gate(message: Message, feature: str | None = None) -> tuple[bool, str | None]:
    """Centralized chat permission gate applied to every command.

    Returns ``(allowed, reason)``.  A ``reason`` is shown to the user; ``None``
    means the command is ignored silently (used when a group is fully disabled).

    ``feature`` selects the per-chat feature toggle (economy/games/leaderboard/
    admin).  ``None`` only requires the group to be enabled.  ``"chat_control"``
    bypasses every group toggle so owners/sudo can always manage a chat's config.
    """
    kind = chat_type(message.chat)
    if kind == UNSUPPORTED:
        return False, "This bot only works in private chats and groups."
    if kind == "PRIVATE":
        return True, None
    if feature == "chat_control":
        return True, None

    # Security checks: global ban and quarantine (before group config)
    user_id = message.from_user.id if message.from_user else 0
    if user_id:
        from services import security as security_service
        is_banned, ban_reason = await security_service.global_ban_check(user_id)
        if is_banned:
            return False, f"You are globally banned: {ban_reason}"
        is_quarantined = await security_service.quarantine_check(user_id)
        if is_quarantined:
            return False, "Your account is quarantined. Contact the owner."

    cfg = await group_config_service.get_group_config(message.chat.id)
    if not cfg.get("group_enabled", True):
        return False, None

    if feature is None:
        return True, None

    key = _FEATURE_KEY.get(feature)
    if key and not cfg.get(key, True):
        user_id = message.from_user.id if message.from_user else 0
        if feature == "admin" and await is_owner(user_id):
            return True, None
        return False, _DISABLED_MSG.get(feature, "This feature is disabled in this group.")

    return True, None
