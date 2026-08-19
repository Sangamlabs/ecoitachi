"""Automatic promo code detection + explicit ``/redeem`` fallback.

Users redeem a promo by typing the code as normal text in DM, groups or
supergroups — no command needed.  Detection runs as the catch-all text
handler and is deliberately cheap:

1. fast regex tokenization of the message text;
2. an in-memory active-code cache pre-filter;
3. one authoritative ``redeem`` call against the promo engine.

The explicit ``/redeem CODE`` command remains available and uses the SAME
engine and responses.  Forwarded / service / bot / channel messages and
commands are ignored, and the message is fully ignored (no spam, no economy
change) unless it contains a token that is a known active promo code.
"""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from config import config
from handlers.common import safe_handler
from services import identity as identity_service
from services import promos as promo_service
from services.promo_rewards import PromoRewardError
from services.promos import (
    PromoAlreadyUsed,
    PromoError,
    PromoExpired,
    PromoInactive,
    PromoLimitReached,
    PromoNotFound,
)
from utils import messages as msgs
from utils.chat import check_gate
from utils.sender import reply_html

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[A-Z0-9]{3,20}")
MAX_SCAN_LENGTH = 1000

NOT_CHANNEL = ~filters.channel & ~filters.bot


async def _redeem_once(client: Client, message: Message, code: str) -> bool:
    """Redeem one code through the shared atomic engine and reply.

    Returns ``True`` when the code was handled (stop scanning further
    candidates), ``False`` when the code simply does not exist (keep
    scanning).  All business rules (limit, per-user limit, expiry, reward,
    transaction, duplicate protection) live in ``promo_service.redeem``.
    """
    try:
        result = await promo_service.redeem(
            message.from_user.id, code, chat_id=message.chat.id
        )
    except PromoNotFound:
        return False
    except PromoAlreadyUsed:
        await reply_html(client, message, msgs.promo_already_used())
        return True
    except PromoExpired:
        await reply_html(client, message, msgs.promo_expired())
        return True
    except PromoInactive:
        await reply_html(client, message, msgs.promo_inactive())
        return True
    except PromoLimitReached:
        await reply_html(client, message, msgs.promo_limit_reached())
        return True
    except (PromoError, PromoRewardError) as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return True
    except Exception:
        logger.exception(
            "promo redeem failed user=%s code=%s", message.from_user.id, code
        )
        return True
    if result:
        await reply_html(client, message, msgs.promo_redeemed(result))
    return True


def register(app: Client) -> None:
    @app.on_message(filters.command("redeem") & NOT_CHANNEL)
    @safe_handler
    async def cmd_redeem(client: Client, message: Message):
        if message.from_user is None:
            return
        if len(message.command) < 2:
            await reply_html(
                client, message, msgs.error("Usage: <code>/redeem CODE</code>")
            )
            return
        await identity_service.ensure_user_from_telegram(message.from_user)
        handled = await _redeem_once(client, message, message.command[1])
        if not handled:
            await reply_html(client, message, msgs.error("Promo code not found."))

    @app.on_message(filters.text & ~filters.channel & ~filters.bot & ~filters.service)
    async def on_promo_text(client: Client, message: Message):
        if message.from_user is None:
            message.continue_propagation()
        if message.from_user.id == config.BOT_ID:
            message.continue_propagation()
        if getattr(message, "forward_from", None) or getattr(
            message, "forward_from_chat", None
        ):
            message.continue_propagation()
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            message.continue_propagation()
        if len(text) > MAX_SCAN_LENGTH:
            message.continue_propagation()

        tokens = TOKEN_RE.findall(text.upper())
        if not tokens:
            message.continue_propagation()

        try:
            candidates = await promo_service.cache.candidates(tokens)
        except Exception:
            logger.exception("promo cache lookup failed")
            message.continue_propagation()
        if not candidates:
            message.continue_propagation()

        allowed, _reason = await check_gate(message, feature="economy")
        if not allowed:
            message.continue_propagation()

        await identity_service.ensure_user_from_telegram(message.from_user)

        for code in candidates:
            if await _redeem_once(client, message, code):
                return
        message.continue_propagation()
