"""Admin handlers for emoji games + blackjack configuration.

All commands are OWNER/SUDO only.  Config values persist in the centralized
settings collection and are validated before writing.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import blackjack as blackjack_service
from services import settings as settings_service
from services.emoji_games import EMOJI_GAMES, get_game_def
from utils import messages as msgs
from utils.permissions import sudo_only
from utils.sender import reply_html
from utils.validators import is_safe_multiplier, validate_min_max

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot

# <command field> -> (settings key, parser)
EMOJI_FIELDS: dict[str, tuple[str, type]] = {
    "cooldown": ("cooldown", int),
    "min_bet": ("minimum_bet", int),
    "minbet": ("minimum_bet", int),
    "max_bet": ("maximum_bet", int),
    "maxbet": ("maximum_bet", int),
    "multiplier": ("multiplier", float),
    "mult": ("multiplier", float),
    "win_rule": ("win_rule", str),
    "rule": ("win_rule", str),
    "win_target": ("win_target", int),
    "target": ("win_target", int),
    "single_enabled": ("single_enabled", bool),
    "single": ("single_enabled", bool),
    "duel_enabled": ("duel_enabled", bool),
    "duel": ("duel_enabled", bool),
    "enabled": ("enabled", bool),
    "lobby_expiry": ("lobby_expiry", int),
    "expiry": ("lobby_expiry", int),
}

BJ_FIELDS: dict[str, tuple[str, type]] = {
    "cooldown": ("cooldown", int),
    "min_bet": ("minimum_bet", int),
    "minbet": ("minimum_bet", int),
    "max_bet": ("maximum_bet", int),
    "maxbet": ("maximum_bet", int),
    "multiplier": ("multiplier", float),
    "mult": ("multiplier", float),
    "enabled": ("enabled", bool),
}


def _parse_bool(raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("on", "true", "1", "yes"):
        return True
    if value in ("off", "false", "0", "no"):
        return False
    raise ValueError


def _parse_field(field: str, raw: str) -> tuple[str, object]:
    key, parser = field
    if parser is bool:
        return key, _parse_bool(raw)
    if parser is int:
        return key, int(raw)
    return key, float(raw)


def _validate_emoji_changes(game_type: str, config: dict) -> str | None:
    game_def = get_game_def(game_type)
    if config["win_rule"] not in ("gte", "eq"):
        return "Win rule must be <code>gte</code> or <code>eq</code>."
    target = int(config["win_target"])
    if not (game_def.result_min <= target <= game_def.result_max):
        return (
            f"Win target must be between {game_def.result_min} and "
            f"{game_def.result_max} for {game_def.label}."
        )
    if not is_safe_multiplier(float(config["multiplier"])):
        return "Multiplier must be between 0 and 1000."
    if not validate_min_max(int(config["minimum_bet"]), int(config["maximum_bet"])):
        return "Minimum bet cannot exceed maximum bet."
    if int(config["cooldown"]) < 0:
        return "Cooldown cannot be negative."
    if int(config["lobby_expiry"]) < 0:
        return "Lobby expiry cannot be negative."
    return None


def _validate_bj_changes(config: dict) -> str | None:
    if not is_safe_multiplier(float(config["multiplier"])):
        return "Multiplier must be between 0 and 1000."
    if not validate_min_max(int(config["minimum_bet"]), int(config["maximum_bet"])):
        return "Minimum bet cannot exceed maximum bet."
    if int(config["cooldown"]) < 0:
        return "Cooldown cannot be negative."
    return None


def _apply_pairs(args: list[str], fields: dict) -> dict:
    changes: dict[str, object] = {}
    for arg in args:
        if "=" not in arg:
            raise ValueError(f"Expected <code>key=value</code>, got <code>{arg}</code>.")
        raw_key, raw_value = arg.split("=", 1)
        raw_key = raw_key.lower().strip()
        if raw_key not in fields:
            raise ValueError(f"Unknown field <code>{raw_key}</code>.")
        key, value = _parse_field(fields[raw_key], raw_value)
        changes[key] = value
    return changes


def register(app: Client) -> None:
    @app.on_message(filters.command("emojiset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_emojiset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 3:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/emojiset GAME field value</code>. "
                    f"Fields: {', '.join(EMOJI_FIELDS)}"
                ),
            )
            return
        game_type = args[0].lower()
        if game_type not in EMOJI_GAMES:
            await reply_html(
                client, message,
                msgs.error(f"Unknown game. Valid: {', '.join(EMOJI_GAMES)}"),
            )
            return
        field, raw_value = args[1].lower(), args[2]
        if field not in EMOJI_FIELDS:
            await reply_html(
                client, message,
                msgs.error(f"Unknown field. Valid: {', '.join(EMOJI_FIELDS)}"),
            )
            return
        try:
            key, value = _parse_field(EMOJI_FIELDS[field], raw_value)
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid value for field."))
            return
        config = await settings_service.get_emoji_game_config(game_type)
        config[key] = value
        err = _validate_emoji_changes(game_type, config)
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        await settings_service.update_emoji_game_config(game_type, **{key: value})
        await reply_html(
            client, message,
            msgs.success(f"Emoji game <code>{game_type}</code>: <code>{key}</code> set to {value}."),
        )

    @app.on_message(filters.command("emojitrap") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_emojitrap(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/emojitrap GAME key=value key=value ...</code> "
                    "(e.g. <code>/emojitrap ball cooldown=30 min_bet=50 max_bet=50000 "
                    "multiplier=2 rule=gte target=5</code>)"
                ),
            )
            return
        game_type = args[0].lower()
        if game_type not in EMOJI_GAMES:
            await reply_html(
                client, message,
                msgs.error(f"Unknown game. Valid: {', '.join(EMOJI_GAMES)}"),
            )
            return
        try:
            changes = _apply_pairs(args[1:], EMOJI_FIELDS)
        except ValueError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        config = await settings_service.get_emoji_game_config(game_type)
        config.update(changes)
        err = _validate_emoji_changes(game_type, config)
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        updated = await settings_service.update_emoji_game_config(game_type, **changes)
        keys = ", ".join(f"<code>{k}</code>={v}" for k, v in updated.items()
                         if k in changes)
        await reply_html(client, message, msgs.success(f"Emoji game <code>{game_type}</code> updated: {keys}."))

    @app.on_message(filters.command("emojigameinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_emojigameinfo(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if not args:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/emojigameinfo GAME</code>"),
            )
            return
        game_type = args[0].lower()
        if game_type not in EMOJI_GAMES:
            await reply_html(
                client, message,
                msgs.error(f"Unknown game. Valid: {', '.join(EMOJI_GAMES)}"),
            )
            return
        game_def = get_game_def(game_type)
        config = await settings_service.get_emoji_game_config(game_type)
        await reply_html(
            client, message,
            msgs.emoji_game_info(game_type, game_def.emoji, game_def.label, config),
        )

    @app.on_message(filters.command("emojigames") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_emojigames(client: Client, message: Message):
        await ensure_user(client, message)
        configs = await settings_service.get_emoji_games_config()
        await reply_html(client, message, msgs.emoji_games_list(configs, EMOJI_GAMES))

    @app.on_message(filters.command("bjset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_bjset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/bjset field value</code> or "
                    f"<code>/bjset key=value ...</code>. Fields: {', '.join(BJ_FIELDS)}"
                ),
            )
            return
        config = await blackjack_service.get_config()
        if "=" in args[0]:
            try:
                changes = _apply_pairs(args, BJ_FIELDS)
            except ValueError as exc:
                await reply_html(client, message, msgs.error(str(exc)))
                return
            config.update(changes)
        else:
            field = args[0].lower()
            if field not in BJ_FIELDS:
                await reply_html(
                    client, message,
                    msgs.error(f"Unknown field. Valid: {', '.join(BJ_FIELDS)}"),
                )
                return
            try:
                key, value = _parse_field(BJ_FIELDS[field], args[1])
            except ValueError:
                await reply_html(client, message, msgs.error("Invalid value for field."))
                return
            config[key] = value
        err = _validate_bj_changes(config)
        if err:
            await reply_html(client, message, msgs.error(err))
            return
        await settings_service.update_blackjack_config(**config)
        await reply_html(client, message, msgs.success("Blackjack settings updated."))

    @app.on_message(filters.command("bjinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler
    async def cmd_bjinfo(client: Client, message: Message):
        await ensure_user(client, message)
        config = await blackjack_service.get_config()
        await reply_html(client, message, msgs.blackjack_info(config))
