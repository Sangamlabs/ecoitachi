"""Centralized permission service and decorators.

Hierarchy: OWNER (from OWNER_ID) > SUDO ADMINS > USERS.
Permissions are always resolved from numeric Telegram IDs, never usernames.
"""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from pyrogram import Client
from pyrogram.types import Message

from config import config
from database import admins as admins_db
from utils.messages import error
from utils.sender import reply_html

logger = logging.getLogger(__name__)


async def is_owner(user_id: int) -> bool:
    return user_id == config.OWNER_ID


async def is_sudo(user_id: int) -> bool:
    if user_id == config.OWNER_ID:
        return True
    return await admins_db.is_sudo(user_id)


async def is_admin(user_id: int) -> bool:
    """Admin == owner or sudo. Serves as the 'admin_only' gate."""
    return await is_sudo(user_id)


async def has_any_role(user_id: int) -> bool:
    return await is_sudo(user_id)


def _role_guard(role: str) -> Callable:
    async def check(user_id: int) -> bool:
        if role == "owner":
            return await is_owner(user_id)
        if role == "sudo":
            return await is_sudo(user_id)
        return await is_admin(user_id)

    def decorator(func: Callable[..., Awaitable]) -> Callable:
        @functools.wraps(func)
        async def wrapper(client: Client, message: Message, *args, **kwargs):
            user_id = message.from_user.id if message.from_user else 0
            if not user_id:
                await reply_html(client, message, error("Invalid user."))
                return
            if not await check(user_id):
                await reply_html(
                    client,
                    message,
                    error("You are not allowed to use this command."),
                )
                logger.warning("DENIED %s command to user %s", role, user_id)
                return
            return await func(client, message, *args, **kwargs)

        return wrapper

    return decorator


owner_only = _role_guard("owner")
sudo_only = _role_guard("sudo")
admin_only = sudo_only  # admin == owner or sudo in Phase 1


# Security-specific decorators
# These enforce OWNER-only access for security recovery commands
# SUDO ADMINS CANNOT USE THESE

def security_owner_only(func: Callable[..., Awaitable]) -> Callable:
    """Decorator for OWNER-ONLY security commands (/clear, /restore, /recover, /restorecase, /dumpinfo, /dumps).
    SUDO ADMINS CANNOT USE THESE."""
    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        user_id = message.from_user.id if message.from_user else 0
        if not user_id:
            await reply_html(client, message, error("Invalid user."))
            return
        if not await is_owner(user_id):
            await reply_html(
                client,
                message,
                error("This command is restricted to the bot owner only."),
            )
            logger.warning("DENIED security OWNER command to user %s", user_id)
            return
        return await func(client, message, *args, **kwargs)
    return wrapper


def security_sudo_or_owner(func: Callable[..., Awaitable]) -> Callable:
    """Decorator for SUDO+OWNER security commands (/gban, /ungban, /gbaninfo, /gbanlist, /securityset).
    Both OWNER and SUDO can use these."""
    @functools.wraps(func)
    async def wrapper(client: Client, message: Message, *args, **kwargs):
        user_id = message.from_user.id if message.from_user else 0
        if not user_id:
            await reply_html(client, message, error("Invalid user."))
            return
        if not await is_sudo(user_id):
            await reply_html(
                client,
                message,
                error("You are not allowed to use this command."),
            )
            logger.warning("DENIED security SUDO command to user %s", user_id)
            return
        return await func(client, message, *args, **kwargs)
    return wrapper