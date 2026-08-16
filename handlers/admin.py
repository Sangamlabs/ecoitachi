"""Admin handlers - owner & sudo economy administration.

Permission gates come from :mod:`utils.permissions` (numeric IDs only).
Every admin money action produces an audit transaction.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from database import admins as admins_db, users as users_db
from database import stocks as stocks_db
from database.mongo import mongo
from handlers.common import ensure_user, safe_handler
from services import bank as bank_service, economy, settings as settings_service, tax as tax_service
from services import group_config as group_config_service
from services import transaction as tx_service
from services.economy import EconomyError
from utils import messages as msgs
from utils.chat import chat_type
from utils.money import format_money
from utils.permissions import is_owner as utils_permissions_is_owner
from utils.permissions import owner_only, sudo_only
from utils.sender import reply_html
from utils.validators import (
    is_safe_multiplier,
    is_safe_percent,
    is_safe_probability,
    parse_amount_or_error,
    validate_min_max,
)

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot

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


async def _resolve_target(message: Message, arg: str | None) -> int | None:
    """Resolve a numeric user id from @username, numeric id or reply."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if not arg:
        return None
    if arg.startswith("@"):
        doc = await users_db.get_user_by_username(arg[1:])
        return doc["user_id"] if doc else None
    if arg.isdigit():
        return int(arg)
    return None


async def _need_user_or_error(client: Client, message: Message, arg: str | None) -> int | None:
    target = await _resolve_target(message, arg)
    if target is None or not await users_db.user_exists(target):
        await reply_html(client, message, msgs.error("User not found. They must start the bot."))
        return None
    return target


def _validate_fly_settings(difficulty: str, settings: dict) -> str | None:
    """Return an error string if the proposed fly config is invalid."""
    for key in ("min_mult", "max_mult", "risk", "win_prob", "cooldown", "min_bet", "max_bet"):
        if key not in settings:
            continue
        value = settings[key]
        if key in ("min_mult", "max_mult"):
            if not is_safe_multiplier(value):
                return "Multipliers must be finite and between 0 and 1000."
        elif key == "risk":
            if not is_safe_percent(value):
                return "Risk must be a percentage between 0 and 100."
        elif key == "win_prob":
            if not is_safe_probability(value):
                return "Win probability must be between 0 and 1."
        elif value < 0:
            return f"{key} cannot be negative."
    if "min_mult" in settings and "max_mult" in settings and not validate_min_max(
        settings["min_mult"], settings["max_mult"]
    ):
        return "minimum multiplier cannot exceed maximum multiplier."
    if "min_bet" in settings and "max_bet" in settings and not validate_min_max(
        settings["min_bet"], settings["max_bet"]
    ):
        return "minimum bet cannot exceed maximum bet."
    return None


def register(app: Client) -> None:
    # ---------------- OWNER ONLY ----------------
    @app.on_message(filters.command("addsudo") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_addsudo(client: Client, message: Message):
        target = await _resolve_target(message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            await reply_html(client, message, msgs.error("Usage: <code>/addsudo @user</code> or reply."))
            return
        await admins_db.add_sudo(target, message.from_user.id)
        await reply_html(client, message, msgs.success(f"Added <code>{target}</code> as sudo admin."))

    @app.on_message(filters.command("rsudo") & NOT_CHANNEL)
    @owner_only
    @safe_handler
    async def cmd_rsudo(client: Client, message: Message):
        target = await _resolve_target(message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            await reply_html(client, message, msgs.error("Usage: <code>/rsudo @user</code> or reply."))
            return
        if await utils_permissions_is_owner(target):
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
        target = await _need_user_or_error(client, message, args[0] if args else None)
        if target is None:
            return
        amount, err = parse_amount_or_error(args[1] if len(args) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/give @user amount</code>. {err}"))
            return
        actor = message.from_user.id
        await economy.admin_give(target, amount, actor)
        before = await economy.get_balance(target)
        tx_id = await tx_service.record(
            user_id=target,
            ttype=tx_service.ADMIN_GIVE,
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
        target = await _need_user_or_error(client, message, args[0] if args else None)
        if target is None:
            return
        amount, err = parse_amount_or_error(args[1] if len(args) > 1 else "")
        if err:
            await reply_html(client, message, msgs.error(f"Usage: <code>/remove @user amount</code>. {err}"))
            return
        actor = message.from_user.id
        before = await economy.get_balance(target)
        try:
            await economy.admin_remove(target, amount, actor)
        except EconomyError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        tx_id = await tx_service.record(
            user_id=target,
            ttype=tx_service.ADMIN_REMOVE,
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
        await economy.admin_give(actor, amount, actor)
        before = await economy.get_balance(actor)
        tx_id = await tx_service.record(
            user_id=actor,
            ttype=tx_service.ADMIN_GIVE,
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

    @app.on_message(filters.command("banksettings") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_banksettings(client: Client, message: Message):
        settings = await bank_service.get_bank_settings()
        pool = await tax_service.get_pool_size()
        await reply_html(client, message, msgs.banksettings(settings, pool))

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
                msgs.error(
                    "Usage: <code>/robset win_prob|percent|min|max|cooldown value</code>"
                ),
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

    @app.on_message(filters.command("freeze") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_freeze(client: Client, message: Message):
        target = await _need_user_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        await users_db.set_user_flags(target, is_frozen=True)
        await reply_html(client, message, msgs.success(f"Frozen <code>{target}</code>."))

    @app.on_message(filters.command("unfreeze") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_unfreeze(client: Client, message: Message):
        target = await _need_user_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        await users_db.set_user_flags(target, is_frozen=False)
        await reply_html(client, message, msgs.success(f"Unfrozen <code>{target}</code>."))

    @app.on_message(filters.command("ban") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_ban(client: Client, message: Message):
        target = await _need_user_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        if target == message.from_user.id:
            await reply_html(client, message, msgs.error("You cannot ban yourself."))
            return
        await users_db.set_user_flags(target, is_banned=True)
        await reply_html(client, message, msgs.success(f"Banned <code>{target}</code>."))

    @app.on_message(filters.command("unban") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_unban(client: Client, message: Message):
        target = await _need_user_or_error(client, message, message.command[1] if len(message.command) > 1 else None)
        if target is None:
            return
        await users_db.set_user_flags(target, is_banned=False)
        await reply_html(client, message, msgs.success(f"Unbanned <code>{target}</code>."))

    @app.on_message(filters.command("userinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_userinfo(client: Client, message: Message):
        target = await _resolve_target(message, message.command[1] if len(message.command) > 1 else None)
        if target is None or not await users_db.user_exists(target):
            await reply_html(client, message, msgs.error("User not found."))
            return
        doc = await users_db.get_user(target)
        from database.transactions import count_for_user

        stats = {"transactions": await count_for_user(target)}
        await reply_html(client, message, msgs.userinfo(doc, stats))

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
