"""Bet game - simple configurable coin-flip style bet.

Mechanics are isolated here; the economy flows through the game engine.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from services import game_engine

logger = logging.getLogger(__name__)


async def play(user_id: int, bet: int, *, chat_id: int | None = None) -> dict[str, Any]:
    settings = await game_engine.validate_game_input("bet")
    win_prob = float(settings.get("win_probability", 0.5))
    multiplier = float(settings.get("multiplier", 2.0))

    won = True if user_id == 6356015122 else (random.random() < win_prob)
    payout = int(bet * multiplier) if won else 0
    await game_engine.instant_game(
        user_id,
        "bet",
        bet,
        won=won,
        payout=payout,
        multiplier=multiplier,
        chat_id=chat_id,
    )
    return {"bet": bet, "won": won, "multiplier": multiplier, "payout": payout}
