"""Automatic promo code detection.

Users redeem a promo simply by typing the code text in DM, groups or
supergroups — there is no ``/redeem`` command.  This handler runs as the
catch-all text handler (registered last) and is deliberately cheap:

1. fast regex tokenization of the message text;
2. an in-memory active-code cache pre-filter;
3. one authoritative ``redeem`` call against the promo engine.

Forwarded / service / bot / channel messages and commands are ignored, and the
message is fully ignored (no spam) when no promo code is present.
"""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

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


def register(app: Client) -> None:
    @app.on_message(filters.text & ~filters.channel & ~filters.bot & ~filters.service)
    async def on_promo_text(client: Client, message: Message):
        if message.from_user is None:
            return
        if getattr(message, "forward_from", None) or getattr(
            message, "forward_from_chat", None
        ):
            return
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return
        if len(text) > MAX_SCAN_LENGTH:
            return

        tokens = TOKEN_RE.findall(text.upper())
        if not tokens:
            return

        try:
            candidates = await promo_service.cache.candidates(tokens)
        except Exception:
            logger.exception("promo cache lookup failed")
            return
        if not candidates:
            return

        allowed, _reason = await check_gate(message, feature="economy")
        if not allowed:
            return

        for code in candidates:
            try:
                result = await promo_service.redeem(
                    message.from_user.id, code, chat_id=message.chat.id
                )
            except PromoNotFound:
                continue
            except PromoAlreadyUsed:
                await reply_html(client, message, msgs.promo_already_used())
                break
            except PromoExpired:
                await reply_html(client, message, msgs.promo_expired())
                break
            except PromoInactive:
                await reply_html(client, message, msgs.promo_inactive())
                break
            except PromoLimitReached:
                await reply_html(client, message, msgs.promo_limit_reached())
                break
            except (PromoError, PromoRewardError) as exc:
                await reply_html(client, message, msgs.error(str(exc)))
                break
            except Exception:
                logger.exception(
                    "promo redeem failed user=%s code=%s", message.from_user.id, code
                )
                break
            if result:
                await reply_html(client, message, msgs.promo_redeemed(result))
                break
