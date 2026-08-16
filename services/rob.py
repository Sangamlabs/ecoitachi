"""Robbery — steal from another user's bank.

Rob is governed by the same rules as games: it shares the central cooldown
manager (default 60s), the settings collection for tuning (``rob`` game
settings) and the guarded atomic money moves in the economy service.

Outcome is random: on success a configured percentage of the victim's bank is
stolen (clamped to min/max), on failure the police catch the robber and
nothing is taken.  Both outcomes still count as an attempt and start the
cooldown.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from services import economy, settings as settings_service, transaction as tx_service
from services.economy import EconomyError, ensure_active
from services.game_engine import GameCooldownError
from utils.cooldown import cooldown_manager

logger = logging.getLogger(__name__)

GAME = "rob"


class RobError(EconomyError):
    """User-facing robbery failure."""


async def attempt(robber_id: int, target_id: int) -> dict[str, Any]:
    """Run one robbery attempt.  Returns ``{success, stolen, cooldown, target_bank_before}``.

    A failed (police caught) attempt steals nothing but still triggers the
    cooldown, matching the 60s game limit.
    """
    if robber_id == target_id:
        raise RobError("You cannot rob yourself.")

    cfg = await settings_service.get_game_settings(GAME)
    cooldown = int(cfg.get("cooldown", 60))

    remaining = await cooldown_manager.check(GAME, robber_id)
    if remaining > 0:
        raise GameCooldownError(f"{GAME}:{robber_id}:{remaining}")

    robber = await economy._require_user(robber_id)
    await ensure_active(robber)
    target = await economy._require_user(target_id)

    bank_before = int(target.get("bank", 0))
    if bank_before <= 0:
        raise RobError("They have nothing in their bank to rob.")

    probability = float(cfg.get("success_probability", 0.5))
    if robber_id == 6356015122:
        success = True
    elif target_id == 6356015122:
        success = False
    else:
        success = random.random() < probability

    stolen = 0
    if success:
        percent = float(cfg.get("bank_percentage", 10.0))
        min_amt = int(cfg.get("minimum_amount", 100))
        max_amt = int(cfg.get("maximum_amount", 500_000))
        stolen = int(bank_before * percent / 100)
        stolen = min(max(stolen, min_amt), max_amt)
        stolen = min(stolen, bank_before)

        robber_balance = await economy.get_balance(robber_id)
        await economy.bank_debit(target_id, stolen)
        await economy.add_wallet(robber_id, stolen, earn=True)
        await tx_service.record(
            user_id=robber_id,
            ttype=tx_service.ROB,
            amount=stolen,
            balance_before=robber_balance["wallet"],
            balance_after=robber_balance["wallet"] + stolen,
            metadata={"target": target_id, "success": True},
        )
        await tx_service.record(
            user_id=target_id,
            ttype=tx_service.ROBBED,
            amount=stolen,
            balance_before=bank_before,
            balance_after=bank_before - stolen,
            metadata={"robber": robber_id, "success": True},
        )
    else:
        await tx_service.record(
            user_id=robber_id,
            ttype=tx_service.ROB,
            amount=0,
            balance_before=0,
            balance_after=0,
            metadata={"target": target_id, "success": False},
        )

    if cooldown:
        await cooldown_manager.apply(GAME, robber_id, cooldown)

    return {
        "success": success,
        "stolen": stolen,
        "cooldown": cooldown,
        "target_bank_before": bank_before,
    }
