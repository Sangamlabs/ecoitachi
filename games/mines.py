"""Mines game - 6x6 inline-button board.

Board state lives in the Mongo game session (not Python memory), every callback
data is namespaced with the session id, and every callback verifies the caller
owns the game.  Multipliers follow the standard mathematically-fair mines
table unless an admin configures a custom table via /minestrap.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from database import games as games_db
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from services import game_engine
from services.game_engine import GameError, NoActiveGame
from utils.money import format_money

logger = logging.getLogger(__name__)

BOARD_SIZE = 6
TILES = BOARD_SIZE * BOARD_SIZE  # 36

SAFE = "✅"
MINE = "💣"
HIDDEN = "⬜"


def render_board(state: dict[str, Any], reveal_mines: bool = False) -> str:
    """Render the 6x6 board grid as text."""
    revealed = set(state.get("revealed", []))
    mines = set(state.get("mines", []))
    rows = []
    for row in range(BOARD_SIZE):
        cells = []
        for col in range(BOARD_SIZE):
            tile = row * BOARD_SIZE + col
            if reveal_mines and tile in mines:
                cells.append(MINE)
            elif tile in revealed:
                cells.append(SAFE)
            else:
                cells.append(HIDDEN)
        rows.append(" ".join(cells))
    return "\n".join(rows)


def board_keyboard(session_id: str, state: dict[str, Any]) -> InlineKeyboardMarkup:
    """Build the inline keyboard for the 6x6 board."""
    revealed = set(state.get("revealed", []))
    rows = []
    for row in range(BOARD_SIZE):
        buttons = []
        for col in range(BOARD_SIZE):
            tile = row * BOARD_SIZE + col
            label = SAFE if tile in revealed else HIDDEN
            buttons.append(
                InlineKeyboardButton(
                    label, callback_data=f"mines:{session_id}:{tile}"
                )
            )
        rows.append(buttons)
    rows.append(
        [InlineKeyboardButton("💰 Cash Out", callback_data=f"mines:{session_id}:cash")]
    )
    return InlineKeyboardMarkup(rows)


def game_header(bet: int, multiplier: float, payout: int, reveals: int) -> str:
    return (
        f"<b>💣 MINES</b>\n"
        f"<blockquote>"
        f"🎯 Bet: {format_money(bet)}\n"
        f"🪙 Revealed: {reveals}\n"
        f"📈 Multiplier: <b>{multiplier:.2f}x</b>\n"
        f"💰 Cash-out Value: <b>{format_money(payout)}</b>\n"
        f"<i>Reveal tiles, avoid mines, then cash out.</i>"
        f"</blockquote>"
    )


async def board_settings() -> dict[str, Any]:
    settings = await game_engine.validate_game_input("mines")
    bombs = int(settings.get("bomb_count", 5))
    bombs = max(1, min(bombs, TILES - 1))
    mode = settings.get("multipliers_mode", "auto")
    custom = settings.get("multipliers", [])
    if mode == "custom" and isinstance(custom, list) and len(custom) >= TILES - bombs:
        table = [max(1.0, float(x)) for x in custom]
    else:
        table = _auto_table(bombs)
    return {
        **settings,
        "bomb_count": bombs,
        "multipliers": table,
        "min_reveals": int(settings.get("min_reveals", 3)),
    }


def _auto_table(bombs: int) -> list[float]:
    """Standard fair mines multiplier per reveal for a 36-tile board.

    The multiplier grows with each safe reveal: 1/P(all reveals so far safe).
    """
    table: list[float] = []
    multiplier = 1.0
    safe = TILES - bombs
    for reveal in range(safe):
        multiplier *= (TILES - reveal) / (safe - reveal)
        table.append(round(multiplier, 4))
    return table


def _spawn_mines(bombs: int) -> set[int]:
    return set(random.sample(range(TILES), bombs))


def multiplier_after(reveals: int, table: list[float]) -> float:
    if reveals <= 0:
        return 1.0
    idx = min(reveals, len(table)) - 1
    return float(table[idx])


def payout_for(bet: int, reveals: int, table: list[float]) -> int:
    return int(bet * multiplier_after(reveals, table))


async def start(
    user_id: int, bet: int, *, chat_id: int | None = None, message_id: int | None = None
) -> tuple[str, dict[str, Any]]:
    """Start a mines game; returns (session_id, state)."""
    cfg = await board_settings()
    await game_engine.check_and_lock_bet(user_id, "mines", bet)
    state = {
        "mines": sorted(_spawn_mines(cfg["bomb_count"])),
        "revealed": [],
        "bomb_count": cfg["bomb_count"],
        "board_size": BOARD_SIZE,
        "reveals_so_far": 0,
    }
    session_id = await game_engine.create_session(
        user_id,
        "mines",
        bet,
        state,
        duration=cfg.get("duration"),
        chat_id=chat_id,
        message_id=message_id,
    )
    return session_id, state


def _mine_hit(tile: int, state: dict[str, Any]) -> bool:
    return tile in state.get("mines", [])


async def reveal(
    session_id: str, user_id: int, tile: int, *, chat_id: int | None = None, message_id: int | None = None
) -> dict[str, Any]:
    """Reveal a tile. Returns ``{game_over, won, payout, state, ...}``.

    On a mine the game is settled as a loss; on the last safe tile it is
    settled as a win.
    """
    session = await _owned_active_session(session_id, user_id, chat_id=chat_id, message_id=message_id)
    state = session.get("state", {})
    tile = int(tile)
    if not 0 <= tile < TILES:
        raise GameError("Invalid tile.")
    if tile in state.get("revealed", []):
        raise GameError("Tile already revealed.")
    revealed = list(state.get("revealed", [])) + [tile]
    state["revealed"] = revealed

    if _mine_hit(tile, state):
        await game_engine.settle_game(
            session_id, user_id, won=False, payout=0, meta={"tile": tile}
        )
        await game_engine.apply_cooldown("mines", user_id)
        return {"game_over": True, "won": False, "payout": 0, "state": state}

    bombs = int(state.get("bomb_count", 5))
    max_safe = TILES - bombs
    cfg = await board_settings()
    if len(revealed) >= max_safe:
        payout = payout_for(session.get("bet", 0), len(revealed), cfg["multipliers"])
        await game_engine.settle_game(
            session_id, user_id, won=True, payout=payout,
            multiplier=multiplier_after(len(revealed), cfg["multipliers"]),
            meta={"reveals": len(revealed)},
        )
        await game_engine.apply_cooldown("mines", user_id)
        return {"game_over": True, "won": True, "payout": payout, "state": state}

    await _update_state(session_id, state)
    return {
        "game_over": False,
        "won": None,
        "payout": payout_for(session.get("bet", 0), len(revealed), cfg["multipliers"]),
        "multiplier": multiplier_after(len(revealed), cfg["multipliers"]),
        "state": state,
    }


async def cashout(
    session_id: str, user_id: int, *, chat_id: int | None = None, message_id: int | None = None
) -> dict[str, Any]:
    """Cash out the current revealed position as a win."""
    session = await _owned_active_session(session_id, user_id, chat_id=chat_id, message_id=message_id)
    state = session.get("state", {})
    cfg = await board_settings()
    reveals = len(state.get("revealed", []))
    if reveals < int(cfg.get("min_reveals", 3)):
        raise GameError(f"Reveal at least {cfg.get('min_reveals', 3)} tiles before cashing out.")
    payout = payout_for(session.get("bet", 0), reveals, cfg["multipliers"])
    await game_engine.settle_game(
        session_id, user_id, won=True, payout=payout,
        multiplier=multiplier_after(reveals, cfg["multipliers"]),
        meta={"reveals": reveals, "cashout": True},
    )
    await game_engine.apply_cooldown("mines", user_id)
    return {"game_over": True, "won": True, "payout": payout, "state": state}


async def _update_state(session_id: str, state: dict[str, Any]) -> None:
    from database.mongo import mongo

    await mongo.db["game_sessions"].update_one(
        {"game_id": session_id}, {"$set": {"state": state}}
    )


async def _owned_active_session(
    session_id: str,
    user_id: int,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
) -> dict[str, Any]:
    session = await games_db.get_session(session_id)
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
    return session


async def has_active_game(user_id: int) -> bool:
    return await games_db.get_active_session(user_id, "mines") is not None
