"""Central game engine.

Every game (fly, mines, bet, and all future ones) flows through here so that:

* bets are validated against wallet balance and configured limits,
* cooldowns and one-active-game rules are enforced,
* frozen/banned users cannot gamble,
* the bet is locked (deducted) atomically before a session is created,
* settlement is idempotent (a session can never be paid twice),
* a transaction + audit record exists for every outcome.

Game modules only implement game-specific logic (board, probability, odds)
and call back into this engine.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from database import games as games_db
from services import economy, settings as settings_service, tax as tax_service
from services import transaction as tx_service
from services.economy import ensure_active
from services.global_battle import missions as missions_service
from utils.money import MoneyError

logger = logging.getLogger(__name__)

GAMES = ("fly", "mines", "bet", "colour", "aviator")
ECONOMY_GAMES = {"fly", "mines", "bet", "colour", "aviator"}


class GameError(Exception):
    """User-facing game error."""


class GameInProgress(GameError):
    pass


class GameCooldownError(GameError):
    pass


class NoActiveGame(GameError):
    pass


def new_game_id() -> str:
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


async def validate_game_input(game: str) -> dict[str, Any]:
    if game not in GAMES:
        raise GameError(f"Unknown game: <code>{game}</code>")
    return await settings_service.get_game_settings(game)


async def get_game_cooldown(game: str) -> int:
    settings = await validate_game_input(game)
    cooldown = settings.get("cooldown")
    if cooldown is None:
        cooldown = await settings_service.get_default_cooldown()
    return max(0, int(cooldown))


def _bet_limits_error(game: str, min_bet: int, max_bet: int) -> str:
    from utils.money import format_money

    return (
        f"Bet must be between {format_money(min_bet)} and "
        f"{format_money(max_bet)} for <code>{game}</code>."
    )


async def check_and_lock_bet(
    user_id: int, game: str, bet: int, cooldown_check: bool = True
) -> dict[str, Any]:
    """Validate a bet and atomically lock it out of the wallet.

    Returns ``{user, session_id, bet}`` ready for game-specific setup.
    """
    from utils.cooldown import cooldown_manager

    if game not in GAMES:
        raise GameError(f"Unknown game: <code>{game}</code>")
    if not isinstance(bet, int) or bet <= 0:
        raise MoneyError("Bet must be a positive amount.")

    settings = await settings_service.get_game_settings(game)
    min_bet = int(settings.get("minimum_bet", 0))
    max_bet = int(settings.get("maximum_bet", 0))
    if min_bet and bet < min_bet:
        raise GameError(_bet_limits_error(game, min_bet, max_bet))
    if max_bet and bet > max_bet:
        raise GameError(_bet_limits_error(game, min_bet, max_bet))

    if cooldown_check:
        remaining = await cooldown_manager.check(game, user_id)
        if remaining > 0:
            raise GameCooldownError(f"{game}:{user_id}:{remaining}")

    if await games_db.get_active_session(user_id, game) is not None:
        raise GameInProgress(
            f"You already have an active <code>{game}</code> game. Finish it first."
        )

    user = await economy._require_user(user_id)
    await ensure_active(user)

    # Atomic lock: deduct the bet from the wallet.
    await economy.remove_wallet(user_id, bet, spend=True)

    return {"user": user, "bet": bet, "settings": settings, "user_id": user_id, "game": game}


async def create_session(
    user_id: int,
    game: str,
    bet: int,
    state: dict[str, Any],
    duration: int | None = None,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> str:
    """Persist an active game session and record the GAME_BET transaction.

    ``chat_id``/``message_id`` bind the session to the chat (and inline board
    message) where it started so callbacks can be verified against them.
    """
    session_id = new_game_id()
    doc = {
        "game_id": session_id,
        "game": game,
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "bet": bet,
        "status": "active",
        "state": state,
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + (duration or 0),
    }
    await games_db.insert_session(doc)
    await tx_service.record(
        user_id=user_id,
        ttype=tx_service.GAME_BET,
        amount=bet,
        balance_before=0,
        balance_after=0,
        metadata={"game": game, "session_id": session_id},
    )
    return session_id


async def settle_game(
    session_id: str,
    user_id: int,
    *,
    won: bool,
    payout: int,
    multiplier: float | None = None,
    meta: dict[str, Any] | None = None,
) -> bool:
    """Settle a session exactly once.

    On a win the payout (bet * multiplier) is credited to the wallet.  The
    atomic ``status: active → settled`` transition prevents double payment.

    Returns True when this call performed the settlement.
    """
    session = await games_db.get_session(session_id)
    if session is None:
        raise NoActiveGame("Game session not found.")
    if session.get("user_id") != user_id:
        raise GameError("This game belongs to another user.")
    if session.get("status") != "active":
        return False

    game = session.get("game", "")
    outcome = "won" if won else "lost"
    if won:
        if payout < 0:
            raise MoneyError("Invalid payout.")
        tax = await tax_service.system_tax_amount(game, payout)
        net = payout - tax
        await economy.add_wallet(user_id, net, earn=True)
        if tax > 0:
            await tax_service.collect(user_id, tax)
        await _record_game_tx(
            session,
            tx_service.GAME_WIN,
            net,
            outcome,
            multiplier,
            {"gross_payout": payout, "tax": tax},
        )
    else:
        await _record_game_tx(session, tx_service.GAME_LOSS, 0, outcome, multiplier, meta)

    await games_db.settle_session(session_id, outcome, payout, meta or {})

    # Record mission completion for economy games
    if game in ECONOMY_GAMES:
        await missions_service.record_game_completion(user_id, game)

    return True


async def _record_game_tx(
    session: dict[str, Any],
    ttype: str,
    amount: int,
    outcome: str,
    multiplier: float | None,
    meta: dict[str, Any] | None,
) -> None:
    await economy._require_user(session["user_id"])  # validates existence / ban status
    await tx_service.record(
        user_id=session["user_id"],
        ttype=ttype,
        amount=amount,
        balance_before=0,
        balance_after=0,
        metadata={
            "game": session.get("game"),
            "session_id": session.get("game_id"),
            "bet": session.get("bet", 0),
            "outcome": outcome,
            "multiplier": multiplier,
            **(meta or {}),
        },
    )


async def apply_cooldown(game: str, user_id: int) -> None:
    from utils.cooldown import cooldown_manager

    cooldown = await get_game_cooldown(game)
    if cooldown:
        await cooldown_manager.apply(game, user_id, cooldown)


async def expire_stale_games(game: str) -> list[str]:
    """Settle (lose) games that exceeded their duration. Returns handled ids."""
    settings = await settings_service.get_game_settings(game)
    duration = int(settings.get("duration", 0))
    if duration <= 0:
        return []
    expired = await games_db.find_expired_games(game, duration)
    handled: list[str] = []
    for session in expired:
        try:
            await settle_game(
                session["game_id"],
                session["user_id"],
                won=False,
                payout=0,
                meta={"reason": "timeout"},
            )
            handled.append(session["game_id"])
        except Exception:
            logger.exception("failed to expire game %s", session["game_id"])
    if handled:
        logger.info("expired %s stale %s games", len(handled), game)
    return handled


async def instant_game(
    user_id: int,
    game: str,
    bet: int,
    *,
    won: bool,
    payout: int,
    multiplier: float | None = None,
    meta: dict[str, Any] | None = None,
    chat_id: int | None = None,
) -> dict[str, Any]:
    """Run a single-shot game (fly, bet) end-to-end and return the outcome."""
    await check_and_lock_bet(user_id, game, bet)
    session_id = await create_session(
        user_id, game, bet, {"instant": True}, duration=0, chat_id=chat_id
    )
    try:
        await settle_game(
            session_id,
            user_id,
            won=won,
            payout=payout,
            multiplier=multiplier,
            meta=meta,
        )
    finally:
        await apply_cooldown(game, user_id)
    return {
        "session_id": session_id,
        "bet": bet,
        "won": won,
        "payout": payout,
        "multiplier": multiplier,
    }
