"""Emoji games engine.

Supports single-player rounds (``/sball``, ``/sarrow``, ``/sbasketball``) and
1v1 duels (``/ball``, ``/arrow``, ``/basketball`` + ``/join GAME_ID``) built on
top of Telegram's native animated dice.

Game logic is kept here (pure, parameterized by the actual dice result) so it
can be tested without a live Telegram connection.  Handlers only: send the
animated emoji, wait, read ``message.dice.value`` and pass it back in.

Money flow:
* single player — bet locked at start; WIN credits ``bet + bet*multiplier``,
  LOSS credits nothing;
* duel — both players lock their bet when creating/joining; winner takes the
  pot (``2*bet``), equal results refund both (no money is created);
* every transition is idempotent via the session ``status`` guard.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

from database import emoji_games as emoji_db
from services import economy, settings as settings_service, tax as tax_service
from services import transaction as tx_service
from services.economy import ensure_active
from utils.money import MoneyError, format_money

logger = logging.getLogger(__name__)


class EmojiGameError(Exception):
    """User-facing emoji game error."""


class EmojiGameCooldown(EmojiGameError):
    def __init__(self, game: str, remaining: int) -> None:
        self.game = game
        self.remaining = remaining
        super().__init__(f"{game}:{remaining}")


class EmojiGameInProgress(EmojiGameError):
    pass


class EmojiGameDisabled(EmojiGameError):
    pass


class EmojiDuelNotFound(EmojiGameError):
    pass


class EmojiDuelExpired(EmojiGameError):
    pass


class EmojiSelfJoin(EmojiGameError):
    pass


class EmojiDuelFull(EmojiGameError):
    pass


@dataclass(frozen=True)
class EmojiGameDef:
    game_type: str
    emoji: str
    label: str
    result_min: int
    result_max: int


EMOJI_GAMES: dict[str, EmojiGameDef] = {
    "ball": EmojiGameDef("ball", "🎳", "BALL", 1, 6),
    "arrow": EmojiGameDef("arrow", "🎯", "ARROW", 1, 6),
    "basketball": EmojiGameDef("basketball", "🏀", "BASKETBALL", 1, 5),
}

# /s<game> -> single-player, /<game> -> duel
SINGLE_COMMANDS = {f"s{g}": g for g in EMOJI_GAMES}
DUEL_COMMANDS = {g: g for g in EMOJI_GAMES}


def get_game_def(game_type: str) -> EmojiGameDef:
    try:
        return EMOJI_GAMES[game_type]
    except KeyError:
        raise EmojiGameError(f"Unknown emoji game: <code>{game_type}</code>") from None


def valid_result(game_type: str, result: int) -> bool:
    game_def = get_game_def(game_type)
    return game_def.result_min <= result <= game_def.result_max


def new_session_id() -> str:
    return f"emoji-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def evaluate_single(game_type: str, result: int, bet: int, config: dict[str, Any]) -> dict[str, Any]:
    """Pure win/loss resolution for a single-player round."""
    if not valid_result(game_type, result):
        raise EmojiGameError("Invalid dice result.")
    rule = config.get("win_rule", "gte")
    target = int(config.get("win_target", get_game_def(game_type).result_max))
    won = result >= target if rule == "gte" else result == target
    if won:
        multiplier = float(config.get("multiplier", 1.0))
        gross = bet + int(bet * multiplier)
        return {
            "won": True,
            "outcome": "win",
            "payout": gross,
            "profit": gross - bet,
            "gross_payout": gross,
        }
    return {
        "won": False,
        "outcome": "loss",
        "payout": 0,
        "profit": -bet,
        "gross_payout": 0,
    }


def evaluate_duel(result1: int, result2: int, bet: int) -> dict[str, Any]:
    """Pure duel resolution. Returns the outcome and pot for the winner."""
    if result1 == result2:
        return {"outcome": "draw", "payout": 0, "profit": 0, "gross_payout": 0}
    player1_wins = result1 > result2
    return {
        "outcome": "player1" if player1_wins else "player2",
        "payout": 2 * bet,
        "profit": bet,
        "gross_payout": 2 * bet,
    }


async def _bet_limits_error(game_type: str, min_bet: int, max_bet: int) -> str:
    game_def = get_game_def(game_type)
    return (
        f"Bet must be between {format_money(min_bet)} and "
        f"{format_money(max_bet)} for <code>{game_def.emoji} {game_def.label}</code>."
    )


async def _validate_and_lock_bet(
    user_id: int, game_type: str, bet: int, config: dict[str, Any]
) -> dict[str, Any]:
    from utils.cooldown import cooldown_manager

    if not isinstance(bet, int) or bet <= 0:
        raise MoneyError("Bet must be a positive amount.")

    min_bet = int(config.get("minimum_bet", 0))
    max_bet = int(config.get("maximum_bet", 0))
    if min_bet and bet < min_bet:
        raise EmojiGameError(await _bet_limits_error(game_type, min_bet, max_bet))
    if max_bet and bet > max_bet:
        raise EmojiGameError(await _bet_limits_error(game_type, min_bet, max_bet))

    remaining = await cooldown_manager.check(game_type, user_id)
    if remaining > 0:
        raise EmojiGameCooldown(game_type, remaining)

    if await emoji_db.find_active(user_id) is not None:
        raise EmojiGameInProgress(
            "You already have an active emoji game. Finish it first."
        )

    user = await economy._require_user(user_id)
    await ensure_active(user)

    await economy.remove_wallet(user_id, bet, spend=True)
    return user


async def _record_bet(session: dict[str, Any], user_id: int) -> str:
    return await tx_service.record(
        user_id=user_id,
        ttype=tx_service.GAME_BET,
        amount=int(session.get("bet", 0)),
        balance_before=0,
        balance_after=0,
        metadata={
            "game": session.get("game_type"),
            "mode": session.get("mode"),
            "session_id": session.get("session_id"),
            "game_id": session.get("game_id"),
            "bet": session.get("bet", 0),
        },
    )


async def start_single(
    user_id: int,
    game_type: str,
    bet: int,
    *,
    chat_id: int | None = None,
    username: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Validate + lock the bet and create a single-player session."""
    from utils.cooldown import cooldown_manager

    config = await get_config(game_type)
    if not config.get("enabled", True) or not config.get("single_enabled", True):
        raise EmojiGameDisabled("This game is currently disabled.")
    user = await _validate_and_lock_bet(user_id, game_type, bet, config)

    session_id = new_session_id()
    now = int(time.time())
    session = {
        "session_id": session_id,
        "game_id": session_id,
        "mode": "single",
        "game_type": game_type,
        "chat_id": chat_id,
        "message_id": None,
        "player1_id": user_id,
        "player1_username": username,
        "player1_name": name or (user.get("first_name") or "Player 1"),
        "player2_id": None,
        "player2_username": None,
        "player2_name": None,
        "bet": bet,
        "status": "active",
        "outcome": None,
        "player1_result": None,
        "player2_result": None,
        "winner_id": None,
        "loser_id": None,
        "payout": None,
        "profit": None,
        "created_at": now,
        "joined_at": None,
        "started_at": now,
        "completed_at": None,
        "expires_at": None,
    }
    await emoji_db.insert_session(session)
    await _record_bet(session, user_id)
    cooldown = int(config.get("cooldown", 0))
    if cooldown:
        await cooldown_manager.apply(game_type, user_id, cooldown)
    return {"session_id": session_id, "bet": bet, "game_type": game_type}


async def settle_single(
    session_id: str, result: int
) -> dict[str, Any] | None:
    """Settle a single-player round from the real dice result.

    Idempotent: returns None when the session was already settled.
    """
    session = await emoji_db.get_session(session_id)
    if session is None or session.get("status") != "active":
        return None
    game_type = session["game_type"]
    bet = int(session.get("bet", 0))
    config = await get_config(game_type)
    evaluation = evaluate_single(game_type, result, bet, config)

    settled = await emoji_db.settle_single(
        session_id,
        outcome=evaluation["outcome"],
        payout=evaluation["payout"],
        profit=evaluation["profit"],
        player_result=result,
    )
    if not settled:
        return None

    user_id = session["player1_id"]
    tx_id: str | None = None
    if evaluation["won"]:
        payout = evaluation["payout"]
        tax = await tax_service.system_tax_amount("emoji", payout)
        net = payout - tax
        await economy.add_wallet(user_id, net, earn=True)
        if tax > 0:
            await tax_service.collect(user_id, tax)
        tx_id = await tx_service.record(
            user_id=user_id,
            ttype=tx_service.EMOJI_GAME_WIN,
            amount=net,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "session_id": session_id,
                "bet": bet,
                "outcome": "win",
                "result": result,
                "gross_payout": payout,
                "tax": tax,
                "multiplier": config.get("multiplier"),
            },
        )
    else:
        tx_id = await tx_service.record(
            user_id=user_id,
            ttype=tx_service.EMOJI_GAME_LOSS,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "session_id": session_id,
                "bet": bet,
                "outcome": "loss",
                "result": result,
            },
        )
    return {
        "session_id": session_id,
        "game_type": game_type,
        "bet": bet,
        "result": result,
        "won": evaluation["won"],
        "outcome": evaluation["outcome"],
        "payout": evaluation["payout"],
        "profit": evaluation["profit"],
        "tx_id": tx_id,
    }


async def _new_game_id() -> str:
    while True:
        candidate = str(random.randint(1000, 9999))
        if await emoji_db.get_duel(candidate) is None:
            return candidate


async def create_duel(
    user_id: int,
    game_type: str,
    bet: int,
    *,
    chat_id: int | None = None,
    username: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Create a duel lobby: lock the creator's bet, return the 4-digit id."""
    from utils.cooldown import cooldown_manager

    config = await get_config(game_type)
    if not config.get("enabled", True) or not config.get("duel_enabled", True):
        raise EmojiGameDisabled("This game is currently disabled for duels.")
    user = await _validate_and_lock_bet(user_id, game_type, bet, config)

    game_id = await _new_game_id()
    now = int(time.time())
    expiry = int(config.get("lobby_expiry", 300))
    session_id = new_session_id()
    session = {
        "session_id": session_id,
        "game_id": game_id,
        "mode": "duel",
        "game_type": game_type,
        "chat_id": chat_id,
        "message_id": None,
        "player1_id": user_id,
        "player1_username": username,
        "player1_name": name or (user.get("first_name") or "Player 1"),
        "player2_id": None,
        "player2_username": None,
        "player2_name": None,
        "bet": bet,
        "status": "waiting",
        "outcome": None,
        "player1_result": None,
        "player2_result": None,
        "winner_id": None,
        "loser_id": None,
        "payout": None,
        "profit": None,
        "created_at": now,
        "joined_at": None,
        "started_at": None,
        "completed_at": None,
        "expires_at": now + expiry,
    }
    await emoji_db.insert_session(session)
    await _record_bet(session, user_id)
    cooldown = int(config.get("cooldown", 0))
    if cooldown:
        await cooldown_manager.apply(game_type, user_id, cooldown)
    return {
        "session_id": session_id,
        "game_id": game_id,
        "bet": bet,
        "game_type": game_type,
        "expires_at": now + expiry,
    }


async def join_duel(
    game_id: str,
    user2_id: int,
    *,
    chat_id: int | None = None,
    username: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Join a duel lobby: validate, lock the joiner's bet, start the duel."""
    from utils.cooldown import cooldown_manager

    session = await emoji_db.get_duel(game_id)
    if session is None:
        raise EmojiDuelNotFound(f"No active duel with code <code>{game_id}</code>.")
    game_type = session["game_type"]
    bet = int(session.get("bet", 0))
    config = await get_config(game_type)

    if session.get("status") != "waiting":
        raise EmojiDuelFull(f"Duel <code>{game_id}</code> is already in progress.")
    if int(session.get("expires_at", 0)) <= int(time.time()):
        raise EmojiDuelExpired(f"Duel <code>{game_id}</code> has expired.")
    if session.get("player1_id") == user2_id:
        raise EmojiSelfJoin("You cannot join your own duel.")

    if await emoji_db.find_active(user2_id) is not None:
        raise EmojiGameInProgress(
            "You already have an active emoji game. Finish it first."
        )

    remaining = await cooldown_manager.check(game_type, user2_id)
    if remaining > 0:
        raise EmojiGameCooldown(game_type, remaining)

    user2 = await economy._require_user(user2_id)
    await ensure_active(user2)
    await economy.remove_wallet(user2_id, bet, spend=True)

    joined = await emoji_db.try_join(
        game_id,
        {
            "player2_id": user2_id,
            "player2_username": username,
            "player2_name": name or (user2.get("first_name") or "Player 2"),
        },
    )
    if joined is None:
        # The bet was already locked — refund it when the lobby slipped away
        # (another player joined or it expired in the meantime).
        await economy.add_wallet(user2_id, bet, earn=True)
        await tx_service.record(
            user_id=user2_id,
            ttype=tx_service.EMOJI_DUEL_REFUND,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "game_id": game_id,
                "bet": bet,
                "reason": "join_race",
            },
        )
        raise EmojiDuelExpired(
            f"Duel <code>{game_id}</code> could not be joined (full or expired)."
        )
    await _record_bet(joined, user2_id)
    cooldown = int(config.get("cooldown", 0))
    if cooldown:
        await cooldown_manager.apply(game_type, user2_id, cooldown)
    return {
        "session_id": joined["session_id"],
        "game_id": game_id,
        "game_type": game_type,
        "bet": bet,
        "player1_id": joined["player1_id"],
        "player1_name": joined.get("player1_name", "Player 1"),
        "player2_id": user2_id,
        "player2_name": joined.get("player2_name", "Player 2"),
    }


async def settle_duel(
    session_id: str, result1: int, result2: int
) -> dict[str, Any] | None:
    """Settle a duel from the two real dice results.

    Idempotent: returns None when already settled.
    """
    session = await emoji_db.get_session(session_id)
    if session is None or session.get("status") != "active":
        return None
    game_type = session["game_type"]
    bet = int(session.get("bet", 0))
    game_def = get_game_def(game_type)
    if not (game_def.result_min <= result1 <= game_def.result_max):
        raise EmojiGameError("Invalid first player dice result.")
    if not (game_def.result_min <= result2 <= game_def.result_max):
        raise EmojiGameError("Invalid second player dice result.")

    evaluation = evaluate_duel(result1, result2, bet)
    if evaluation["outcome"] == "draw":
        winner_id = None
        loser_id = None
    elif evaluation["outcome"] == "player1":
        winner_id = session["player1_id"]
        loser_id = session["player2_id"]
    else:
        winner_id = session["player2_id"]
        loser_id = session["player1_id"]

    settled = await emoji_db.settle_duel(
        session_id,
        player1_result=result1,
        player2_result=result2,
        winner_id=winner_id,
        loser_id=loser_id,
        outcome=evaluation["outcome"],
        payout=evaluation["payout"],
        profit=evaluation["profit"],
    )
    if not settled:
        return None

    if evaluation["outcome"] == "draw":
        await economy.add_wallet(session["player1_id"], bet, earn=True)
        await economy.add_wallet(session["player2_id"], bet, earn=True)
        await tx_service.record(
            user_id=session["player1_id"],
            ttype=tx_service.EMOJI_DUEL_DRAW,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "session_id": session_id,
                "game_id": session.get("game_id"),
                "bet": bet,
                "outcome": "draw",
            },
        )
        await tx_service.record(
            user_id=session["player2_id"],
            ttype=tx_service.EMOJI_DUEL_DRAW,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "session_id": session_id,
                "game_id": session.get("game_id"),
                "bet": bet,
                "outcome": "draw",
            },
        )
    else:
        payout = evaluation["payout"]
        tax = await tax_service.system_tax_amount("emoji", payout)
        net = payout - tax
        await economy.add_wallet(winner_id, net, earn=True)
        if tax > 0:
            await tax_service.collect(winner_id, tax)
        await tx_service.record(
            user_id=winner_id,
            ttype=tx_service.EMOJI_DUEL_WIN,
            amount=net,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "session_id": session_id,
                "game_id": session.get("game_id"),
                "bet": bet,
                "outcome": "win",
                "gross_payout": payout,
                "tax": tax,
            },
        )
        await tx_service.record(
            user_id=loser_id,
            ttype=tx_service.EMOJI_DUEL_LOSS,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": game_type,
                "session_id": session_id,
                "game_id": session.get("game_id"),
                "bet": bet,
                "outcome": "loss",
            },
        )

    return {
        "session_id": session_id,
        "game_type": game_type,
        "bet": bet,
        "result1": result1,
        "result2": result2,
        "outcome": evaluation["outcome"],
        "payout": evaluation["payout"],
        "profit": evaluation["profit"],
        "winner_id": winner_id,
        "loser_id": loser_id,
    }


async def expire_stale_duels() -> list[str]:
    """Refund creators of expired waiting duels. Idempotent per session."""
    expired = await emoji_db.find_expired_duels()
    handled: list[str] = []
    for session in expired:
        try:
            if await emoji_db.mark_expired(session["session_id"]):
                await economy.add_wallet(session["player1_id"], int(session.get("bet", 0)), earn=True)
                await tx_service.record(
                    user_id=session["player1_id"],
                    ttype=tx_service.EMOJI_DUEL_REFUND,
                    amount=int(session.get("bet", 0)),
                    balance_before=0,
                    balance_after=0,
                    metadata={
                        "game": session.get("game_type"),
                        "session_id": session["session_id"],
                        "game_id": session.get("game_id"),
                        "bet": session.get("bet", 0),
                        "reason": "expired",
                    },
                )
                handled.append(session["session_id"])
        except Exception:
            logger.exception("failed to refund expired duel %s", session["session_id"])
    return handled


async def get_config(game_type: str) -> dict[str, Any]:
    """Return merged per-game config, defaulting to the registry's spec."""
    return await settings_service.get_emoji_game_config(game_type)
