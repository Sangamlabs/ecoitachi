"""Fly game - configurable difficulty instant game.

Multipliers, risk, win probability, bet limits and cooldown all come from the
MongoDB settings collection (admin-configurable via /flyset and /flytrap).
"""

from __future__ import annotations

import logging
import random
from typing import Any

from services import game_engine
from services.game_engine import GameError

logger = logging.getLogger(__name__)

DIFFICULTIES = ("low", "medium", "high")


def parse_difficulty(raw: str) -> str:
    difficulty = raw.strip().lower()
    if difficulty not in DIFFICULTIES:
        raise GameError(
            f"Unknown difficulty <code>{difficulty}</code>. "
            f"Use <code>low</code>, <code>medium</code> or <code>high</code>."
        )
    return difficulty


def roll(difficulty: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Simulate one fly flight. Pure game logic (no DB side effects)."""
    cfg = settings[difficulty]
    min_mult = float(cfg.get("minimum_multiplier", 1.0))
    max_mult = float(cfg.get("maximum_multiplier", 2.0))
    win_prob = float(cfg.get("win_probability", 0.5))

    multiplier = round(random.uniform(min_mult, max_mult), 2)
    won = random.random() < win_prob
    return {"won": won, "multiplier": multiplier}


async def play(user_id: int, difficulty: str, bet: int, *, chat_id: int | None = None) -> dict[str, Any]:
    """Run a full fly round through the game engine."""
    settings = await game_engine.validate_game_input("fly")
    cfg = settings[difficulty]
    result = roll(difficulty, settings)
    payout = int(bet * result["multiplier"]) if result["won"] else 0
    outcome = await game_engine.instant_game(
        user_id,
        "fly",
        bet,
        won=result["won"],
        payout=payout,
        multiplier=result["multiplier"],
        meta={"difficulty": difficulty, "risk": cfg.get("risk")},
        chat_id=chat_id,
    )
    return {
        "difficulty": difficulty,
        "bet": bet,
        "won": result["won"],
        "multiplier": result["multiplier"],
        "payout": payout,
        "session_id": outcome["session_id"],
    }
