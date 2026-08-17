"""Security Handler - handles /security and related commands."""
from pyrogram import Client, filters
from pyrogram.types import Message

import logging
from typing import Optional, Any, Dict

from database import security as sec_db
from services import settings as settings_service
from utils.permissions import is_owner, is_sudo
from utils.messages import error, success, info
from utils.sender import reply_html

logger = logging.getLogger("security")


async def get_security_config_from_settings() -> dict[str, Any]:
    """Get security configuration from settings."""
    st_cfg = await settings_service.get_settings()
    return st_cfg.get("security", {})


async def get_security_config() -> dict[str, Any]:
    """Get security configuration from settings."""
    return await get_security_config_from_settings()


async def get_global_ban_on_exploit() -> bool:
    """Return whether exploits should trigger automatic global ban."""
    return bool(await get_security_config().get('global_ban_on_exploit', True))


async def get_secret_detection_enabled() -> bool:
    """Return whether secret/API-key detection is enabled."""
    return bool(await get_security_config().get('secret_detection_enabled', True))


async def check_economy_integrity(user_id: int) -> Optional[dict[str, Any]]:
    from services import economy as econ
    return await econ.check_user_balance_integrity(user_id)


async def manual_clear(user_id: int, target_user_id: Optional[int] = None) -> tuple[bool, str]:
    """Owner‑only manual clear of a user's recovery balance."""
    if target_user_id is None:
        target_user_id = user_id
    clear_balance = await settings_service.get_clear_recovery_balance()
    from database import security as sec_db
    ok = await sec_db.reset_recovery_balance(target_user_id, clear_balance)
    if ok:
        return True, f"Recovery balance cleared for user {target_user_id}. Default set to {clear_balance}."
    else:
        return False, "Nothing to clear or user not found."
