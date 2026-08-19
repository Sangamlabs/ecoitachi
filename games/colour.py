"""Colour game - inline-button prediction game.

The player picks one SIZE (BIG/MEDIUM/SMALL), one COLOUR (GREEN/RED/VIOLET/
BLUE) and one NUMBER (0-9), then presses PLAY.  The result number is generated
after PLAY, independently of the picks.  The result's colour and size are
derived deterministically from the number.  A category matches when the
player's pick equals the result's value in that category; the payout scales
with the number of matched categories (0-3) and is bounded by configured
caps, so a win can never mint unlimited money.

Session state and settlement flow through the central game engine exactly
like mines (atomic bet lock, idempotent settle, per-game cooldown).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

from database import games as games_db
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from services import game_engine
from services.game_engine import GameError, NoActiveGame
from utils.money import format_money

logger = logging.getLogger(__name__)

CB_PREFIX = "colour"

SIZES = ("BIG", "MEDIUM", "SMALL")
COLOURS = ("GREEN", "RED", "VIOLET", "BLUE")
NUMBERS = tuple(str(n) for n in range(10))

SIZE_EMOJI = {"BIG": "⬆️", "MEDIUM": "➡️", "SMALL": "⬇️"}
COLOUR_EMOJI = {"GREEN": "🟢", "RED": "🔴", "VIOLET": "🟣", "BLUE": "🔵"}

# Deterministic number -> colour / size mapping (documented in help).
_COLOUR_OF = {
    "0": "VIOLET", "1": "RED", "2": "GREEN", "3": "RED", "4": "GREEN",
    "5": "RED", "6": "GREEN", "7": "RED", "8": "GREEN", "9": "BLUE",
}
_SIZE_OF = {
    "0": "SMALL", "1": "SMALL", "2": "SMALL", "3": "SMALL",
    "4": "MEDIUM", "5": "MEDIUM", "6": "MEDIUM",
    "7": "BIG", "8": "BIG", "9": "BIG",
}

DEFAULT_MULTIPLIERS = [0.0, 1.5, 4.0, 8.0]


def derive_colour(number: int) -> str:
    """Return the colour derived from a result number (0-9)."""
    return _COLOUR_OF[str(number)]


def derive_size(number: int) -> str:
    """Return the size derived from a result number (0-9)."""
    return _SIZE_OF[str(number)]


def roll_result() -> int:
    """Roll the independent result number (0-9)."""
    return random.randint(0, 9)


def match_count(selection: dict[str, Any], number: int) -> int:
    """Count matched categories (0..3) between the picks and the result."""
    count = 0
    if selection.get("size") == derive_size(number):
        count += 1
    if selection.get("colour") == derive_colour(number):
        count += 1
    if str(selection.get("number")) == str(number):
        count += 1
    return count


def multiplier_for(matches: int, cfg: dict[str, Any]) -> float:
    """Multiplier for the number of matched categories, capped at max."""
    table = [float(x) for x in cfg.get("match_multipliers", DEFAULT_MULTIPLIERS)]
    idx = min(matches, len(table) - 1)
    multiplier = max(0.0, table[idx])
    return min(multiplier, float(cfg.get("max_multiplier", 8.0)))


def payout_for(bet: int, matches: int, cfg: dict[str, Any]) -> int:
    """Validated payout for a settlement (int, non-negative, capped)."""
    multiplier = multiplier_for(matches, cfg)
    payout = int(bet * multiplier)
    max_payout = int(cfg.get("max_payout", 0))
    if max_payout > 0:
        payout = min(payout, max_payout)
    return max(0, payout)


def selections_complete(state: dict[str, Any]) -> bool:
    """True once a size, colour and number have all been picked."""
    return bool(state.get("size")) and bool(state.get("colour")) and state.get("number") is not None


def is_expired(session: dict[str, Any], now: int | None = None) -> bool:
    expires_at = session.get("expires_at")
    if expires_at is None:
        return False
    return (now if now is not None else int(time.time())) >= int(expires_at)


def validate_ownership(
    session: dict[str, Any] | None,
    user_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> None:
    """Guard callbacks: owner-only, active-only, same chat/message."""
    if session is None:
        raise NoActiveGame("Game session not found.")
    if session.get("user_id") != user_id:
        raise GameError("You cannot control another user's game.")
    if session.get("status") != "active":
        raise NoActiveGame("This game has already ended.")
    if chat_id is not None and session.get("chat_id") is not None and session["chat_id"] != chat_id:
        raise GameError("This game was started in another chat.")
    if message_id is not None and session.get("message_id") is not None and session["message_id"] != message_id:
        raise GameError("This game is no longer active in this message.")


async def owned_active_session(
    session_id: str,
    user_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> dict[str, Any]:
    session = await games_db.get_session(session_id)
    validate_ownership(session, user_id, chat_id=chat_id, message_id=message_id)
    return session


async def settings() -> dict[str, Any]:
    return await game_engine.validate_game_input("colour")


async def start(
    user_id: int, bet: int, *, chat_id: int | None = None, message_id: int | None = None
) -> tuple[str, dict[str, Any]]:
    """Start a colour session; returns (session_id, state)."""
    cfg = await settings()
    await game_engine.check_and_lock_bet(user_id, "colour", bet)
    state = {"size": None, "colour": None, "number": None}
    session_id = await game_engine.create_session(
        user_id,
        "colour",
        bet,
        state,
        duration=cfg.get("duration"),
        chat_id=chat_id,
        message_id=message_id,
    )
    return session_id, state


def selection_header(bet: int, state: dict[str, Any]) -> str:
    size = state.get("size") or "—"
    colour = state.get("colour") or "—"
    number = state.get("number") if state.get("number") is not None else "—"
    return (
        f"<b>🎨 COLOUR</b>\n"
        f"<blockquote>"
        f"🎯 Bet: {format_money(bet)}\n"
        f"📊 Size: <b>{size}</b> | Colour: <b>{colour}</b> | Number: <b>{number}</b>\n"
        f"<i>Select one of each, then press 🎯 PLAY.</i>"
        f"</blockquote>"
    )


def build_keyboard(session_id: str, state: dict[str, Any]) -> InlineKeyboardMarkup:
    """Build the selection keyboard (size / colour / number / play)."""
    selected_size = state.get("size")
    selected_colour = state.get("colour")
    selected_number = state.get("number")
    rows = [
        [
            InlineKeyboardButton(
                f"{SIZE_EMOJI[s]} {s} ✅" if selected_size == s else f"{SIZE_EMOJI[s]} {s}",
                callback_data=f"{CB_PREFIX}:{session_id}:size:{s}",
            )
            for s in SIZES
        ],
        [
            InlineKeyboardButton(
                f"{COLOUR_EMOJI[c]} {c} ✅" if selected_colour == c else f"{COLOUR_EMOJI[c]} {c}",
                callback_data=f"{CB_PREFIX}:{session_id}:col:{c}",
            )
            for c in COLOURS
        ],
        [
            InlineKeyboardButton(
                f"{n} ✅" if selected_number == n else n,
                callback_data=f"{CB_PREFIX}:{session_id}:num:{n}",
            )
            for n in NUMBERS[:5]
        ],
        [
            InlineKeyboardButton(
                f"{n} ✅" if selected_number == n else n,
                callback_data=f"{CB_PREFIX}:{session_id}:num:{n}",
            )
            for n in NUMBERS[5:]
        ],
    ]
    complete = selections_complete(state)
    play_label = "🎯 PLAY" if complete else "🎯 PLAY (select all)"
    rows.append([InlineKeyboardButton(play_label, callback_data=f"{CB_PREFIX}:{session_id}:play")])
    return InlineKeyboardMarkup(rows)


def parse_callback(data: str) -> tuple[str, str, str] | None:
    """Split ``colour:SESSION_ID:CATEGORY:VALUE`` / ``colour:SESSION_ID:play``."""
    parts = data.split(":")
    if parts[0] != CB_PREFIX:
        return None
    if len(parts) == 3 and parts[2] == "play":
        return parts[1], "play", ""
    if (
        len(parts) == 4
        and parts[2] in ("size", "col", "num")
        and (parts[2] != "size" or parts[3] in SIZES)
        and (parts[2] != "col" or parts[3] in COLOURS)
        and (parts[2] != "num" or parts[3] in NUMBERS)
    ):
        return parts[1], parts[2], parts[3]
    return None


async def _settle_expired(session_id: str, user_id: int) -> bool:
    """Settle an expired session as a loss. Idempotent. Returns True if settled."""
    try:
        settled = await game_engine.settle_game(
            session_id, user_id, won=False, payout=0, meta={"reason": "timeout"}
        )
    except game_engine.NoActiveGame:
        return False
    return settled


async def select(
    session_id: str,
    user_id: int,
    category: str,
    value: str,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> dict[str, Any]:
    """Update one category selection. Expired sessions settle as a loss."""
    session = await owned_active_session(session_id, user_id, chat_id=chat_id, message_id=message_id)
    if is_expired(session):
        await _settle_expired(session_id, user_id)
        return {
            "game_over": True, "won": False, "expired": True,
            "bet": int(session.get("bet", 0)), "state": session.get("state", {}),
        }
    state = dict(session.get("state", {}))
    if category == "size" and value in SIZES:
        state["size"] = value
    elif category == "col" and value in COLOURS:
        state["colour"] = value
    elif category == "num" and value in NUMBERS:
        state["number"] = value
    else:
        raise GameError("Invalid selection.")
    await _update_state(session_id, state)
    return {
        "game_over": False, "expired": False,
        "bet": int(session.get("bet", 0)), "state": state,
    }


async def play(
    session_id: str,
    user_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> dict[str, Any]:
    """Roll the result and settle the game exactly once."""
    session = await owned_active_session(session_id, user_id, chat_id=chat_id, message_id=message_id)
    state = session.get("state", {})
    bet = int(session.get("bet", 0))
    if is_expired(session):
        await _settle_expired(session_id, user_id)
        return {
            "game_over": True, "won": False, "expired": True,
            "bet": bet, "payout": 0, "number": None, "colour": None,
            "size": None, "matches": 0, "multiplier": 0.0, "state": state,
        }
    if not selections_complete(state):
        raise GameError("Select a Size, Colour and Number first.")

    number = roll_result()
    matches = match_count(state, number)
    cfg = await settings()
    won = matches > 0
    payout = payout_for(bet, matches, cfg) if won else 0
    multiplier = multiplier_for(matches, cfg) if won else 0.0
    await game_engine.settle_game(
        session_id,
        user_id,
        won=won,
        payout=payout,
        multiplier=multiplier,
        meta={"number": number, "matches": matches},
    )
    await game_engine.apply_cooldown("colour", user_id)
    return {
        "game_over": True, "won": won, "expired": False, "bet": bet,
        "payout": payout, "number": number, "colour": derive_colour(number),
        "size": derive_size(number), "matches": matches,
        "multiplier": multiplier, "state": state,
    }


async def _update_state(session_id: str, state: dict[str, Any]) -> None:
    from database.mongo import mongo

    await mongo.db["game_sessions"].update_one(
        {"game_id": session_id}, {"$set": {"state": state}}
    )