"""Centralized permission service and decorators.

Hierarchy: OWNER (from OWNER_ID) > SUDO ADMINS > USERS.
Permissions are always resolved from numeric Telegram IDs, never usernames.

Every admin command's permission is resolved through ONE central resolver
(:func:`resolve_command_permission`): the stored per-command override (set via
``/admincmds``) wins, non-delegatable commands are always OWNER ONLY, and
otherwise each command keeps its decorator's default (its CURRENT behavior).

Each permission decorator stamps the wrapped handler with ``_admin_perm`` so the
``/admincmds`` panel can discover the ACTUAL registered admin commands and
their default permission directly from the Pyrogram dispatcher.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Awaitable, Callable

from pyrogram import Client
from pyrogram.types import Message

from config import config
from database import admins as admins_db
from utils.messages import error
from utils.sender import reply_html

logger = logging.getLogger(__name__)

# Commands that MUST remain OWNER ONLY and can never be delegated to SUDO via
# /admincmds (owner management, destructive recovery, security config).
NON_DELEGATABLE_ADMIN_COMMANDS = frozenset({
    "admincmds",
    "addsudo",
    "rsudo",
    "clear",
    "recover",
    "restore",
    "restorecase",
    "securityset",
})

# Small TTL cache so the resolver does not hit Mongo on every admin command.
# /admincmds invalidates the affected entry immediately (no restart needed).
_PERM_CACHE: dict[str, tuple[str, float]] = {}
_PERM_CACHE_TTL = 30.0


def normalize_command_name(raw: str) -> str:
    """Canonical command name: ``/give`` / ``give`` / ``GIVE`` / ``give@Bot`` -> ``give``."""
    name = (raw or "").strip().lstrip("/").lower()
    if "@" in name:
        name = name.split("@", 1)[0]
    return name


def invalidate_command_permission(command: str) -> None:
    """Drop a command's cached mode so the next call reads fresh state."""
    _PERM_CACHE.pop(normalize_command_name(command), None)


async def resolve_command_permission(command: str, *, default: str = "admin") -> str:
    """Resolve the effective permission mode (``owner`` or ``admin``) for a command.

    Order: non-delegatable -> stored override -> decorator default.
    """
    command = normalize_command_name(command)
    if not command:
        return default
    if command in NON_DELEGATABLE_ADMIN_COMMANDS:
        return "owner"
    now = time.time()
    cached = _PERM_CACHE.get(command)
    if cached and cached[1] > now:
        return cached[0]
    from services import settings as settings_service  # lazy: avoid import cycle

    try:
        stored = await settings_service.get_command_permission(command)
    except Exception:  # noqa: BLE001 - DB down: keep the decorator default
        logger.warning("permission lookup failed for %s; using default", command)
        stored = None
    mode = stored if stored in ("owner", "admin") else default
    _PERM_CACHE[command] = (mode, now + _PERM_CACHE_TTL)
    return mode


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
    default_mode = "owner" if role == "owner" else "admin"

    async def check(user_id: int, mode: str) -> bool:
        if mode == "owner":
            return await is_owner(user_id)
        return await is_sudo(user_id)

    def decorator(func: Callable[..., Awaitable]) -> Callable:
        @functools.wraps(func)
        async def wrapper(client: Client, message: Message, *args, **kwargs):
            user_id = message.from_user.id if message.from_user else 0
            if not user_id:
                await reply_html(client, message, error("Invalid user."))
                return
            command = normalize_command_name(message.command[0] if message.command else "")
            mode = await resolve_command_permission(command, default=default_mode)
            if not await check(user_id, mode):
                await reply_html(
                    client,
                    message,
                    error("You are not allowed to use this command."),
                )
                logger.warning("DENIED %s command to user %s", role, user_id)
                return
            return await func(client, message, *args, **kwargs)

        wrapper._admin_perm = default_mode
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
        command = normalize_command_name(message.command[0] if message.command else "")
        mode = await resolve_command_permission(command, default="owner")
        if mode != "owner" or not await is_owner(user_id):
            await reply_html(
                client,
                message,
                error("This command is restricted to the bot owner only."),
            )
            logger.warning("DENIED security OWNER command to user %s", user_id)
            return
        return await func(client, message, *args, **kwargs)

    wrapper._admin_perm = "owner"
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
        command = normalize_command_name(message.command[0] if message.command else "")
        mode = await resolve_command_permission(command, default="admin")
        allowed = await is_sudo(user_id) if mode == "admin" else await is_owner(user_id)
        if not allowed:
            await reply_html(
                client,
                message,
                error("You are not allowed to use this command."),
            )
            logger.warning("DENIED security SUDO command to user %s", user_id)
            return
        return await func(client, message, *args, **kwargs)

    wrapper._admin_perm = "admin"
    return wrapper