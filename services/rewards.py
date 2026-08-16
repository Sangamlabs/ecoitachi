"""Free currency rewards — daily / weekly / monthly claims.

Amounts and cooldowns are stored in the centralized settings collection
(``rewards`` key, admin-configurable via ``/setreward``).  Cooldowns reuse the
shared cooldown manager (keyed by reward kind), so claims survive restarts and
the 24h / 7d / 30d limits are enforced exactly once per user.
"""

from __future__ import annotations

import logging
from typing import Any

from services import economy, settings as settings_service, transaction as tx_service
from services.economy import EconomyError, ensure_active
from services.game_engine import GameCooldownError
from utils.cooldown import cooldown_manager

logger = logging.getLogger(__name__)

KINDS = ("daily", "weekly", "monthly")


class RewardError(EconomyError):
    """User-facing reward failure."""


async def get_reward(kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise RewardError("Unknown reward.")
    rewards = await settings_service.get_rewards()
    return dict(rewards.get(kind, {}))


async def claim(user_id: int, kind: str) -> dict[str, Any]:
    """Claim a free reward.  Returns ``{kind, amount, cooldown}``.

    Raises :class:`GameCooldownError` while a claim is on cooldown so the
    shared handler renders the familiar "on cooldown" message.
    """
    if kind not in KINDS:
        raise RewardError("Unknown reward.")
    cfg = await get_reward(kind)
    amount = int(cfg.get("amount", 0))
    cooldown = int(cfg.get("cooldown", 0))
    if amount <= 0:
        raise RewardError("This reward is not configured.")

    remaining = await cooldown_manager.check(kind, user_id)
    if remaining > 0:
        raise GameCooldownError(f"{kind}:{user_id}:{remaining}")

    user = await economy._require_user(user_id)
    await ensure_active(user)

    before = await economy.get_balance(user_id)
    await economy.add_wallet(user_id, amount, earn=True)
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.REWARD,
        amount=amount,
        balance_before=before["wallet"],
        balance_after=before["wallet"] + amount,
        metadata={"kind": kind, "cooldown": cooldown},
    )
    if cooldown:
        await cooldown_manager.apply(kind, user_id, cooldown)

    return {"kind": kind, "amount": amount, "cooldown": cooldown}
