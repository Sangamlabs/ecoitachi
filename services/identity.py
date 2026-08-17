"""Central UNOITACHI user identity layer.

Every Telegram user detected by the bot is automatically registered as a
UNOITACHI user through :func:`ensure_user` (which also assigns the permanent
internal ``unique_user_id``).  All admin/economy target commands resolve users
through :func:`resolve_user` so lookup logic lives in ONE place instead of
being duplicated across handlers.

Identity rules:

- Telegram ``user_id`` is the external identity (stored as ``user_id``).
- ``unique_user_id`` (``UID-xxxxxx``) is the internal, permanent identity.
- Username is only a convenience lookup key, never the primary identity.
"""

from __future__ import annotations

import logging
from typing import Any

from database import users as users_db

logger = logging.getLogger(__name__)


async def ensure_user(
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    *,
    touch: bool = True,
) -> dict[str, Any]:
    """Register (or touch) a UNOITACHI user and return the stored document.

    Race-safe: ``get_or_create_user`` upserts atomically on the unique
    ``user_id`` index and a permanent ``unique_user_id`` is attached with a
    guarded update, so two simultaneous messages from a new user can never
    create two users or assign two UIDs.
    """
    doc = await users_db.get_or_create_user(user_id, username, first_name)
    if not doc.get("unique_user_id"):
        await users_db.assign_uid(user_id)
    if touch:
        await users_db.touch_user(user_id, username, first_name)
    return await users_db.get_user(user_id) or doc


async def ensure_user_from_telegram(user: Any) -> dict[str, Any] | None:
    """Register a Pyrogram ``User`` object (from a message / reply / mention)."""
    if user is None or not getattr(user, "id", None):
        return None
    return await ensure_user(
        user.id,
        getattr(user, "username", None),
        getattr(user, "first_name", None),
    )


async def resolve_user(
    client: Any,
    message: Any,
    arg: str | None = None,
    *,
    create: bool = True,
) -> dict[str, Any] | None:
    """Resolve a target user from one of: reply, numeric Telegram ID,
    UNOITACHI UID (``UID-xxxxxx``) or ``@username``.

    Order of precedence:
        1. replied-to Telegram user (most reliable),
        2. numeric Telegram user ID,
        3. UNOITACHI UID,
        4. ``@username`` (database first, then Telegram resolution).

    ``create=True`` registers an unknown user through :func:`ensure_user`
    (used by economy/admin targets that require a stored document).
    Returns the user document, or ``None`` when unresolvable.
    """
    # 1. Reply target
    reply = getattr(message, "reply_to_message", None)
    if reply is not None and getattr(reply, "from_user", None):
        return await ensure_user_from_telegram(reply.from_user)

    if arg is None:
        return None
    arg = arg.strip()

    # 2. Numeric Telegram ID
    if arg.isdigit():
        if create:
            return await ensure_user(int(arg))
        return await users_db.get_user(int(arg))

    # 3. UNOITACHI UID
    if users_db.is_uid(arg):
        return await users_db.get_user_by_uid(arg)

    # 4. @username
    if arg.startswith("@"):
        uname = arg[1:]
        doc = await users_db.get_user_by_username(uname)
        if doc is not None:
            return doc
        if create:
            user = await _telegram_get_user(client, uname)
            if user is not None:
                return await ensure_user_from_telegram(user)
        return None

    return None


async def _telegram_get_user(client: Any, username: str) -> Any | None:
    """Resolve an unknown ``@username`` through Telegram itself."""
    if client is None:
        return None
    try:
        return await client.get_users(username)
    except Exception as exc:  # noqa: BLE001 - network/API failures are expected
        logger.warning("could not resolve @%s via Telegram: %s", username, exc)
        return None