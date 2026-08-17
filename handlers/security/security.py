"""Security Handler - handles /security and related commands.
from pyrogram import Client

Business logic delegated to SecurityService and SettingsService.
"""

from pyrogram import filters
from pyrogram.types import Message

from services.security import (
    check_sudo_security, handle_secret_detection,
    manual_clear, manual_dump_user, manual_restore, manual_restorecase,
    get_security_config
)
from services.settings import get_clear_recovery_balance, get_global_ban_on_exploit, get_secret_detection_enabled
from utils.messages import error, success, info


async def cmd_securityset(client: Message):
    """Handle /securityset command - View/edit security configuration.
    
    Owner only.
    """
    # Check owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can configure security settings."))
        return
    
    # Get current config
    cfg = await get_security_config()
    st_cfg = await settings_service.get_settings()
    
    lines = [
        f"<b>Secret detection:</b> {'✅ Enabled' if st_cfg.get('secret_detection_enabled', True) else '❌ Disabled'}",
        f"<b>Global ban on exploit:</b> {'✅ Enabled' if st_cfg.get('global_ban_on_exploit', True) else '❌ Disabled'}",
        f"<b>Clear recovery balance:</b> {st_cfg.get('security', {}).get('clear_recovery_balance', 20000)}",
    ]
    await reply_html(client, message, info("\n".join(lines)))


async def cmd_securityinfo(client: Message):
    """Handle /securityinfo command - Show detailed security status."""
    # Owner only
    if not is_owner(message.from_user.id):
        await reply_html(client, message, msgs.error("Only the bot owner can view detailed security status."))
        return
    
    # Get security config and stats
    cfg = await get_security_config()
    st_cfg = await settings_service.get_settings()
    
    # Get some basic stats
    from database import security as sec_db
    total_bans = await sec_db.list_global_bans(active_only=True)
    total_dumps = await sec_db.list_dumps()
    total_cases = await sec_db.list_cases()
    
    lines = [
        f"<b>Secret detection:</b> {'✅ Enabled' if st_cfg.get('secret_detection_enabled', True) else '❌ Disabled'}",
        f"<b>Global ban on exploit:</b> {'✅ Enabled' if st_cfg.get('global_ban_on_exploit', True) else '❌ Disabled'}",
        f"<b>Clear recovery balance:</b> {st_cfg.get('security', {}).get('clear_recovery_balance', 20000)}",
        f"<br><b>Active global bans:</b> {len(total_bans)}",
        f"<br><b>Security dumps:</b> {len(total_dumps)}",
        f"<br><b>Security cases:</b> {len(total_cases)}",
    ]
    await reply_html(client, message, info("\n".join(lines)))
from utils.sender import reply_html
