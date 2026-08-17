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
    # Promo code redeem is now command-only (/redeem).
    # The automatic text-message detection has been disabled to prevent
    # economy modifications without explicit admin command.
    pass  # Automatic promo detection disabled
