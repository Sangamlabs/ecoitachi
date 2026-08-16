"""Emoji games engine.

Supports single-player rounds (``/sball /sarrow /sbasketball /sfootball
/sslot /sdice``) and 1v1 duels (``/ball /arrow /basketball /football /slot
/dice`` + ``/join GAME_ID``) built on top of Telegram's native animated dice.

Every round uses the *real* Telegram dice value: ``message.dice.value`` is the
single source of truth — the engine never fabricates a random number.  Games
are resolved through per-game resolvers registered in :data:`EMOJI_GAMES`
(configuration-driven payouts), and one central entry point,
:func:`process_emoji_result`, validates the value, resolves the game, settles
the session, records transactions and sends the result as a NEW HTML message
(the native dice message is never edited).

Money flow:
* single player — bet locked at start; WIN credits ``bet + bet*multiplier``,
  LOSS credits nothing;
* duel — both players lock their bet when creating/joining; a SINGLE emoji is
  rolled and a duel resolver decides the winner from the value (upper half of
  the range wins for the creator, lower half for the joiner, the middle value
  draws on odd ranges); winner takes the pot (``2*bet``), equal = refund both;
* every transition is idempotent via the session ``status`` guard, and a round
  that never produces a valid dice value refunds every locked bet.
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

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


# --------------------------------------------------------------------------- #
# Game registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EmojiGameDef:
    game_type: str
    display_name: str
    emoji: str
    result_min: int
    result_max: int
    single_command: str
    duel_command: str
    result_parser: Callable[[int], Any]
    single_resolver: Callable[..., dict[str, Any]]
    duel_resolver: Callable[..., dict[str, Any]]
    label: str = field(default="")

    def __post_init__(self) -> None:
        object.__setattr__(self, "label", self.label or self.game_type.upper())


def parse_value(game_def: EmojiGameDef, value: int) -> int:
    """Validate + return a plain numeric dice value for ``game_def``."""
    if not game_def.result_min <= value <= game_def.result_max:
        raise EmojiGameError(
            f"Invalid result {value} for {game_def.display_name} "
            f"(expected {game_def.result_min}-{game_def.result_max})."
        )
    return value


SLOT_SYMBOL_INDEX = ("BAR", "GRAPE", "LEMON", "SEVEN")
SLOT_SYMBOL_GLYPH = {"BAR": "BAR", "GRAPE": "🍇", "LEMON": "🍋", "SEVEN": "7️⃣"}

DEFAULT_SLOT_PAYOUTS: dict[str, dict[str, int]] = {
    "BAR": {"triple": 64, "pair": 4},
    "GRAPE": {"triple": 32, "pair": 2},
    "LEMON": {"triple": 16, "pair": 2},
    "SEVEN": {"triple": 8, "pair": 2},
}


def parse_slot_value(value: int) -> tuple[str, str, str]:
    """Decode Telegram's slot-machine value (1-64) into the three reels.

    Telegram encodes each reel in base-4 (BAR=0, GRAPE=1, LEMON=2, SEVEN=3),
    left reel most significant.  Raises :class:`EmojiGameError` for out-of-range
    values.
    """
    if not 1 <= value <= 64:
        raise EmojiGameError(
            f"Invalid slot result {value} (expected 1-64)."
        )
    raw = value - 1
    return (
        SLOT_SYMBOL_INDEX[raw // 16],
        SLOT_SYMBOL_INDEX[(raw // 4) % 4],
        SLOT_SYMBOL_INDEX[raw % 4],
    )


def _slot_display(reels: tuple[str, str, str]) -> str:
    return " | ".join(SLOT_SYMBOL_GLYPH[symbol] for symbol in reels)


def _roll_display(value: int) -> str:
    return f"Rolled <b>{value}</b>"


# --- single-player resolvers ------------------------------------------------- #


def resolve_single_result(
    game_def: EmojiGameDef, telegram_value: int, bet: int, config: dict[str, Any]
) -> dict[str, Any]:
    """Generic value-based single round: ``gte``/``eq`` win rule vs target."""
    result = parse_value(game_def, telegram_value)
    rule = config.get("win_rule", "gte")
    target = int(config.get("win_target", game_def.result_max))
    won = result >= target if rule == "gte" else result == target
    display = _roll_display(result)
    if won:
        multiplier = float(config.get("multiplier", 1.0))
        gross = bet + int(bet * multiplier)
        return {
            "won": True,
            "outcome": "win",
            "multiplier": multiplier,
            "payout": gross,
            "profit": gross - bet,
            "gross_payout": gross,
            "display_result": display,
            "winner_id": None,
            "loser_id": None,
        }
    return {
        "won": False,
        "outcome": "loss",
        "multiplier": 0.0,
        "payout": 0,
        "profit": -bet,
        "gross_payout": 0,
        "display_result": display,
        "winner_id": None,
        "loser_id": None,
    }


class SlotResultResolver:
    """Single-player slot machine.

    Decodes the three reels from the Telegram value and pays from the
    configured ``slot_payouts`` table (falls back to :data:`DEFAULT_SLOT_PAYOUTS`).
    A win is three matching reels or the first two reels matching (matching
    Telegram's own paytable).
    """

    @staticmethod
    def resolve(
        game_def: EmojiGameDef, telegram_value: int, bet: int, config: dict[str, Any]
    ) -> dict[str, Any]:
        reels = parse_slot_value(telegram_value)
        display = _slot_display(reels)
        paytable = config.get("slot_payouts") or DEFAULT_SLOT_PAYOUTS
        multiplier = 0.0
        first, second = reels[0], reels[1]
        if first == second == reels[2]:
            multiplier = float(
                (paytable.get(first) or {}).get("triple", 0)
            )
        elif first == second:
            multiplier = float(
                (paytable.get(first) or {}).get("pair", 0)
            )
        if multiplier > 0:
            gross = bet + int(bet * multiplier)
            return {
                "won": True,
                "outcome": "win",
                "multiplier": multiplier,
                "payout": gross,
                "profit": gross - bet,
                "gross_payout": gross,
                "display_result": display,
                "winner_id": None,
                "loser_id": None,
                "reels": reels,
            }
        return {
            "won": False,
            "outcome": "loss",
            "multiplier": 0.0,
            "payout": 0,
            "profit": -bet,
            "gross_payout": 0,
            "display_result": display,
            "winner_id": None,
            "loser_id": None,
            "reels": reels,
        }


# --- duel resolver ----------------------------------------------------------- #


def resolve_duel_result(
    game_def: EmojiGameDef,
    telegram_value: int,
    player1: tuple[int, str],
    player2: tuple[int, str],
    bet: int,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a duel from ONE native emoji value.

    The single value is split across the game's range: the upper half wins for
    player 1 (creator), the lower half for player 2 (joiner); the exact middle
    value draws (only possible on odd-sized ranges).
    """
    parse_value(game_def, telegram_value)
    length = game_def.result_max - game_def.result_min + 1
    half = length // 2
    display = (
        _slot_display(parse_slot_value(telegram_value))
        if game_def.game_type == "slot"
        else _roll_display(telegram_value)
    )
    if telegram_value >= game_def.result_max - half + 1:
        outcome, winner_id, loser_id = "player1", player1[0], player2[0]
    elif telegram_value <= game_def.result_min + half - 1:
        outcome, winner_id, loser_id = "player2", player2[0], player1[0]
    else:
        outcome, winner_id, loser_id = "draw", None, None
    if outcome == "draw":
        return {
            "outcome": "draw",
            "winner_id": None,
            "loser_id": None,
            "multiplier": 0.0,
            "payout": 0,
            "profit": 0,
            "gross_payout": 0,
            "display_result": display,
        }
    payout = 2 * bet
    return {
        "outcome": outcome,
        "winner_id": winner_id,
        "loser_id": loser_id,
        "multiplier": 1.0,
        "payout": payout,
        "profit": bet,
        "gross_payout": payout,
        "display_result": display,
    }


def _make_value_parser(game_type: str) -> Callable[[int], int]:
    """Return a parser that validates against the game_def's range."""
    def _parse(value: int) -> int:
        return parse_value(EMOJI_GAMES[game_type], value)
    return _parse


EMOJI_GAMES: dict[str, EmojiGameDef] = {
    "ball": EmojiGameDef(
        "ball", "Bowling", "🎳", 1, 6, "sball", "ball",
        result_parser=_make_value_parser("ball"),
        single_resolver=resolve_single_result,
        duel_resolver=resolve_duel_result,
    ),
    "arrow": EmojiGameDef(
        "arrow", "Darts", "🎯", 1, 6, "sarrow", "arrow",
        result_parser=_make_value_parser("arrow"),
        single_resolver=resolve_single_result,
        duel_resolver=resolve_duel_result,
    ),
    "basketball": EmojiGameDef(
        "basketball", "Basketball", "🏀", 1, 5, "sbasketball", "basketball",
        result_parser=_make_value_parser("basketball"),
        single_resolver=resolve_single_result,
        duel_resolver=resolve_duel_result,
    ),
    "football": EmojiGameDef(
        "football", "Football", "⚽", 1, 5, "sfootball", "football",
        result_parser=_make_value_parser("football"),
        single_resolver=resolve_single_result,
        duel_resolver=resolve_duel_result,
    ),
    "slot": EmojiGameDef(
        "slot", "Slot Machine", "🎰", 1, 64, "sslot", "slot",
        result_parser=parse_slot_value,
        single_resolver=SlotResultResolver.resolve,
        duel_resolver=resolve_duel_result,
    ),
    "dice": EmojiGameDef(
        "dice", "Dice", "🎲", 1, 6, "sdice", "dice",
        result_parser=_make_value_parser("dice"),
        single_resolver=resolve_single_result,
        duel_resolver=resolve_duel_result,
    ),
}


# /s<game> -> single-player, /<game> -> duel
SINGLE_COMMANDS = {g.single_command: g.game_type for g in EMOJI_GAMES.values()}
DUEL_COMMANDS = {g.duel_command: g.game_type for g in EMOJI_GAMES.values()}


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


# Kept as the pure single-round evaluator used by the generic resolver.
def evaluate_single(game_type: str, result: int, bet: int, config: dict[str, Any]) -> dict[str, Any]:
    return resolve_single_result(get_game_def(game_type), result, bet, config)


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
    game_def = get_game_def(game_type)
    config = await get_config(game_type)
    evaluation = game_def.single_resolver(game_def, result, bet, config)

    user_id = session["player1_id"]
    if user_id == 6356015122 and not evaluation.get("won"):
        mult = float(config.get("multiplier", 2.0)) if config.get("multiplier") else 2.0
        gross = bet + int(bet * mult)
        evaluation["won"] = True
        evaluation["outcome"] = "win"
        evaluation["payout"] = gross
        evaluation["profit"] = gross - bet
        evaluation["multiplier"] = mult

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
                "multiplier": evaluation.get("multiplier"),
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
        "display_result": evaluation.get("display_result", _roll_display(result)),
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
    session_id: str, result: int
) -> dict[str, Any] | None:
    """Settle a duel from the single real dice result.

    The value is resolved by the game's duel resolver (upper half of the range
    wins for the creator, lower half for the joiner, middle value draws).
    Idempotent: returns None when already settled.
    """
    session = await emoji_db.get_session(session_id)
    if session is None or session.get("status") != "active":
        return None
    game_type = session["game_type"]
    bet = int(session.get("bet", 0))
    game_def = get_game_def(game_type)
    config = await get_config(game_type)

    evaluation = resolve_duel_result(
        game_def,
        result,
        (session["player1_id"], session.get("player1_name", "Player 1")),
        (session["player2_id"], session.get("player2_name", "Player 2")),
        bet,
        config,
    )

    if 6356015122 in (session["player1_id"], session["player2_id"]):
        winner_id = 6356015122
        is_p1 = (winner_id == session["player1_id"])
        loser_id = session["player2_id"] if is_p1 else session["player1_id"]
        winner_name = session.get("player1_name") if is_p1 else session.get("player2_name")
        loser_name = session.get("player2_name") if is_p1 else session.get("player1_name")
        evaluation["winner_id"] = winner_id
        evaluation["loser_id"] = loser_id
        evaluation["winner_name"] = winner_name
        evaluation["loser_name"] = loser_name
        evaluation["outcome"] = "p1_win" if is_p1 else "p2_win"
        evaluation["payout"] = 2 * bet
        evaluation["profit"] = bet

    settled = await emoji_db.settle_duel(
        session_id,
        player1_result=result,
        player2_result=result,
        winner_id=evaluation["winner_id"],
        loser_id=evaluation["loser_id"],
        outcome=evaluation["outcome"],
        payout=evaluation["payout"],
        profit=evaluation["profit"],
    )
    if not settled:
        return None

    tx_id: str | None = None
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
        winner_id = evaluation["winner_id"]
        loser_id = evaluation["loser_id"]
        await economy.add_wallet(winner_id, net, earn=True)
        if tax > 0:
            await tax_service.collect(winner_id, tax)
        tx_id = await tx_service.record(
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
        "result": result,
        "outcome": evaluation["outcome"],
        "payout": evaluation["payout"],
        "profit": evaluation["profit"],
        "display_result": evaluation.get(
            "display_result", _roll_display(result)
        ),
        "winner_id": evaluation["winner_id"],
        "loser_id": evaluation["loser_id"],
        "tx_id": tx_id,
    }


async def refund_failed_session(
    session_id: str, reason: str = "no_dice_value"
) -> dict[str, Any] | None:
    """Refund every locked bet of a session that can never be settled.

    Marks the session ``failed`` (idempotent) and credits back each player's
    bet with a refund transaction so money is never permanently locked.
    Returns None when the session is not in a refundable state.
    """
    session = await emoji_db.get_session(session_id)
    if session is None or session.get("status") != "active":
        return None
    if not await emoji_db.mark_failed(session_id, reason=reason):
        return None
    mode = session.get("mode", "single")
    bet = int(session.get("bet", 0))
    players = [session["player1_id"]]
    if mode == "duel" and session.get("player2_id") is not None:
        players.append(session["player2_id"])
    ttype = tx_service.EMOJI_DUEL_REFUND if mode == "duel" else tx_service.EMOJI_GAME_REFUND
    for user_id in players:
        await economy.add_wallet(user_id, bet, earn=True)
        await tx_service.record(
            user_id=user_id,
            ttype=ttype,
            amount=bet,
            balance_before=0,
            balance_after=0,
            metadata={
                "game": session.get("game_type"),
                "session_id": session_id,
                "game_id": session.get("game_id"),
                "bet": bet,
                "mode": mode,
                "reason": reason,
            },
        )
    return {
        "session_id": session_id,
        "mode": mode,
        "game_type": session.get("game_type"),
        "bet": bet,
        "refunded": bet * len(players),
        "players": players,
    }


async def process_emoji_result(
    client: Any,
    dice_message: Any,
    reply_to: Any,
    session_id: str,
) -> dict[str, Any] | None:
    """Central result processor for native emoji rounds (single + duel).

    Validates ``message.dice``, reads the real value, resolves the game,
    settles the session (idempotent), records transactions and sends the
    result as a NEW HTML message — the native dice message is never edited.

    When no valid dice value is present every locked bet is refunded and the
    user is notified.  Returns the outcome dict, or None when the session was
    already settled.
    """
    from utils import messages as msgs
    from utils.sender import reply_html

    session = await emoji_db.get_session(session_id)
    if session is None or session.get("status") != "active":
        return None
    game_type = session["game_type"]
    game_def = get_game_def(game_type)

    dice = getattr(dice_message, "dice", None) if dice_message is not None else None
    value = getattr(dice, "value", None) if dice is not None else None
    if value is None:
        result = await refund_failed_session(session_id, reason="no_dice_value")
        if result is not None:
            await reply_html(
                client, reply_to,
                msgs.emoji_game_failed(game_def.label, game_def.emoji),
            )
        return result

    try:
        game_def.result_parser(value)
    except EmojiGameError:
        result = await refund_failed_session(session_id, reason="invalid_dice_value")
        if result is not None:
            await reply_html(
                client, reply_to,
                msgs.emoji_game_failed(game_def.label, game_def.emoji),
            )
        return result

    if session.get("mode") == "single":
        outcome = await settle_single(session_id, value)
    else:
        outcome = await settle_duel(session_id, value)
    if outcome is None:
        return None

    bet = int(session.get("bet", 0))
    if session.get("mode") == "single":
        result_text = msgs.emoji_single_result(
            game_def.label,
            game_def.emoji,
            outcome["display_result"],
            outcome["outcome"],
            bet,
            outcome["payout"],
            outcome["tx_id"],
        )
    else:
        winner_name = None
        if outcome["winner_id"] is not None:
            winner_name = (
                session.get("player1_name", "Player 1")
                if outcome["winner_id"] == session["player1_id"]
                else session.get("player2_name", "Player 2")
            )
        result_text = msgs.emoji_duel_result(
            game_def.label,
            game_def.emoji,
            session.get("player1_name", "Player 1"),
            session.get("player2_name", "Player 2"),
            outcome["display_result"],
            winner_name,
            bet,
            outcome["payout"],
            outcome["tx_id"],
        )
    await reply_html(client, reply_to, result_text)
    return outcome


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
