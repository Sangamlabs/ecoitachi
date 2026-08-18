"""Centralized settings service.

All admin-configurable values (interest, tax, game tuning, cooldowns) are
stored in MongoDB and read/written through this module.  Handlers must never
hardcode configurable numbers.
"""

from __future__ import annotations

from typing import Any

from database.mongo import mongo

DEFAULT_SLOT_PAYOUTS: dict[str, dict[str, int]] = {
    "BAR": {"triple": 64, "pair": 4},
    "GRAPE": {"triple": 32, "pair": 2},
    "LEMON": {"triple": 16, "pair": 2},
    "SEVEN": {"triple": 8, "pair": 2},
}

COLLECTION = "settings"

DEFAULTS: dict[str, Any] = {
    "currency": "₹ UN",
    "starting_balance": 50_000,  # ₹500 welcome grant for new users
    "bank_interest_rate": 2.0,
    "bank_interest_interval_hours": 24,
    "withdrawal_tax_rate": 5.0,
    "default_game_cooldown": 60,
    "tax_distribution": {
        "enabled": True,
        "percentages": [25.0, 18.0, 13.0, 10.0, 8.0, 7.0, 6.0, 5.0, 4.0, 4.0],
    },
    "rewards": {
        "daily": {"amount": 50_000, "cooldown": 86_400},
        "weekly": {"amount": 300_000, "cooldown": 604_800},
        "monthly": {"amount": 1_200_000, "cooldown": 2_592_000},
    },
    "asset_market": {
        "enabled": True,
        "tick_interval_minutes": 2,
        "default_volatility": 0.02,
        "default_fractional_allowed": False,
        "buy_fee_percent": 0.0,
        "sell_fee_percent": 0.0,
        "listing_fee_percent": 0.0,
        "buy_price_multiplier": 1.0,
        "sell_price_multiplier": 1.0,
        "price_history_retention": 500,
    },
    "income": {
        "bank_rate_percent": 2.0,
        "asset_rate_percent": 1.0,
        "stock_rate_percent": 1.0,
    },
    "system_taxes": {
        "assets": 0.0,
        "stocks": 0.0,
        "payments": 0.0,
        "mines": 0.0,
        "fly": 0.0,
        "bet": 0.0,
        "emoji": 0.0,
        "blackjack": 0.0,
    },
    "emoji_games": {
        "ball": {
            "enabled": True,
            "single_enabled": True,
            "duel_enabled": True,
            "cooldown": 60,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
            "win_rule": "gte",
            "win_target": 5,
            "multiplier": 1.0,
            "lobby_expiry": 300,
        },
        "arrow": {
            "enabled": True,
            "single_enabled": True,
            "duel_enabled": True,
            "cooldown": 60,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
            "win_rule": "eq",
            "win_target": 6,
            "multiplier": 1.5,
            "lobby_expiry": 300,
        },
        "basketball": {
            "enabled": True,
            "single_enabled": True,
            "duel_enabled": True,
            "cooldown": 60,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
            "win_rule": "gte",
            "win_target": 4,
            "multiplier": 1.5,
            "lobby_expiry": 300,
        },
        "football": {
            "enabled": True,
            "single_enabled": True,
            "duel_enabled": True,
            "cooldown": 60,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
            "win_rule": "eq",
            "win_target": 5,
            "multiplier": 1.5,
            "lobby_expiry": 300,
        },
        "dice": {
            "enabled": True,
            "single_enabled": True,
            "duel_enabled": True,
            "cooldown": 60,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
            "win_rule": "eq",
            "win_target": 6,
            "multiplier": 2.0,
            "lobby_expiry": 300,
        },
        "slot": {
            "enabled": True,
            "single_enabled": True,
            "duel_enabled": True,
            "cooldown": 60,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
            "win_rule": "eq",
            "win_target": 64,
            "multiplier": 1.0,
            "lobby_expiry": 300,
            "slot_payouts": DEFAULT_SLOT_PAYOUTS,
        },
    },
    "blackjack": {
        "enabled": True,
        "cooldown": 60,
        "minimum_bet": 100,
        "maximum_bet": 100_000,
        "multiplier": 1.0,
    },
    "security": {
        "clear_recovery_balance": 999999999,
        "global_ban_on_exploit": False,
        "secret_detection_enabled": False,
    },
    "loan": {
        "enabled": True,
        "max_principal": 100_000,
        "min_duration_days": 1,
        "max_duration_days": 7,
        "interest_rate": 1.0,
        "recovery_percent": 100,
    },
}


async def ensure_indexes() -> None:
    await mongo.db[COLLECTION].create_index("key", unique=True)


async def get_settings() -> dict[str, Any]:
    doc = await mongo.db[COLLECTION].find_one({"key": "global"})
    merged = dict(DEFAULTS)
    if doc:
        merged.update({k: v for k, v in doc.items() if k not in ("_id", "key")})
    return merged


async def update_settings(**changes: Any) -> dict[str, Any]:
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {**changes, "key": "global"}}, upsert=True
    )
    return await get_settings()


async def get_bank_interest_rate() -> float:
    settings = await get_settings()
    return float(settings.get("bank_interest_rate", 2.0))


async def get_withdrawal_tax_rate() -> float:
    settings = await get_settings()
    return float(settings.get("withdrawal_tax_rate", 5.0))


async def get_default_cooldown() -> int:
    settings = await get_settings()
    return int(settings.get("default_game_cooldown", 60))


async def get_starting_balance() -> int:
    settings = await get_settings()
    return int(settings.get("starting_balance", 0))


async def get_tax_distribution() -> dict[str, Any]:
    settings = await get_settings()
    return dict(settings.get("tax_distribution", DEFAULTS["tax_distribution"]))


async def get_game_settings(game: str) -> dict[str, Any]:
    """Return merged settings for a game (falling back to defaults)."""
    key = f"{game}_settings"
    doc = await mongo.db[COLLECTION].find_one({"key": key})
    defaults = GAME_DEFAULTS.get(game, {})
    if doc:
        defaults.update({k: v for k, v in doc.items() if k not in ("_id", "key")})
    return defaults


async def get_rewards() -> dict[str, Any]:
    """Return the configured daily/weekly/monthly reward amounts and cooldowns."""
    settings = await get_settings()
    return dict(settings.get("rewards", DEFAULTS["rewards"]))


async def update_rewards(**changes: dict[str, Any]) -> dict[str, Any]:
    """Overwrite one or more reward entries (e.g. ``update_rewards(daily={...})``)."""
    rewards = await get_rewards()
    rewards.update(changes)
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"rewards": rewards}}, upsert=True
    )
    return rewards


async def get_asset_market_config() -> dict[str, Any]:
    """Return the centralized Assets Market configuration."""
    settings = await get_settings()
    return dict(settings.get("asset_market", DEFAULTS["asset_market"]))


async def update_asset_market_config(**changes: Any) -> dict[str, Any]:
    """Overwrite one or more Assets Market configuration values."""
    current = await get_asset_market_config()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS["asset_market"]})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"asset_market": current}}, upsert=True
    )
    return current


async def get_income_config() -> dict[str, Any]:
    """Return the centralized daily-income (claim) configuration."""
    settings = await get_settings()
    return dict(settings.get("income", DEFAULTS["income"]))


async def update_income_config(**changes: Any) -> dict[str, Any]:
    """Overwrite one or more daily-income rates (values in percent per 24h)."""
    current = await get_income_config()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS["income"]})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"income": current}}, upsert=True
    )
    return current


async def get_system_taxes() -> dict[str, Any]:
    """Return per-system transaction tax rates (percent), merged over defaults."""
    settings = await get_settings()
    return dict(settings.get("system_taxes", DEFAULTS["system_taxes"]))


async def update_system_taxes(**changes: Any) -> dict[str, Any]:
    """Overwrite one or more per-system tax rates (percent)."""
    current = await get_system_taxes()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS["system_taxes"]})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"system_taxes": current}}, upsert=True
    )
    return current


async def update_game_settings(game: str, **changes: Any) -> dict[str, Any]:
    await mongo.db[COLLECTION].update_one(
        {"key": f"{game}_settings"},
        {"$set": {**changes, "key": f"{game}_settings"}},
        upsert=True,
    )
    return await get_game_settings(game)


async def get_emoji_games_config() -> dict[str, dict[str, Any]]:
    """Return per-emoji-game config merged over defaults."""
    settings = await get_settings()
    stored = settings.get("emoji_games", {})
    merged: dict[str, dict[str, Any]] = {}
    for game, defaults in DEFAULTS["emoji_games"].items():
        cfg = dict(defaults)
        cfg.update(stored.get(game, {}) if isinstance(stored.get(game), dict) else {})
        merged[game] = cfg
    return merged


async def get_emoji_game_config(game: str) -> dict[str, Any]:
    """Return merged config for one emoji game (falls back to defaults)."""
    config = await get_emoji_games_config()
    if game not in config:
        raise ValueError(f"Unknown emoji game: {game}")
    return config[game]


async def update_emoji_game_config(game: str, **changes: Any) -> dict[str, Any]:
    """Overwrite one or more config values for an emoji game."""
    if game not in DEFAULTS["emoji_games"]:
        raise ValueError(f"Unknown emoji game: {game}")
    config = await get_emoji_games_config()
    allowed = set(DEFAULTS["emoji_games"][game])
    config[game].update({k: v for k, v in changes.items() if k in allowed})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"emoji_games": config}}, upsert=True
    )
    return config[game]


async def get_blackjack_config() -> dict[str, Any]:
    """Return the Blackjack configuration merged over defaults."""
    settings = await get_settings()
    return dict(settings.get("blackjack", DEFAULTS["blackjack"]))


async def update_blackjack_config(**changes: Any) -> dict[str, Any]:
    """Overwrite one or more Blackjack configuration values."""
    current = await get_blackjack_config()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS["blackjack"]})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"blackjack": current}}, upsert=True
    )
    return current


async def get_security_config() -> dict[str, Any]:
    """Return the security configuration merged over defaults."""
    settings = await get_settings()
    return dict(settings.get("security", DEFAULTS["security"]))


async def update_security_config(**changes: Any) -> dict[str, Any]:
    """Overwrite one or more security configuration values."""
    current = await get_security_config()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS["security"]})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"security": current}}, upsert=True
    )
    return current


async def get_loan_config() -> dict[str, Any]:
    """Return the loan configuration merged over defaults."""
    settings = await get_settings()
    return dict(settings.get("loan", DEFAULTS["loan"]))


async def update_loan_config(**changes: Any) -> dict[str, Any]:
    """Overwrite one or more loan configuration values."""
    current = await get_loan_config()
    current.update({k: v for k, v in changes.items() if k in DEFAULTS["loan"]})
    await mongo.db[COLLECTION].update_one(
        {"key": "global"}, {"$set": {"loan": current}}, upsert=True
    )
    return current


GAME_DEFAULTS: dict[str, dict[str, Any]] = {
    "fly": {
        "low": {
            "minimum_multiplier": 1.1,
            "maximum_multiplier": 1.6,
            "risk": 0.2,
            "win_probability": 0.75,
            "minimum_bet": 100,
            "maximum_bet": 100_000,
        },
        "medium": {
            "minimum_multiplier": 1.5,
            "maximum_multiplier": 2.5,
            "risk": 0.35,
            "win_probability": 0.55,
            "minimum_bet": 100,
            "maximum_bet": 250_000,
        },
        "high": {
            "minimum_multiplier": 2.0,
            "maximum_multiplier": 5.0,
            "risk": 0.5,
            "win_probability": 0.35,
            "minimum_bet": 100,
            "maximum_bet": 500_000,
        },
        "cooldown": 60,
    },
    "mines": {
        "bomb_count": 5,
        "min_reveals": 3,
        "multipliers": [1.0, 1.18, 1.4, 1.66, 2.0, 2.45, 3.0, 3.8, 4.9, 6.5, 9.0, 13.0, 19.0, 30.0, 50.0, 85.0, 150.0],
        "minimum_bet": 100,
        "maximum_bet": 200_000,
        "cooldown": 60,
        "duration": 300,
        "board_size": 6,
    },
    "bet": {
        "win_probability": 0.5,
        "multiplier": 2.0,
        "minimum_bet": 100,
        "maximum_bet": 100_000,
        "cooldown": 60,
    },
    "rob": {
        "success_probability": 0.5,
        "bank_percentage": 10.0,
        "minimum_amount": 100,
        "maximum_amount": 500_000,
        "cooldown": 60,
    },
}

SECURITY_KEYWORDS: list[str] = [
    "token",
    "api_key",
    "secret",
    "password",
    "private_key",
]


async def get_clear_recovery_balance() -> int:
    """Return the configured recovery balance a manual /clear resets to."""
    settings = await get_settings()
    security = settings.get("security") or {}
    return int(security.get("clear_recovery_balance", 20_000))


async def get_global_ban_on_exploit() -> bool:
    """Return whether exploits should trigger automatic global ban."""
    settings = await get_settings()
    return bool(settings.get("global_ban_on_exploit", True))


async def get_secret_detection_enabled() -> bool:
    """Return whether secret/API-key detection is enabled."""
    settings = await get_settings()
    return bool(settings.get("secret_detection_enabled", True))


async def is_security_enabled() -> bool:
    """Return whether the full security system is active."""
    return await get_secret_detection_enabled()
