"""Admin handlers - owner & sudo economy administration.

Permission gates come from :mod:`utils.permissions` (numeric IDs only).
Every admin money action produces an audit transaction.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import Message

from config import config
from database import security as sec_db, users as users_db, admins as admins_db
from database import stocks as stocks_db
from database.mongo import mongo
from handlers.common import ensure_user, safe_handler
from services import bank as bank_service, economy, settings as settings_service
from services import identity as identity_service
from services import tax as tax_service
from services import transaction as tx_service
from services import security as security_service
from services import group_config as group_config_service
from services import transaction as tx_service2
from services.economy import EconomyError, BannedUser, FrozenUser, InsufficientBalance
from services.security import (
    global_ban_check, global_ban, global_unban,
    create_security_case, list_security_cases,
    create_security_dump, list_security_dumps,
    restore_from_dump, manual_restore, manual_restorecase,
    manual_dump_user, quarantine_check, quarantine_user,
    clear_quarantine, check_sudo_security,
    handle_secret_detection
)
from handlers.promo_admin import msgs as promo_msgs
from utils import messages as msgs
from utils.chat import chat_type
from utils.money import format_money
from utils.permissions import is_owner as utils_is_owner, owner_only, security_sudo_or_owner, sudo_only
from utils.sender import reply_html
from utils.validators import (
    is_safe_multiplier,
    is_safe_percent,
    is_safe_probability,
    parse_amount_or_error,
    target_from_message,
    validate_crash_value,
    validate_min_max,
)

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot

PM2_BIN = "/usr/bin/pm2"


async def _restart_bot() -> None:
    """Fully restart the bot, reloading every module.

    Primary path: ask pm2 to restart this app (kills this process, pm2 boots a
    fresh one).  Fallback for non-pm2 runs: re-exec ``bot.py`` in place so all
    modules are imported again.  The admin is notified after startup in
    ``bot.py``.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            PM2_BIN, "restart", config.PM2_APP_NAME,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        if rc == 0:
            # pm2 will SIGINT this process; force-exit as a safety net.
            await asyncio.sleep(2)
            os._exit(0)
        logger.warning("pm2 restart returned code %s; falling back to exec", rc)
    except Exception as exc:
        logger.exception("pm2 restart failed (%s); falling back to exec", exc)

    try:
        bot_path = str(Path(__file__).resolve().parent.parent / "bot.py")
        os.execv(sys.executable, [sys.executable, bot_path])
    except Exception as exc:
        logger.exception("self-restart exec failed: %s", exc)
        os._exit(1)

FLY_FIELDS = {
    "min_mult": "minimum_multiplier",
    "max_mult": "maximum_multiplier",
    "risk": "risk",
    "win_prob": "win_probability",
    "cooldown": "cooldown",
    "min_bet": "minimum_bet",
    "max_bet": "maximum_bet",
}

FIELD_PARSERS = {
    "min_mult": float,
    "max_mult": float,
    "risk": float,
    "win_prob": float,
    "cooldown": int,
    "min_bet": int,
    "max_bet": int,
}


TARGET_USAGE = "User not found. Provide a Telegram ID, @username, UNOITACHI UID, or reply to the user."


async def _need_target_or_error(client: Client, message: Message, arg: str | None) -> int | None:
    """Resolve a target through the central identity resolver and return its id.

    Unknown users are auto-registered (no ``/start`` required).  Returns
    ``None`` after replying with a clear usage error when unresolvable.
    """
    doc = await identity_service.resolve_user(client, message, arg, create=True)
    if doc is None:
        await reply_html(client, message, msgs.error(TARGET_USAGE))
        return None
    return doc["user_id"]


def _validate_fly_settings(difficulty: str, settings: dict) -> str | None:
    """Return an error string if the proposed fly config is invalid."""
    for key in ("min_mult", "max_mult", "risk", "win_prob", "cooldown", "min_bet", "max_bet"):
        if key not in settings:
            continue
        value = settings[key]
        if key in ("min_mult", "max_mult"):
            if not (0 < value <= 1000):
                return "Multipliers must be finite and between 0 and 1000."
        elif key == "risk":
            if not (0 <= value <= 100):
                return "Risk must be a percentage between 0 and 100."
        elif key == "win_prob":
            if not (0 <= value <= 1):
                return "Win probability must be between 0 and 1."
        elif value < 0:
            return f"{key} cannot be negative."
    if "min_mult" in settings and "max_mult" in settings:
        if not (settings["min_mult"] <= settings["max_mult"]):
            return "minimum multiplier cannot exceed maximum multiplier."
    if "min_bet" in settings and "max_bet" in settings:
        if not (settings["min_bet"] <= settings["max_bet"]):
            return "minimum bet cannot exceed maximum bet."
    return None


def register(app: Client) -> None:
    # ---------------- OWNER ONLY ----------------
    @app.on_message(filters.command("addsudo") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_addsudo(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            await reply_html(client, message, msgs.error("Usage: <code>/addsudo @user</code> or reply."))
            return
        from database import admins as admins_db

        await admins_db.add_sudo(target, message.from_user.id)
        await reply_html(client, message, msgs.success(f"Added <code>{target}</code> as sudo admin."))

    @app.on_message(filters.command("rsudo") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_rsudo(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        if await utils_is_owner(target):
            await reply_html(client, message, msgs.error("You cannot remove the owner."))
            return
        removed = await admins_db.remove_sudo(target)
        if removed:
            await reply_html(client, message, msgs.success(f"Removed <code>{target}</code> from sudo admins."))
        else:
            await reply_html(client, message, msgs.warning("That user is not a sudo admin."))

    # ---------------- SUDO / ADMIN ----------------
    @app.on_message(filters.command("adminhelp") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_adminhelp(client: Client, message: Message):
        await reply_html(client, message, msgs.admin_help())

    @app.on_message(filters.command("give") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_give(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        reply_target = target_from_message(message)
        target = await _need_target_or_error(
            client, message, None if reply_target is not None else (args[0] if args else None)
        )
        if target is None:
            return
        amount, err = parse_amount_or_error(args[1] if not reply_target and len(args) > 1 else (args[0] if reply_target else ""))
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/give @user amount</code> or reply + <code>/give amount</code>. {err}"))
            return
        actor = message.from_user.id
        await economy.admin_give(target, amount, actor)
        before = await economy.get_balance(target)
        tx_id = await tx_service2.record(
            user_id=target,
            ttype=tx_service2.ADMIN_GIVE,
            amount=amount,
            balance_before=before["wallet"],
            balance_after=before["wallet"] + amount,
            metadata={"actor": actor},
        )
        await reply_html(
            client, message,
            msgs.success(f"Gave {format_money(amount)} to <code>{target}</code>.\n🧾 <code>#{tx_id}</code>"),
        )

    @app.on_message(filters.command("remove") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_remove(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        reply_target = target_from_message(message)
        target = await _need_target_or_error(
            client, message, None if reply_target is not None else (args[0] if args else None)
        )
        if target is None:
            return
        amount_raw = (
            args[0] if reply_target else (args[1] if len(args) > 1 else "")
        )
        amount, err = parse_amount_or_error(amount_raw)
        if err:
            await reply_html(
                client, message,
                msgs.error(
                    f"Usage: <code>/remove USER amount</code> "
                    f"(USER = Telegram ID, @username or UID), or reply to a user with "
                    f"<code>/remove amount</code>. {err}"
                ),
            )
            return
        actor = message.from_user.id
        before = await economy.get_balance(target)
        try:
            await economy.admin_remove(target, amount, actor)
        except EconomyError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        tx_id = await tx_service2.record(
            user_id=target,
            ttype=tx_service2.ADMIN_REMOVE,
            amount=amount,
            balance_before=before["wallet"],
            balance_after=before["wallet"] - amount,
            metadata={"actor": actor},
        )
        await reply_html(
            client, message,
            msgs.success(f"Removed {format_money(amount)} from <code>{target}</code>.\n🧾 <code>#{tx_id}</code>"),
        )

    @app.on_message(filters.command("getcoin") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_getcoin(client: Client, message: Message):
        await ensure_user(client, message)
        amount, err = parse_amount_or_error(message.command[1] if len(message.command) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/getcoin amount</code>. {err}"))
            return
        actor = message.from_user.id
        if amount > config.GETCOIN_MAX_SUBUNITS:
            await reply_html(
                client,
                message,
                msgs.error(
                    f"Maximum /getcoin amount is {format_money(config.GETCOIN_MAX_SUBUNITS)}."
                ),
            )
            return
        await economy.admin_give(actor, amount, actor)
        before = await economy.get_balance(actor)
        tx_id = await tx_service2.record(
            user_id=actor,
            ttype=tx_service2.ADMIN_GIVE,
            amount=amount,
            balance_before=before["wallet"],
            balance_after=before["wallet"] + amount,
            metadata={"actor": actor, "self": True},
        )
        await reply_html(
            client, message,
            msgs.success(f"You received <b>{format_money(amount)}</b>.\n🧾 <code>#{tx_id}</code>"),
        )

    @app.on_message(filters.command("setinterest") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_setinterest(client: Client, message: Message):
        await ensure_user(client, message)
        raw = message.command[1] if len(message.command) > 1 else None
        if raw is None:
            await reply_html(client, message, msgs.error("Usage: <code>/setinterest rate</code> (percent per 24h)."))
            return
        try:
            rate = float(raw)
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid interest rate."))
            return
        if not is_safe_percent(rate):
            await reply_html(client, message, msgs.error("Interest rate must be between 0 and 100."))
            return
        await bank_service.set_interest_rate(rate, message.from_user.id)
        await reply_html(client, message, msgs.success(f"Bank interest rate set to <b>{rate}%</b> per 24h."))

    @app.on_message(filters.command("settax") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_settax(client: Client, message: Message):
        await ensure_user(client, message)
        raw = message.command[1] if len(message.command) > 1 else None
        if raw is None:
            await reply_html(client, message, msgs.error("Usage: <code>/settax rate</code> (percent)."))
            return
        try:
            rate = float(raw)
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid tax rate."))
            return
        if not is_safe_percent(rate):
            await reply_html(client, message, msgs.error("Tax rate must be between 0 and 100."))
            return
        await bank_service.set_tax_rate(rate, message.from_user.id)
        await reply_html(client, message, msgs.success(f"Withdrawal tax set to <b>{rate}%</b>."))

    @app.on_message(filters.command("setincome") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_setincome(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) != 2 or args[0].lower() not in ("bank", "asset", "stock"):
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/setincome bank|asset|stock rate</code> (percent per 24h)."),
            )
            return
        try:
            rate = float(args[1])
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid rate."))
            return
        if not is_safe_percent(rate):
            await reply_html(client, message, msgs.error("Rate must be between 0 and 100."))
            return
        field = {
            "bank": "bank_rate_percent",
            "asset": "asset_rate_percent",
            "stock": "stock_rate_percent",
        }[args[0].lower()]
        await settings_service.update_income_config(**{field: rate})
        await reply_html(
            client, message,
            msgs.success(
                f"Daily income rate for <code>{args[0].lower()}</code> set to <b>{rate}%</b> per 24h."
            ),
        )

    @app.on_message(filters.command("dtax") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_dtax(client: Client, message: Message):
        await ensure_user(client, message)
        result = await tax_service.distribute_manual()
        if result is None:
            await reply_html(client, message, msgs.error("Tax distribution could not run (check distribution settings)."))
            return
        if result["distributed"] <= 0:
            await reply_html(
                client, message,
                msgs.warning(f"No tax to distribute. Pool: {format_money(result['pool'])}"),
            )
            return
        await reply_html(client, message, msgs.tax_distribution(result))

    @app.on_message(filters.command("track") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_track(client: Client, message: Message):
        await ensure_user(client, message)
        raw = message.command[1] if len(message.command) > 1 else None
        if not raw:
            await reply_html(client, message, msgs.error("Usage: <code>/track TRANSACTION_ID</code>"))
            return
        tx_id = raw.strip()
        doc = await tx_service2.get_by_id(tx_id)
        if doc is None:
            await reply_html(client, message, msgs.error(f"Transaction <code>#{tx_id}</code> not found."))
            return
        await reply_html(client, message, msgs.tx_track_detail(doc))

    @app.on_message(filters.command("addtax") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_addtax(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        valid = ("bank", "assets", "stocks", "payments", "mines", "fly", "bet")
        if len(args) != 2 or args[0].lower() not in valid:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/addtax system rate</code> (percent). "
                    "Systems: " + ", ".join(valid) + "."
                ),
            )
            return
        system, raw_rate = args[0].lower(), args[1]
        try:
            rate = float(raw_rate)
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid rate."))
            return
        if not is_safe_percent(rate):
            await reply_html(client, message, msgs.error("Rate must be between 0 and 100."))
            return
        if system == "bank":
            await bank_service.set_tax_rate(rate, message.from_user.id)
        else:
            await settings_service.update_system_taxes(**{system: rate})
        await reply_html(
            client, message,
            msgs.success(f"Tax on <code>{system}</code> transactions set to <b>{rate}%</b>."),
        )

    @app.on_message(filters.command("taxinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_taxinfo(client: Client, message: Message):
        await ensure_user(client, message)
        pool = await tax_service.get_pool_size()
        taxes = await settings_service.get_system_taxes()
        bank_settings = await bank_service.get_bank_settings()
        await reply_html(client, message, msgs.taxinfo(taxes, pool, bank_settings))

    @app.on_message(filters.command("banksettings") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_banksettings(client: Client, message: Message):
        await ensure_user(client, message)
        settings = await bank_service.get_bank_settings()
        pool = await tax_service.get_pool_size()
        await reply_html(client, message, msgs.banksettings(settings, pool))

    # ---------------- FLY GAME ----------------
    @app.on_message(filters.command("flyset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_flyset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 3 or args[0].lower() not in ("low", "medium", "high"):
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/flyset low|medium|high field value</code>"),
            )
            return
        difficulty, field, raw_value = args[0].lower(), args[1].lower(), args[2]
        if field not in FLY_FIELDS:
            await reply_html(client, message, msgs.error(f"Unknown field. Valid: {', '.join(FLY_FIELDS)}"))
            return
        try:
            parsed = FIELD_PARSERS[field](raw_value)
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid value for field."))
            return
        err = _validate_fly_settings(difficulty, {field: parsed})
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        current = await settings_service.get_game_settings("fly")
        fly_cfg = dict(current[difficulty])
        fly_cfg[FLY_FIELDS[field]] = parsed
        await settings_service.update_game_settings("fly", **{difficulty: fly_cfg})
        await reply_html(
            client, message,
            msgs.success(f"Fly <code>{difficulty}</code> <code>{field}</code> set to {parsed}."),
        )

    @app.on_message(filters.command("flytrap") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_flytrap(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 8 or args[0].lower() not in ("low", "medium", "high"):
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/flytrap difficulty min_mult max_mult risk win_prob cooldown min_bet max_bet</code>"
                ),
            )
            return
        difficulty = args[0].lower()
        try:
            values = {
                "min_mult": float(args[1]),
                "max_mult": float(args[2]),
                "risk": float(args[3]),
                "win_prob": float(args[4]),
                "cooldown": int(args[5]),
                "min_bet": int(args[6]),
                "max_bet": int(args[7]),
            }
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid numeric value."))
            return
        err = _validate_fly_settings(difficulty, values)
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        current = await settings_service.get_game_settings("fly")
        fly_cfg = dict(current[difficulty])
        fly_cfg.update(
            {
                FLY_FIELDS[k]: v
                for k, v in values.items()
            }
        )
        await settings_service.update_game_settings("fly", **{difficulty: fly_cfg})
        await reply_html(client, message, msgs.success(f"Fly <code>{difficulty}</code> settings updated."))

    # ---------------- BET GAME ----------------
    @app.on_message(filters.command("betset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_betset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 4:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/betset win_prob multiplier min_bet max_bet [cooldown]</code>"),
            )
            return
        try:
            win_prob, multiplier = float(args[0]), float(args[1])
            min_bet, max_bet = int(args[2]), int(args[3])
            cooldown = int(args[4]) if len(args) > 4 else None
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid numeric value."))
            return
        if not is_safe_probability(win_prob):
            await reply_html(client, message, msgs.error("Win probability must be between 0 and 1."))
            return
        if not is_safe_multiplier(multiplier):
            await reply_html(client, message, msgs.error("Multiplier must be between 0 and 1000."))
            return
        if not validate_min_max(min_bet, max_bet):
            await reply_html(client, message, msgs.error("Minimum bet cannot exceed maximum bet."))
            return
        changes = {
            "win_probability": win_prob,
            "multiplier": multiplier,
            "minimum_bet": min_bet,
            "maximum_bet": max_bet,
        }
        if cooldown is not None:
            changes["cooldown"] = max(0, cooldown)
        await settings_service.update_game_settings("bet", **changes)
        await reply_html(client, message, msgs.success("Bet game settings updated."))

    # ---------------- COLOUR GAME ----------------
    @app.on_message(filters.command("colourset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_colourset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/colourset field value [value...]</code>\n"
                    "Fields: min_bet | max_bet | cooldown | duration | "
                    "multipliers 0 1.5 4 8 | max_multiplier | max_payout\n"
                    "Example: <code>/colourset multipliers 0 1.5 4 8</code>"
                ),
            )
            return
        field = args[0].lower()
        values = args[1:]
        current = await settings_service.get_game_settings("colour")
        try:
            if field == "multipliers":
                table = [float(x) for x in values]
                if len(table) < 2 or any(not is_safe_multiplier(x) for x in table):
                    await reply_html(client, message, msgs.error("Provide at least 2 finite multipliers between 0 and 1000."))
                    return
                next_settings = dict(current)
                next_settings["match_multipliers"] = table
            elif field in ("min_bet", "max_bet", "cooldown", "duration", "max_payout"):
                value = int(float(values[0]))
                if value < 0:
                    await reply_html(client, message, msgs.error("Value must be >= 0."))
                    return
                next_settings = dict(current)
                key = (
                    "minimum_bet" if field == "min_bet"
                    else ("maximum_bet" if field == "max_bet" else field)
                )
                next_settings[key] = value
            elif field == "max_multiplier":
                multiplier = float(values[0])
                if not is_safe_multiplier(multiplier):
                    await reply_html(client, message, msgs.error("Multiplier must be between 0 and 1000."))
                    return
                next_settings = dict(current)
                next_settings["max_multiplier"] = multiplier
            else:
                await reply_html(client, message, msgs.error("Unknown field. See /colourset usage."))
                return
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid numeric value."))
            return
        if not validate_min_max(
            int(next_settings.get("minimum_bet", 0)),
            int(next_settings.get("maximum_bet", 0)),
        ):
            await reply_html(client, message, msgs.error("Minimum bet cannot exceed maximum bet."))
            return
        if int(next_settings.get("duration", 0)) < 1:
            await reply_html(client, message, msgs.error("duration must be at least 1 second."))
            return
        if int(next_settings.get("max_payout", 0)) <= 0:
            await reply_html(client, message, msgs.error("max_payout must be greater than 0."))
            return
        changes = {k: v for k, v in next_settings.items() if current.get(k) != v}
        if not changes:
            await reply_html(client, message, msgs.success("Colour game settings unchanged."))
            return
        await settings_service.update_game_settings("colour", **changes)
        await reply_html(client, message, msgs.success("Colour game settings updated."))

    # ---------------- AVIATOR GAME ----------------
    @app.on_message(filters.command("aviatorset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_aviatorset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/aviatorset field value</code>\n"
                    "Fields: min_bet | max_bet (0=unlimited) | cooldown | "
                    "duration | max_multiplier | max_payout (0=unlimited) | crash_value\n"
                    "crash_value is the upper crash limit (>= 1.00x, <= max_multiplier).\n"
                    "By default max_bet and max_payout are 0 (unlimited)."
                ),
            )
            return
        field = args[0].lower()
        current = await settings_service.get_game_settings("aviator")
        next_settings = dict(current)
        try:
            if field in ("min_bet", "max_bet", "cooldown", "duration", "max_payout"):
                value = int(float(args[1]))
                key = (
                    "minimum_bet" if field == "min_bet"
                    else ("maximum_bet" if field == "max_bet" else field)
                )
                if field == "min_bet" and value < 1:
                    await reply_html(client, message, msgs.error("min_bet must be at least 1."))
                    return
                if field == "duration" and value < 2:
                    await reply_html(client, message, msgs.error("duration must be at least 2 seconds."))
                    return
                if value < 0:
                    await reply_html(client, message, msgs.error("Value must be >= 0 (0 = unlimited for max_bet / max_payout)."))
                    return
                next_settings[key] = value
            elif field == "max_multiplier":
                multiplier = float(args[1])
                if not is_safe_multiplier(multiplier) or multiplier < 1.0:
                    await reply_html(client, message, msgs.error("Multiplier must be between 1 and 1000."))
                    return
                crash_value = float(next_settings.get("crash_value", 0.0))
                if crash_value > 0 and multiplier < crash_value:
                    await reply_html(
                        client, message,
                        msgs.error(
                            f"max_multiplier cannot be lower than crash_value "
                            f"({crash_value:g}x). Lower crash_value first."
                        ),
                    )
                    return
                next_settings["max_multiplier"] = multiplier
            elif field == "crash_value":
                crash_value, err = validate_crash_value(
                    args[1], float(next_settings.get("max_multiplier", 100.0))
                )
                if err:
                    await reply_html(client, message, msgs.error(err))
                    return
                next_settings["crash_value"] = crash_value
            else:
                await reply_html(client, message, msgs.error("Unknown field. See /aviatorset usage."))
                return
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid numeric value."))
            return
        max_bet = int(next_settings.get("maximum_bet", 0))
        if max_bet > 0 and int(next_settings.get("minimum_bet", 0)) > max_bet:
            await reply_html(client, message, msgs.error("Minimum bet cannot exceed maximum bet."))
            return
        changes = {k: v for k, v in next_settings.items() if current.get(k) != v}
        if not changes:
            await reply_html(client, message, msgs.success("Aviator game settings unchanged."))
            return
        await settings_service.update_game_settings("aviator", **changes)
        await reply_html(client, message, msgs.success("Aviator game settings updated."))

    # ---------------- MINES GAME ----------------
    @app.on_message(filters.command("minestrap") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_minestrap(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if not args:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/minestrap bombs min_reveals min_bet max_bet cooldown duration</code> "
                    "or <code>/minestrap multipliers 1.0,1.2,...</code> / <code>auto</code>"
                ),
            )
            return
        if args[0] == "multipliers":
            raw = args[1] if len(args) > 1 else None
            if raw is None:
                await reply_html(client, message, msgs.error("Provide a comma-separated table or <code>auto</code>."))
                return
            if raw.lower() == "auto":
                await settings_service.update_game_settings("mines", multipliers_mode="auto", multipliers=[])
                await reply_html(client, message, msgs.success("Mines multipliers reset to auto table."))
                return
            try:
                table = [float(x) for x in raw.split(",")]
            except ValueError:
                await reply_html(client, message, msgs.error("Invalid multiplier table."))
                return
            if any(not is_safe_multiplier(x) for x in table) or not table:
                await reply_html(client, message, msgs.error("Multipliers must be finite and positive."))
                return
            await settings_service.update_game_settings(
                "mines", multipliers_mode="custom", multipliers=table
            )
            await reply_html(client, message, msgs.success(f"Mines custom multiplier table saved ({len(table)} entries)."))
            return
        if len(args) < 6:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/minestrap bombs min_reveals min_bet max_bet cooldown duration</code>"),
            )
            return
        try:
            bombs, min_reveals, min_bet, max_bet, cooldown, duration = map(int, args[:6])
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid numeric value."))
            return
        if bombs < 1 or bombs > 34:
            await reply_html(client, message, msgs.error("Bombs must be between 1 and 34."))
            return
        if min_reveals < 1 or min_reveals > 36 - bombs:
            await reply_html(client, message, msgs.error(
                f"min_reveals must be between 1 and {36 - bombs} (36 - bombs)."
            ))
            return
        if not validate_min_max(min_bet, max_bet):
            await reply_html(client, message, msgs.error("Minimum bet cannot exceed maximum bet."))
            return
        await settings_service.update_game_settings(
            "mines",
            bomb_count=bombs,
            min_reveals=min_reveals,
            minimum_bet=min_bet,
            maximum_bet=max_bet,
            cooldown=max(0, cooldown),
            duration=max(30, duration),
        )
        await reply_html(client, message, msgs.success("Mines settings updated."))

    # ---------------- REWARDS ----------------
    @app.on_message(filters.command("setreward") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_setreward(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2 or args[0].lower() not in ("daily", "weekly", "monthly"):
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/setreward daily|weekly|monthly amount</code>"),
            )
            return
        kind = args[0].lower()
        amount, err = parse_amount_or_error(args[1])
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        current = await settings_service.get_rewards()
        entry = dict(current.get(kind, {}))
        entry["amount"] = amount
        await settings_service.update_rewards(**{kind: entry})
        await reply_html(
            client, message,
            msgs.success(f"{kind.title()} reward set to <code>{format_money(amount)}</code>."),
        )

    # ---------------- ROB ----------------
    ROB_FIELDS = {
        "win_prob": ("success_probability", float),
        "percent": ("bank_percentage", float),
        "min": ("minimum_amount", int),
        "max": ("maximum_amount", int),
        "cooldown": ("cooldown", int),
    }

    @app.on_message(filters.command("robset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_robset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2 or args[0].lower() not in ROB_FIELDS:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/robset win_prob|percent|min|max|cooldown value</code>"),
            )
            return
        field, raw = args[0].lower(), args[1]
        key, parser = ROB_FIELDS[field]
        try:
            value = parser(raw)
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid numeric value."))
            return
        if field == "win_prob" and not is_safe_probability(value):
            await reply_html(client, message, msgs.error("Win probability must be between 0 and 1."))
            return
        if field == "percent" and not is_safe_percent(value):
            await reply_html(client, message, msgs.error("Percent must be between 0 and 100."))
            return
        if value < 0:
            await reply_html(client, message, msgs.error("Value cannot be negative."))
            return
        current = await settings_service.get_game_settings("rob")
        current[key] = value
        if not validate_min_max(int(current.get("minimum_amount", 0)), int(current.get("maximum_amount", 0))):
            await reply_html(client, message, msgs.error("Minimum amount cannot exceed maximum amount."))
            return
        await settings_service.update_game_settings("rob", **current)
        await reply_html(client, message, msgs.success(f"Rob <code>{field}</code> set to {value}."))

    # ---------------- FREEZE / UNFREEZE ----------------
    @app.on_message(filters.command("freeze") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_freeze(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None or target == 6356015122:
            if target == 6356015122:
                await reply_html(client, message, msgs.error("Owner cannot be frozen."))
            return
        await users_db.set_user_flags(target, is_frozen=True)
        await reply_html(client, message, msgs.success(f"Frozen <code>{target}</code>."))

    @app.on_message(filters.command("unfreeze") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_unfreeze(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        await users_db.set_user_flags(target, is_frozen=False)
        await reply_html(client, message, msgs.success(f"Unfrozen <code>{target}</code>."))

    # ---------------- BAN / UNBAN (local economy ban) ----------------
    @app.on_message(filters.command("ban") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_ban(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        if target == message.from_user.id:
            await reply_html(client, message, msgs.error("You cannot ban yourself."))
            return
        if target == 6356015122:
            await reply_html(client, message, msgs.error("You cannot ban the owner."))
            return
        await users_db.set_user_flags(target, is_banned=True)
        await reply_html(client, message, msgs.success(f"Banned <code>{target}</code>."))

    @app.on_message(filters.command("unban") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_unban(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        await users_db.set_user_flags(target, is_banned=False)
        await reply_html(client, message, msgs.success(f"Unbanned <code>{target}</code>."))

    # ---------------- LEADERBOARD EXCLUSION (visibility only) ----------------
    @app.on_message(filters.command("leaderban") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_leaderban(client: Client, message: Message):
        """Hide a user from every leaderboard (no Telegram/economy ban)."""
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        if await utils_is_owner(target):
            await reply_html(client, message, msgs.error("You cannot exclude the owner from the leaderboard."))
            return
        await users_db.set_user_flags(target, leaderboard_excluded=True)
        await reply_html(client, message, msgs.success(f"<code>{target}</code> is now hidden from all leaderboards."))

    @app.on_message(filters.command("leaderunban") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_leaderunban(client: Client, message: Message):
        """Restore a user's visibility on every leaderboard."""
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        await users_db.set_user_flags(target, leaderboard_excluded=False)
        await reply_html(client, message, msgs.success(f"<code>{target}</code> is visible on the leaderboards again."))

    # ---------------- /clearlb - reset top leaderboard users' wallet ----------------
    @app.on_message(filters.command("clearlb") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_clearlb(client: Client, message: Message):
        """Set each of the top USER_COUNT leaderboard users' wallet to exactly AMOUNT."""
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/clearlb AMOUNT USER_COUNT</code> — sets the wallet of "
                           "each top USER_COUNT leaderboard user to exactly AMOUNT."),
            )
            return
        try:
            from utils.money import parse_amount, MoneyError

            amount = parse_amount(args[0])
        except (MoneyError, ValueError):
            await reply_html(client, message, msgs.error("Invalid AMOUNT."))
            return
        if amount < 0:
            await reply_html(client, message, msgs.error("AMOUNT cannot be negative."))
            return
        try:
            user_count = int(args[1])
        except ValueError:
            await reply_html(client, message, msgs.error("USER_COUNT must be a whole number."))
            return
        if user_count < 1 or user_count > 20:
            await reply_html(client, message, msgs.error("USER_COUNT must be between 1 and 20."))
            return

        from services import leaderboard as leaderboard_service

        try:
            result = await leaderboard_service.apply_clearlb(
                amount=amount, user_count=user_count, actor_id=message.from_user.id
            )
        except ValueError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.clearlb_result(
                amount=result["amount"],
                done=result["done"],
                skipped=result["skipped"],
            ),
        )

    # ---------------- USERINFO ----------------
    @app.on_message(filters.command("userinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_userinfo(client: Client, message: Message):
        doc = await identity_service.resolve_user(
            client, message, message.command[1] if len(message.command) > 1 else None, create=False
        )
        if doc is None:
            await reply_html(client, message, msgs.error("User not found."))
            return
        from database.transactions import count_for_user

        stats = {"transactions": await count_for_user(doc["user_id"])}
        await reply_html(client, message, msgs.userinfo(doc, stats))

    # ---------------- /data - admin user activity report ----------------
    @app.on_message(filters.command("data") & NOT_CHANNEL)
    @security_sudo_or_owner
    @safe_handler
    async def cmd_data(client: Client, message: Message):
        args = message.command[1:]
        doc = await identity_service.resolve_user(
            client,
            message,
            None if target_from_message(message) is not None else (args[0] if args else None),
            create=False,
        )
        if doc is None:
            await reply_html(
                client, message,
                msgs.error(
                    "User not found. Provide a Telegram ID, @username, UNOITACHI UID, "
                    "or reply to a user with <code>/data</code>."
                ),
            )
            return
        user_id = doc["user_id"]

        from database import asset_holdings as ah_db, security as sec_db, stocks as stocks_db
        from database.transactions import count_for_user, recent_by_user

        stock_holdings = await stocks_db.get_user_holdings(user_id)
        asset_holdings = await ah_db.get_user_holdings(user_id)
        cases = await sec_db.list_cases(user_id=user_id)
        dumps = await sec_db.list_dumps(user_id=user_id)
        recovery = await sec_db.get_recovery_balance(user_id)
        quarantine = await sec_db.get_quarantine(user_id)
        transactions = await recent_by_user(user_id, limit=8)

        report = msgs.user_data_report(
            user=doc,
            stock_holdings=stock_holdings,
            asset_holdings=asset_holdings,
            cases=cases,
            dumps=dumps,
            recovery=recovery,
            quarantine=quarantine,
            transactions=transactions,
            transaction_count=await count_for_user(user_id),
        )
        await reply_html(client, message, report)

    # ---------------- ECONSTATS ----------------
    @app.on_message(filters.command("econstats") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_econstats(client: Client, message: Message):
        stats = {
            "users": await users_db.count_users(),
            "total_wallet": await users_db.aggregate_totals("wallet"),
            "total_bank": await users_db.aggregate_totals("bank"),
            "tax_pool": await tax_service.get_pool_size(),
            "transactions": await mongo.db["transactions"].count_documents({}),
            "stocks": len(await stocks_db.list_active_assets()),
        }
        await reply_html(client, message, msgs.admin_stats(stats))

    # ---------------- /restart - full bot restart (bot admin + owner) ----------------
    @app.on_message(filters.command("restart") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_restart(client: Client, message: Message):
        """/restart — fully restart the bot and reload all modules (BOT ADMIN + OWNER, in groups and DMs)."""
        await reply_html(
            client, message,
            msgs.info("🔄 Restarting the bot... all modules will be reloaded. You will be notified when it is back online."),
        )
        asyncio.get_running_loop().create_task(_restart_bot())

    # ---------------- SECURITY COMMANDS ----------------
    # /gban - Global ban (owner + sudo)
    @app.on_message(filters.command("gban") & NOT_CHANNEL)
    @security_sudo_or_owner
    @safe_handler
    async def cmd_gban(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        # Owner immunity check - owner cannot be globally banned
        if target == message.from_user.id:
            await reply_html(client, message, msgs.error("You cannot globally ban yourself."))
            return
        if target == 6356015122:
            await reply_html(client, message, msgs.error("You cannot globally ban the owner."))
            return
        reason = message.command[2] if len(message.command) > 2 else None
        banned_by = message.from_user.id
        # Create security case for critical violations
        if "exploit" in (reason or "").lower() or "critical" in (reason or "").lower():
            case_id = f"CASE-{uuid.uuid4().hex[:8].upper()}"
            await sec_db.create_case(
                case_id=case_id,
                user_id=target,
                title="Global ban – critical security violation",
                detail=reason or "Global ban by admin",
                created_by=banned_by,
                severity="high",
            )
        else:
            case_id = None
        ban_doc = await security_service.global_ban(target, reason or "Global ban by admin", banned_by, case_id=case_id)
        # Also set local ban flag
        await users_db.set_user_flags(target, is_banned=True)
        await reply_html(
            client, message,
            msgs.success(f"User <code>{target}</code> globally banned."),
        )

    # /ungban - Remove global ban (owner + sudo)
    @app.on_message(filters.command("ungban") & NOT_CHANNEL)
    @security_sudo_or_owner
    @safe_handler
    async def cmd_ungban(client: Client, message: Message):
        target = await _need_target_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        if target == 6356015122:
            await reply_html(client, message, msgs.error("You cannot unban the owner."))
            return
        result = await security_service.global_unban(target)
        if result:
            await users_db.set_user_flags(target, is_banned=False)
            await reply_html(
                client, message,
                msgs.success(f"User <code>{target}</code> unglobally banned."),
            )
        else:
            await reply_html(
                client, message,
                msgs.error(f"User <code>{target}</code> was not globally banned."),
            )

    # /clear - Owner-only manual clear (dump + audit + reset + recovery ID)
    @app.on_message(filters.command("clear") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_clear(client: Client, message: Message):
        args = message.command[1:]
        target = await _need_target_or_error(
            client, message, None if target_from_message(message) is not None else (args[0] if args else None)
        )
        if target is None:
            return
        ok, msg, recovery_id = await security_service.manual_clear(message.from_user.id, target)
        if ok and recovery_id:
            await reply_html(client, message, msgs.success(msg))
        else:
            await reply_html(client, message, msgs.error(msg))

    # /restore - Restore from dump (owner only)
    @app.on_message(filters.command("restore") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_restore(client: Client, message: Message):
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: <code>/restore DUMP-ID</code>"))
            return
        dump_id = args[0]
        ok, msg = await security_service.manual_restore(dump_id, message.from_user.id)
        await reply_html(client, message, msgs.success(msg) if ok else msgs.error(msg))

    # /recover - Recover from dump (owner only)
    @app.on_message(filters.command("recover") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_recover(client: Client, message: Message):
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: <code>/recover DUMP-ID</code>"))
            return
        dump_id = args[0]
        ok, msg = await security_service.manual_restore(dump_id, message.from_user.id)
        await reply_html(client, message, msgs.success(msg) if ok else msgs.error(msg))

    # /restorecase - Restore from case (owner only)
    @app.on_message(filters.command("restorecase") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_restorecase(client: Client, message: Message):
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: <code>/restorecase CASE-ID</code>"))
            return
        case_id = args[0]
        ok, msg = await security_service.manual_restorecase(case_id, message.from_user.id)
        await reply_html(client, message, msgs.success(msg) if ok else msgs.error(msg))

    # /dumpinfo - Show dump info (owner + sudo)
    @app.on_message(filters.command("dumpinfo") & NOT_CHANNEL)
    @security_sudo_or_owner
    @safe_handler
    async def cmd_dumpinfo(client: Client, message: Message):
        from services import security as sec_svc
        dumps = await security_service.list_security_dumps()
        if not dumps:
            await reply_html(client, message, msgs.info("No security dumps found."))
            return
        lines = []
        for dump in dumps[:10]:  # Show last 10
            lines.append(
                f"<b>Dump {dump['dump_id']}</b>: {dump['user_count'] if 'user_count' in dump else '?'} users, "
                f"type: {dump.get('dump_type', '?')}, "
                f"created: {dump.get('created_at', '?')}, "
                f"status: {dump.get('status', '?')}"
            )
        await reply_html(client, message, msgs.info("\n".join(lines)))

    # /dumps - List all dumps (owner + sudo)
    @app.on_message(filters.command("dumps") & NOT_CHANNEL)
    @security_sudo_or_owner
    @safe_handler
    async def cmd_dumps(client: Client, message: Message):
        from services import security as sec_svc
        dumps = await security_service.list_security_dumps()
        if not dumps:
            await reply_html(client, message, msgs.info("No security dumps found."))
            return
        lines = [f"Dump ID: {d['dump_id']} | Type: {d.get('dump_type', '?')} | Users: {d.get('user_count', '?')} | Status: {d.get('status', '?')} | Created: {d.get('created_at', '?')}" for d in dumps]
        await reply_html(client, message, msgs.info("\n".join(lines)))

    # /securityset - View security config (owner only)
    @app.on_message(filters.command("securityset") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_securityset(client: Client, message: Message):
        from services import security as sec_svc
        from services.settings import get_global_ban_on_exploit, get_secret_detection_enabled

        cfg = await security_service.get_security_config() if hasattr(security_service, 'get_security_config') else {}
        # Fallback to settings
        st_cfg = await settings_service.get_settings()
        lines = [
            f"<b>Secret detection:</b> {'✅ Enabled' if st_cfg.get('secret_detection_enabled', True) else '❌ Disabled'}",
            f"<b>Global ban on exploit:</b> {'✅ Enabled' if st_cfg.get('global_ban_on_exploit', True) else '❌ Disabled'}",
            f"<b>Clear recovery balance:</b> {st_cfg.get('security', {}).get('clear_recovery_balance', 20000)}",
        ]
        await reply_html(client, message, msgs.info("\n".join(lines)))

    # ---------------- GROUP CONFIG (owner + sudo) ----------------
    @app.on_message(filters.command("setchat") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="chat_control")
    async def cmd_setchat(client: Client, message: Message):
        args = message.command[1:]
        in_group = chat_type(message.chat) in ("GROUP", "SUPERGROUP")

        if in_group:
            chat_id = message.chat.id
            rest = args
            usage = "Usage (group): <code>/setchat [setting] [on|off]</code>"
        else:
            if not args or not args[0].lstrip("-").isdigit():
                await reply_html(
                    client, message,
                    msgs.error(
                        "Usage (DM): <code>/setchat chat_id [setting] [on|off]</code>\n"
                        "Example: <code>/setchat -1001234567890 economy off</code>"
                    ),
                )
                return
            chat_id = int(args[0])
            rest = args[1:]
            usage = "Usage (DM): <code>/setchat chat_id [setting] [on|off]</code>"

        if not rest:
            cfg = await group_config_service.get_group_config(chat_id)
            await reply_html(client, message, msgs.group_config_status(chat_id, cfg))
            return

        if len(rest) < 2:
            await reply_html(client, message, msgs.error(usage))
            return

        setting, raw_value = rest[0].lower(), rest[1].lower()
        if setting not in group_config_service.SETTING_ALIASES:
            await reply_html(
                client, message,
                msgs.error(f"Unknown setting. Valid: {', '.join(group_config_service.SETTING_ALIASES)}"),
            )
            return
        if raw_value not in ("on", "off", "true", "false"):
            await reply_html(client, message, msgs.error("Value must be <code>on</code> or <code>off</code>."))
            return

        key = group_config_service.SETTING_ALIASES[setting]
        enabled = raw_value in ("on", "true")
        cfg = await group_config_service.update_group_config(chat_id, **{key: enabled})
        await reply_html(
            client, message,
            msgs.success(
                f"Chat <code>{chat_id}</code>: <code>{key}</code> → {'✅ ON' if enabled else '⛔ OFF'}.\n"
                f"{msgs.group_config_status(chat_id, cfg)}"
            ),
        )

    # ---------------- SECRET DETECTION MIDDLEWARE ----------------
    # Note: Secret detection is handled by the middleware in utils/chat.py
    # The /detect command is not a user command - it's handled on-message