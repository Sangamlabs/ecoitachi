"""Blackjack — USER VS BOT.

Exactly two cards each, no hit/stand/split/double/insurance.  Card values:
2-10 face value, J/Q/K = 10, A = 11 (or 1 if it would bust).  The higher
total wins; equal totals are a draw and the bet is returned.

Rounds resolve instantly (no in-flight session), so the bet lock + settle
happen inside a single coroutine and cannot double-pay.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from services import economy, settings as settings_service, tax as tax_service
from services import transaction as tx_service
from services.economy import ensure_active
from utils.money import MoneyError, format_money

logger = logging.getLogger(__name__)


class BlackjackError(Exception):
    """User-facing blackjack error."""


class BlackjackDisabled(BlackjackError):
    pass


class BlackjackCooldown(BlackjackError):
    def __init__(self, remaining: int) -> None:
        self.remaining = remaining
        super().__init__(f"blackjack:{remaining}")


RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def build_deck() -> list[str]:
    """A fresh shuffled 52-card deck."""
    deck = [rank for rank in RANKS for _ in range(4)]
    random.shuffle(deck)
    return deck


def card_value(card: str) -> int:
    if card == "A":
        return 11
    if card in ("J", "Q", "K"):
        return 10
    return int(card)


def hand_total(cards: list[str]) -> int:
    """Total with soft-ace handling (A counts 11, or 1 when it would bust)."""
    total = sum(card_value(c) for c in cards)
    aces = cards.count("A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def deal(deck: list[str], n: int) -> tuple[list[str], list[str]]:
    """Draw ``n`` cards off the top of the deck, mutating it."""
    hand, deck = deck[:n], deck[n:]
    return hand, deck


def evaluate(user_cards: list[str], bot_cards: list[str], bet: int, config: dict[str, Any]) -> dict[str, Any]:
    """Pure outcome resolution: win / loss / draw."""
    user_total = hand_total(user_cards)
    bot_total = hand_total(bot_cards)
    if user_total == bot_total:
        return {"outcome": "draw", "payout": 0, "profit": 0, "gross_payout": 0}
    if user_total > bot_total:
        multiplier = float(config.get("multiplier", 1.0))
        gross = bet + int(bet * multiplier)
        return {"outcome": "win", "payout": gross, "profit": gross - bet, "gross_payout": gross}
    return {"outcome": "loss", "payout": 0, "profit": -bet, "gross_payout": 0}


async def get_config() -> dict[str, Any]:
    return await settings_service.get_blackjack_config()


async def _lock_bet(user_id: int, bet: int) -> dict[str, Any]:
    from utils.cooldown import cooldown_manager

    if not isinstance(bet, int) or bet <= 0:
        raise MoneyError("Bet must be a positive amount.")
    config = await get_config()
    min_bet = int(config.get("minimum_bet", 0))
    max_bet = int(config.get("maximum_bet", 0))
    if min_bet and bet < min_bet:
        raise BlackjackError(
            f"Bet must be at least {format_money(min_bet)} for <code>BLACKJACK</code>."
        )
    if max_bet and bet > max_bet:
        raise BlackjackError(
            f"Bet must be at most {format_money(max_bet)} for <code>BLACKJACK</code>."
        )

    remaining = await cooldown_manager.check("blackjack", user_id)
    if remaining > 0:
        raise BlackjackCooldown(remaining)

    user = await economy._require_user(user_id)
    await ensure_active(user)
    await economy.remove_wallet(user_id, bet, spend=True)
    return user


async def play(user_id: int, bet: int, *, chat_id: int | None = None) -> dict[str, Any]:
    """Run one blackjack round and return the full outcome."""
    from utils.cooldown import cooldown_manager

    config = await get_config()
    if not config.get("enabled", True):
        raise BlackjackDisabled("Blackjack is currently disabled.")
    await _lock_bet(user_id, bet)

    deck = build_deck()
    user_cards, deck = deal(deck, 2)
    bot_cards, deck = deal(deck, 2)
    evaluation = evaluate(user_cards, bot_cards, bet, config)

    user_total = hand_total(user_cards)
    bot_total = hand_total(bot_cards)
    tx_id: str | None = None

    if evaluation["outcome"] == "draw":
        await economy.add_wallet(user_id, bet, earn=True)
        tx_id = await tx_service.record(
            user_id=user_id,
            ttype=tx_service.BLACKJACK_DRAW,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={"bet": bet, "outcome": "draw", "user_total": user_total, "bot_total": bot_total},
        )
    elif evaluation["outcome"] == "win":
        payout = evaluation["payout"]
        tax = await tax_service.system_tax_amount("blackjack", payout)
        net = payout - tax
        await economy.add_wallet(user_id, net, earn=True)
        if tax > 0:
            await tax_service.collect(user_id, tax)
        tx_id = await tx_service.record(
            user_id=user_id,
            ttype=tx_service.BLACKJACK_WIN,
            amount=net,
            balance_before=0,
            balance_after=0,
            metadata={
                "bet": bet,
                "outcome": "win",
                "user_total": user_total,
                "bot_total": bot_total,
                "gross_payout": payout,
                "tax": tax,
            },
        )
    else:
        tx_id = await tx_service.record(
            user_id=user_id,
            ttype=tx_service.BLACKJACK_LOSS,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={"bet": bet, "outcome": "loss", "user_total": user_total, "bot_total": bot_total},
        )

    cooldown = int(config.get("cooldown", 0))
    if cooldown:
        await cooldown_manager.apply("blackjack", user_id, cooldown)

    return {
        "bet": bet,
        "user_cards": user_cards,
        "bot_cards": bot_cards,
        "user_total": user_total,
        "bot_total": bot_total,
        "outcome": evaluation["outcome"],
        "payout": evaluation["payout"],
        "profit": evaluation["profit"],
        "tx_id": tx_id,
    }
