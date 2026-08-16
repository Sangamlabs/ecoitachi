"""Shared helpers for handlers: user bootstrap and safe execution."""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from pyrogram import Client
from pyrogram.types import Message

from database import users as users_db
from services.economy import EconomyError, BannedUser, FrozenUser, InsufficientBalance
from services.game_engine import GameCooldownError, GameError, GameInProgress, NoActiveGame
from services.promo_rewards import PromoRewardError
from services.promos import PromoError
from utils.chat import check_gate
from utils.messages import error
from utils.money import MoneyError
from utils.sender import reply_html

logger = logging.getLogger(__name__)


async def ensure_user(client: Client, message: Message) -> None:
    """Create/touch the interacting user before running a command."""
    user = message.from_user
    if user is None:
        return
    await users_db.get_or_create_user(user.id, user.username, user.first_name)
    await users_db.touch_user(user.id, user.username, user.first_name)


def safe_handler(func=None, *, feature: str | None = None) -> Callable:
    """Wrap a command handler with user bootstrap, the centralized chat gate,
    and centralized error handling.

    ``feature`` is a group-config feature name (``economy``, ``games``,
    ``leaderboard``, ``admin``) that gates the command in groups.  ``None``
    means the command only needs ``group_enabled``.  ``"chat_control"`` is used
    by the group-config admin commands themselves.
    """

    def decorator(func: Callable[..., Awaitable]) -> Callable:
        @functools.wraps(func)
        async def wrapper(client: Client, message: Message, *args, **kwargs):
            try:
                await ensure_user(client, message)
                allowed, reason = await check_gate(message, feature=feature)
                if not allowed:
                    if reason:
                        await reply_html(client, message, error(reason))
                    return
                return await func(client, message, *args, **kwargs)
            except GameCooldownError as exc:
                parts = str(exc).split(":")
                game = parts[0] if parts else "game"
                remaining = int(parts[2]) if len(parts) >= 3 else 0
                from utils.messages import game_cooldown

                await reply_html(client, message, game_cooldown(game, remaining))
            except GameInProgress as exc:
                await reply_html(client, message, error(str(exc)))
            except (EconomyError, MoneyError, GameError, NoActiveGame) as exc:
                await reply_html(client, message, error(str(exc)))
            except (PromoError, PromoRewardError) as exc:
                await reply_html(client, message, error(str(exc)))
            except FrozenUser:
                await reply_html(client, message, error("Your account is frozen. Contact an admin."))
            except BannedUser:
                await reply_html(client, message, error("You are banned from the economy."))
            except InsufficientBalance as exc:
                await reply_html(client, message, error(str(exc)))
            except Exception as exc:  # noqa: BLE001 - last-resort guard
                logger.exception("handler %s crashed: %s", func.__name__, exc)
                try:
                    await reply_html(client, message, error("Something went wrong. Try again."))
                except Exception:
                    logger.exception("failed to send error message")

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


def require_reply(client: Client, message: Message) -> Message | None:
    if not getattr(message, "reply_to_message", None) or not message.reply_to_message.from_user:
        return None
    return message.reply_to_message
