"""Aviator crash game - slow, live, single-message inline game.

The plane takes off at 1.00x and the multiplier climbs a slow curve for a
random amount of time (``crash_time``, rolled once at session start and
independent of the player).  The player presses CASH OUT before the crash to
win ``bet * multiplier`` (unlimited by default; admin-adjustable via
``/aviatorset``).  If the plane crashes first the bet is lost.

All economy side-effects flow through the central game engine: atomic bet
lock (:func:`game_engine.check_and_lock_bet`), idempotent settlement
(:func:`game_engine.settle_game`), per-game cooldown, and system tax.  Money
is stored as integer sub-units only; multipliers are float purely for display
and payout calculation.

The only game-specific mechanism is a per-session background task that drives
the ~2-second Telegram message edits and crash detection.  A per-session
``asyncio.Lock`` serializes the two settlement paths (CASH OUT callback vs
the crash task) so a session can only ever be settled once.  Message edits are
UI-only: settlement is driven by wall-clock time and session status, so a
failed or FloodWait-blocked edit never affects the economy.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from database import games as games_db
from database.mongo import mongo
from services import game_engine
from services.game_engine import GameError, NoActiveGame
from utils.money import format_money
from utils.validators import is_safe_multiplier

logger = logging.getLogger(__name__)

CB_PREFIX = "aviator"

# Telegram UI cadence: one edit ~every 2 seconds (FloodWait-friendly).
EDIT_INTERVAL = 2.0

# The plane must fly at least this long before it can crash.
MIN_FLY_TIME = 1.0

# Default curve exponent: how slowly the multiplier climbs (higher = slower
# early growth, faster late growth).  Kept out of /aviatorset by design.
GROWTH_EXPONENT = 4.0

# Keeps live background tasks referenced (avoids GC, mirrors utils.sender).
_TASKS: set[asyncio.Task] = set()
# Per-session settlement locks (CASH OUT callback vs crash task).
_LOCKS: dict[str, asyncio.Lock] = {}

_EDIT_KWARGS = {"parse_mode": ParseMode.HTML}


# ---------------------------------------------------------------------------
# Pure game logic (no I/O, unit-testable)
# ---------------------------------------------------------------------------

def multiplier_at(elapsed: float, cfg: dict[str, Any]) -> float:
    """Multiplier after ``elapsed`` seconds on the slow growth curve.

    Bounded below by 1.00 and above by ``max_multiplier``; reaches
    ``max_multiplier`` exactly at ``duration`` seconds.
    """
    duration = max(1.0, float(cfg.get("duration", 60)))
    max_mult = max(1.0, float(cfg.get("max_multiplier", 100.0)))
    exponent = max(1.0, float(cfg.get("growth_exponent", GROWTH_EXPONENT)))
    t = min(max(float(elapsed), 0.0), duration)
    multiplier = 1.0 + (max_mult - 1.0) * (t / duration) ** exponent
    return round(multiplier, 2)


def roll_crash_time(cfg: dict[str, Any]) -> float:
    """Random crash moment in ``(MIN_FLY_TIME, duration]``.

    Independent of the player: takes only the game config (never user id,
    username, balance, bet, or history).
    """
    duration = max(1.0, float(cfg.get("duration", 60)))
    return random.uniform(MIN_FLY_TIME, duration)


def crash_multiplier_for(crash_time: float, cfg: dict[str, Any]) -> float:
    """The multiplier the plane will have reached at the crash moment."""
    return multiplier_at(crash_time, cfg)


def compute_payout(bet: int, multiplier: float, cfg: dict[str, Any]) -> int:
    """Integer payout for a cash-out. Capped only when ``max_payout > 0``."""
    if not is_safe_multiplier(multiplier):
        raise GameError("Invalid multiplier.")
    payout = int(bet * multiplier)
    max_payout = int(cfg.get("max_payout", 0))
    if max_payout > 0:
        payout = min(payout, max_payout)
    return max(0, payout)


def cashout_button(session_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("💰 CASH OUT", callback_data=f"{CB_PREFIX}:{session_id}:cash")]]
    )


def parse_callback(data: str) -> str | None:
    """Split ``aviator:SESSION_ID:cash`` -> session_id."""
    parts = data.split(":")
    if len(parts) == 3 and parts[0] == CB_PREFIX and parts[2] == "cash":
        return parts[1]
    return None


def live_text(bet: int, multiplier: float, session_id: str) -> str:
    return (
        f"<b>✈️ AVIATOR</b>\n"
        f"<blockquote>Bet: {format_money(bet)}\n"
        f"Multiplier: <b>{multiplier:.2f}x</b>\n"
        f"🧾 <code>#{session_id}</code></blockquote>\n"
        f"<i>Press 💰 CASH OUT before the plane crashes.</i>"
    )


def cashout_text(bet: int, multiplier: float, payout: int) -> str:
    profit = payout - bet
    return (
        f"<b>✈️ AVIATOR — 💰 CASH OUT</b>\n"
        f"<blockquote>💰 Cashed out at <b>{multiplier:.2f}x</b>\n"
        f"Bet: {format_money(bet)}\n"
        f"Payout: <b>{format_money(payout)}</b>\n"
        f"Profit: <b>{format_money(profit)}</b></blockquote>"
    )


def crash_text(bet: int, crash_multiplier: float) -> str:
    return (
        f"<b>✈️ AVIATOR — 💥 CRASHED</b>\n"
        f"<blockquote>💥 Crashed at <b>{crash_multiplier:.2f}x</b>\n"
        f"Bet: {format_money(bet)}\n"
        f"❌ Payout: {format_money(0)} — bet lost</blockquote>"
    )


def expired_text(bet: int) -> str:
    return (
        f"<b>✈️ AVIATOR — EXPIRED</b>\n"
        f"<blockquote>⏱️ Session timed out — settled as a loss.\n"
        f"❌ Lost: <b>{format_money(bet)}</b></blockquote>"
    )


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


def _state_cfg(state: dict[str, Any]) -> dict[str, Any]:
    return dict(
        state.get("cfg")
        or {"duration": 60, "max_multiplier": 100.0, "max_payout": 0, "growth_exponent": GROWTH_EXPONENT}
    )


def _lock_for(session_id: str) -> asyncio.Lock:
    return _LOCKS.setdefault(session_id, asyncio.Lock())


# ---------------------------------------------------------------------------
# Game lifecycle
# ---------------------------------------------------------------------------

async def settings() -> dict[str, Any]:
    return await game_engine.validate_game_input("aviator")


async def start(user_id: int, bet: int, *, chat_id: int | None = None) -> tuple[str, dict[str, Any]]:
    """Lock the bet and create an active aviator session. Returns (id, state)."""
    cfg = await settings()
    await game_engine.check_and_lock_bet(user_id, "aviator", bet)
    crash_time = roll_crash_time(cfg)
    state = {
        "crash_time": crash_time,
        "crash_multiplier": crash_multiplier_for(crash_time, cfg),
        "last_multiplier": 1.0,
        "cashout_multiplier": None,
        "payout": None,
        "tx_id": None,
        "cfg": {
            "duration": int(cfg.get("duration", 60)),
            "max_multiplier": float(cfg.get("max_multiplier", 100.0)),
            "max_payout": int(cfg.get("max_payout", 0)),
            "growth_exponent": float(cfg.get("growth_exponent", GROWTH_EXPONENT)),
        },
    }
    session_id = await game_engine.create_session(
        user_id, "aviator", bet, state,
        duration=int(cfg.get("duration", 60)),
        chat_id=chat_id,
    )
    return session_id, state


async def _patch_state(session_id: str, patch: dict[str, Any]) -> None:
    session = await games_db.get_session(session_id)
    if session is None:
        return
    state = dict(session.get("state", {}))
    state.update(patch)
    await mongo.db["game_sessions"].update_one(
        {"game_id": session_id}, {"$set": {"state": state}}
    )


# ---------------------------------------------------------------------------
# FloodWait-aware editing (UI only — never affects settlement)
# ---------------------------------------------------------------------------

async def _try_edit(
    client: Client, message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    try:
        await message.edit(text, reply_markup=reply_markup, **_EDIT_KWARGS)
    except FloodWait as exc:
        logger.warning("aviator edit floodwait (msg=%s): sleeping %ss", message.id, exc.value)
        await asyncio.sleep(exc.value)
    except Exception as exc:  # noqa: BLE001 - edits are best-effort UI
        logger.warning("aviator edit failed (id=%s): %s", message.id, exc)


# ---------------------------------------------------------------------------
# Settlement (exactly once per session)
# ---------------------------------------------------------------------------

async def _settle_crash(session_id: str, client: Client, message: Message) -> bool:
    """Crash settlement (loss). Idempotent; returns True when it settled."""
    async with _lock_for(session_id):
        return await _settle_crash_locked(session_id, client, message)


async def _settle_crash_locked(session_id: str, client: Client, message: Message) -> bool:
    """Crash settlement assuming the session lock is already held."""
    session = await games_db.get_session(session_id)
    if session is None or session.get("status") != "active":
        return False
    state = session.get("state", {})
    bet = int(session.get("bet", 0))
    crash_multiplier = float(state.get("crash_multiplier", 0.0))
    settled = await game_engine.settle_game(
        session_id,
        session["user_id"],
        won=False,
        payout=0,
        multiplier=crash_multiplier,
        meta={"aviator_outcome": "crashed", "crash_multiplier": crash_multiplier},
    )
    if settled:
        await _patch_state(session_id, {"last_multiplier": crash_multiplier})
        await _try_edit(client, message, crash_text(bet, crash_multiplier), reply_markup=None)
    return settled


async def cashout(
    session_id: str,
    user_id: int,
    client: Client,
    message: Message,
) -> dict[str, Any]:
    """CASH OUT callback: settle a win (or a crash) exactly once."""
    async with _lock_for(session_id):
        session = await games_db.get_session(session_id)
        validate_ownership(
            session, user_id, chat_id=message.chat.id, message_id=message.id
        )
        state = session.get("state", {})
        bet = int(session.get("bet", 0))
        cfg = _state_cfg(state)
        elapsed = time.time() - float(session.get("created_at", time.time()))
        crash_time = float(state.get("crash_time", 0.0))

        if elapsed >= crash_time:
            await _settle_crash_locked(session_id, client, message)
            return {
                "won": False, "crashed": True, "bet": bet, "payout": 0,
                "multiplier": float(state.get("crash_multiplier", 0.0)),
            }

        multiplier = multiplier_at(elapsed, cfg)
        payout = compute_payout(bet, multiplier, cfg)
        settled = await game_engine.settle_game(
            session_id,
            user_id,
            won=True,
            payout=payout,
            multiplier=multiplier,
            meta={"aviator_outcome": "cashed_out", "cashout_multiplier": multiplier},
        )
        if not settled:
            raise NoActiveGame("This game has already ended.")
        await _patch_state(
            session_id,
            {"last_multiplier": multiplier, "cashout_multiplier": multiplier, "payout": payout},
        )
        await _try_edit(client, message, cashout_text(bet, multiplier, payout), reply_markup=None)
        return {
            "won": True, "crashed": False, "bet": bet, "payout": payout,
            "multiplier": multiplier,
        }


# ---------------------------------------------------------------------------
# Background driver (message edits + crash detection)
# ---------------------------------------------------------------------------

async def run(session_id: str, client: Client, message: Message) -> None:
    """Live loop: ~2s edits, then crash-settle when the plane goes down.

    UI-only: settlement is driven by wall-clock time + session status, so a
    failed edit never affects the economy.  Stops as soon as the session is
    settled (by this loop, CASH OUT, or the scheduler cleanup job).
    """
    task = asyncio.current_task()
    try:
        while True:
            session = await games_db.get_session(session_id)
            if session is None or session.get("status") != "active":
                break
            state = session.get("state", {})
            created_at = float(session.get("created_at", time.time()))
            elapsed = time.time() - created_at
            crash_time = float(state.get("crash_time", 0.0))

            if elapsed >= crash_time:
                await _settle_crash(session_id, client, message)
                break

            cfg = _state_cfg(state)
            multiplier = multiplier_at(elapsed, cfg)
            bet = int(session.get("bet", 0))
            await _try_edit(
                client, message,
                live_text(bet, multiplier, session_id),
                reply_markup=cashout_button(session_id),
            )
            await asyncio.sleep(EDIT_INTERVAL)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("aviator background task crashed for %s", session_id)
    finally:
        _TASKS.discard(task)
        _LOCKS.pop(session_id, None)


def start_task(session_id: str, client: Client, message: Message) -> asyncio.Task:
    """Launch the background driver for a session (keeps a strong ref)."""
    task = asyncio.create_task(run(session_id, client, message))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task
