"""Integration tests for the emoji games engine (single + duel).

Covers pure win/loss/duel resolution, the single-player money flow, the duel
lobby lifecycle (create/join/settle), expiry refunds and admin configuration.

Run with:  pytest tests/test_emoji_games.py -v
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_emoji")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import emoji_games as emoji_db  # noqa: E402
from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import economy, settings as settings_service  # noqa: E402
from services import emoji_games as emoji_service  # noqa: E402
from services import transaction as tx_service  # noqa: E402
from utils.cooldown import cooldown_manager  # noqa: E402

A, B, ADMIN = 9501, 9502, 1

GAMES = ("ball", "arrow", "basketball", "football", "dice", "slot")


def mongo_available() -> bool:
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(mongo.connect())
        loop.run_until_complete(mongo.close())
        loop.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not mongo_available(), reason="MongoDB not reachable")


@pytest.fixture(autouse=True)
async def clean_db():
    await mongo.connect()
    db = mongo.db
    await db.users.delete_many({"user_id": {"$in": [A, B]}})
    await db.transactions.delete_many({"user_id": {"$in": [A, B]}})
    await db.emoji_game_sessions.delete_many({})
    await db.game_cooldowns.delete_many({})
    await db.settings.delete_many({})
    await settings_service.ensure_indexes()
    await emoji_db.ensure_indexes()
    await settings_service.update_settings(starting_balance=0)
    await users_db.get_or_create_user(A, "user_a", "User A")
    await users_db.get_or_create_user(B, "user_b", "User B")
    for game in GAMES:
        await cooldown_manager.clear(game, A)
        await cooldown_manager.clear(game, B)
    await cooldown_manager.clear("blackjack", A)
    await cooldown_manager.clear("blackjack", B)
    yield
    await mongo.close()


async def _fund(user_id: int, amount: int) -> None:
    await economy.admin_give(user_id, amount, ADMIN)


async def _wallet(user_id: int) -> int:
    return (await economy.get_balance(user_id))["wallet"]


# ---------- pure logic ----------


def test_evaluate_single_gte_win():
    config = {"win_rule": "gte", "win_target": 5, "multiplier": 1.0}
    result = emoji_service.evaluate_single("ball", 5, 10_000, config)
    assert result["won"] is True
    assert result["outcome"] == "win"
    assert result["payout"] == 20_000
    assert result["profit"] == 10_000


def test_evaluate_single_gte_loss():
    config = {"win_rule": "gte", "win_target": 5, "multiplier": 1.0}
    result = emoji_service.evaluate_single("ball", 3, 10_000, config)
    assert result["won"] is False
    assert result["outcome"] == "loss"
    assert result["payout"] == 0
    assert result["profit"] == -10_000


def test_evaluate_single_eq_rule():
    config = {"win_rule": "eq", "win_target": 6, "multiplier": 1.5}
    win = emoji_service.evaluate_single("arrow", 6, 10_000, config)
    loss = emoji_service.evaluate_single("arrow", 5, 10_000, config)
    assert win["won"] is True
    assert win["payout"] == 10_000 + 15_000
    assert loss["won"] is False


def test_evaluate_single_invalid_result():
    config = {"win_rule": "gte", "win_target": 5, "multiplier": 1.0}
    with pytest.raises(emoji_service.EmojiGameError):
        emoji_service.evaluate_single("basketball", 7, 10_000, config)


def test_resolve_duel_result_thresholds():
    from services.emoji_games import get_game_def, resolve_duel_result
    ball = get_game_def("ball")  # 1-6
    # P1 wins upper half (4-6)
    res = resolve_duel_result(ball, 6, (A, "P1"), (B, "P2"), 10_000, {})
    assert res["outcome"] == "player1"
    assert res["winner_id"] == A
    res = resolve_duel_result(ball, 1, (A, "P1"), (B, "P2"), 10_000, {})
    assert res["outcome"] == "player2"
    assert res["winner_id"] == B
    # No draw on even range
    res = resolve_duel_result(ball, 3, (A, "P1"), (B, "P2"), 10_000, {})
    assert res["outcome"] == "player2"

    basketball = get_game_def("basketball")  # 1-5
    # Draw on middle (3)
    res = resolve_duel_result(basketball, 3, (A, "P1"), (B, "P2"), 10_000, {})
    assert res["outcome"] == "draw"
    # P1 wins upper half (4-5)
    res = resolve_duel_result(basketball, 5, (A, "P1"), (B, "P2"), 10_000, {})
    assert res["outcome"] == "player1"
    # P2 wins lower half (1-2)
    res = resolve_duel_result(basketball, 1, (A, "P1"), (B, "P2"), 10_000, {})
    assert res["outcome"] == "player2"


# ---------- single player flow ----------


async def test_single_win_flow():
    await _fund(A, 1_000_000)
    started = await emoji_service.start_single(A, "ball", 10_000, chat_id=1, username="user_a", name="User A")
    assert (await _wallet(A)) == 990_000
    outcome = await emoji_service.settle_single(started["session_id"], 6)
    assert outcome["won"] is True
    assert outcome["payout"] == 20_000
    assert (await _wallet(A)) == 990_000 + 20_000
    txs = await tx_service.get_recent(A, 10)
    assert any(t["type"] == tx_service.GAME_BET for t in txs)
    assert any(t["type"] == tx_service.EMOJI_GAME_WIN for t in txs)
    # idempotent: settling again pays nothing
    assert await emoji_service.settle_single(started["session_id"], 6) is None
    assert (await _wallet(A)) == 990_000 + 20_000


async def test_single_loss_flow():
    await _fund(A, 1_000_000)
    started = await emoji_service.start_single(A, "ball", 10_000)
    outcome = await emoji_service.settle_single(started["session_id"], 2)
    assert outcome["won"] is False
    assert outcome["payout"] == 0
    assert (await _wallet(A)) == 990_000
    txs = await tx_service.get_recent(A, 10)
    assert any(t["type"] == tx_service.EMOJI_GAME_LOSS for t in txs)


async def test_single_win_tax_withheld():
    await settings_service.update_system_taxes(emoji=5.0)
    await cooldown_manager.clear("ball", A)
    await _fund(A, 1_000_000)
    started = await emoji_service.start_single(A, "ball", 10_000)
    outcome = await emoji_service.settle_single(started["session_id"], 6)
    # payout 20_000, tax 5% -> net 19_000
    assert outcome["payout"] == 20_000
    assert (await _wallet(A)) == 990_000 + 19_000


async def test_single_cooldown_blocks():
    await _fund(A, 1_000_000)
    started = await emoji_service.start_single(A, "ball", 10_000)
    await emoji_service.settle_single(started["session_id"], 6)
    with pytest.raises(emoji_service.EmojiGameCooldown):
        await emoji_service.start_single(A, "ball", 10_000)


async def test_bet_limits():
    await _fund(A, 1_000_000)
    with pytest.raises(emoji_service.EmojiGameError):
        await emoji_service.start_single(A, "ball", 10)  # below default 100
    await settings_service.update_emoji_game_config("ball", minimum_bet=100, maximum_bet=500)
    with pytest.raises(emoji_service.EmojiGameError):
        await emoji_service.start_single(A, "ball", 600)


async def test_insufficient_balance():
    await _fund(A, 50)
    with pytest.raises(economy.InsufficientBalance):
        await emoji_service.start_single(A, "ball", 100)


# ---------- duel lifecycle ----------


async def test_duel_create_locks_bet():
    await _fund(A, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000, username="user_a", name="User A")
    assert len(lobby["game_id"]) == 4 and lobby["game_id"].isdigit()
    assert (await _wallet(A)) == 990_000
    session = await emoji_db.get_duel(lobby["game_id"])
    assert session["status"] == "waiting"
    assert session["bet"] == 10_000


async def test_duel_p1_wins():
    await _fund(A, 1_000_000)
    await _fund(B, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000, username="user_a", name="User A")
    joined = await emoji_service.join_duel(lobby["game_id"], B, username="user_b", name="User B")
    assert (await _wallet(B)) == 990_000
    # ball range 1-6, upper half 4-6 -> P1 wins
    outcome = await emoji_service.settle_duel(joined["session_id"], 6)
    assert outcome["outcome"] == "player1"
    assert outcome["payout"] == 20_000
    assert outcome["winner_id"] == A
    assert (await _wallet(A)) == 1_000_000 - 10_000 + 20_000
    assert (await _wallet(B)) == 990_000
    # idempotent
    assert await emoji_service.settle_duel(joined["session_id"], 6) is None
    assert (await _wallet(A)) == 1_000_000 - 10_000 + 20_000
    txs_b = await tx_service.get_recent(B, 10)
    assert any(t["type"] == tx_service.EMOJI_DUEL_LOSS for t in txs_b)


async def test_duel_p2_wins():
    await _fund(A, 1_000_000)
    await _fund(B, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000, username="user_a", name="User A")
    joined = await emoji_service.join_duel(lobby["game_id"], B, username="user_b", name="User B")
    # ball range 1-6, lower half 1-3 -> P2 wins
    outcome = await emoji_service.settle_duel(joined["session_id"], 2)
    assert outcome["outcome"] == "player2"
    assert outcome["payout"] == 20_000
    assert outcome["winner_id"] == B
    assert (await _wallet(B)) == 1_000_000 - 10_000 + 20_000
    assert (await _wallet(A)) == 990_000


async def test_duel_draw_refunds_both():
    await _fund(A, 1_000_000)
    await _fund(B, 1_000_000)
    # basketball range 1-5, middle 3 -> draw
    lobby = await emoji_service.create_duel(A, "basketball", 10_000)
    joined = await emoji_service.join_duel(lobby["game_id"], B)
    outcome = await emoji_service.settle_duel(joined["session_id"], 3)
    assert outcome["outcome"] == "draw"
    assert (await _wallet(A)) == 1_000_000
    assert (await _wallet(B)) == 1_000_000
    txs_a = await tx_service.get_recent(A, 10)
    assert any(t["type"] == tx_service.EMOJI_DUEL_DRAW for t in txs_a)


async def test_duel_self_join_rejected():
    await _fund(A, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000)
    with pytest.raises(emoji_service.EmojiSelfJoin):
        await emoji_service.join_duel(lobby["game_id"], A)


async def test_duel_unknown_code():
    with pytest.raises(emoji_service.EmojiDuelNotFound):
        await emoji_service.join_duel("9999", B)


async def test_duel_join_requires_funds():
    await _fund(A, 1_000_000)
    await _fund(B, 50)
    lobby = await emoji_service.create_duel(A, "ball", 10_000)
    with pytest.raises(economy.InsufficientBalance):
        await emoji_service.join_duel(lobby["game_id"], B)


async def test_expired_duel_refund():
    await _fund(A, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000)
    assert (await _wallet(A)) == 990_000
    # force expiry
    await mongo.db.emoji_game_sessions.update_one(
        {"session_id": lobby["session_id"]},
        {"$set": {"expires_at": int(time.time()) - 10}},
    )
    handled = await emoji_service.expire_stale_duels()
    assert lobby["session_id"] in handled
    assert (await _wallet(A)) == 1_000_000
    session = await emoji_db.get_session(lobby["session_id"])
    assert session["status"] == "expired"
    # idempotent: no double refund
    handled2 = await emoji_service.expire_stale_duels()
    assert lobby["session_id"] not in handled2
    assert (await _wallet(A)) == 1_000_000
    txs = await tx_service.get_recent(A, 10)
    assert any(t["type"] == tx_service.EMOJI_DUEL_REFUND for t in txs)


async def test_join_expired_lobby_rejected():
    await _fund(A, 1_000_000)
    await _fund(B, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000)
    await mongo.db.emoji_game_sessions.update_one(
        {"session_id": lobby["session_id"]},
        {"$set": {"expires_at": int(time.time()) - 10}},
    )
    with pytest.raises(emoji_service.EmojiDuelExpired):
        await emoji_service.join_duel(lobby["game_id"], B)


# ---------- configuration ----------


async def test_emoji_config_defaults():
    config = await settings_service.get_emoji_games_config()
    assert set(config) == set(GAMES)
    assert config["ball"]["win_rule"] == "gte"
    assert config["arrow"]["win_rule"] == "eq"
    single = await settings_service.get_emoji_game_config("ball")
    assert single["cooldown"] == 60
    assert single["minimum_bet"] == 100


async def test_emoji_config_update():
    await settings_service.update_emoji_game_config("ball", cooldown=30, multiplier=2.0, win_rule="eq", win_target=6)
    config = await settings_service.get_emoji_game_config("ball")
    assert config["cooldown"] == 30
    assert config["multiplier"] == 2.0
    assert config["win_rule"] == "eq"
    assert config["win_target"] == 6
    other = await settings_service.get_emoji_game_config("arrow")
    assert other["cooldown"] == 60  # untouched


async def test_emoji_config_unknown_game():
    with pytest.raises(ValueError):
        await settings_service.get_emoji_game_config("cricket")
    with pytest.raises(ValueError):
        await settings_service.update_emoji_game_config("cricket", cooldown=10)


async def test_blackjack_config():
    config = await settings_service.get_blackjack_config()
    assert config["enabled"] is True
    assert config["multiplier"] == 1.0
    await settings_service.update_blackjack_config(multiplier=2.0, minimum_bet=200)
    config = await settings_service.get_blackjack_config()
    assert config["multiplier"] == 2.0
    assert config["minimum_bet"] == 200


async def test_system_taxes_include_emoji_and_blackjack():
    taxes = await settings_service.get_system_taxes()
    assert "emoji" in taxes
    assert "blackjack" in taxes


# ---------- new games single-player ----------


async def test_football_single_win():
    await _fund(A, 1_000_000)
    await cooldown_manager.clear("football", A)
    started = await emoji_service.start_single(A, "football", 10_000)
    # football win on 5 (eq)
    outcome = await emoji_service.settle_single(started["session_id"], 5)
    assert outcome["won"] is True
    assert outcome["payout"] == 25_000  # 10_000 + 10_000*1.5


async def test_football_single_loss():
    await _fund(A, 1_000_000)
    await cooldown_manager.clear("football", A)
    started = await emoji_service.start_single(A, "football", 10_000)
    outcome = await emoji_service.settle_single(started["session_id"], 3)
    assert outcome["won"] is False
    assert outcome["payout"] == 0


async def test_dice_single_win():
    await _fund(A, 1_000_000)
    await cooldown_manager.clear("dice", A)
    started = await emoji_service.start_single(A, "dice", 10_000)
    # dice win on 6 (eq)
    outcome = await emoji_service.settle_single(started["session_id"], 6)
    assert outcome["won"] is True
    assert outcome["payout"] == 30_000  # 10_000 + 10_000*2.0


async def test_slot_single_loss():
    await _fund(A, 1_000_000)
    await cooldown_manager.clear("slot", A)
    started = await emoji_service.start_single(A, "slot", 10_000)
    # value 7 = BAR GRAPE LEMON (no matches) -> loss
    outcome = await emoji_service.settle_single(started["session_id"], 7)
    assert outcome["won"] is False


# ---------- refund on invalid dice ----------


async def test_refund_failed_single_session():
    await _fund(A, 1_000_000)
    started = await emoji_service.start_single(A, "ball", 10_000)
    assert (await _wallet(A)) == 990_000
    # Simulate failed dice (no value) by calling refund_failed_session
    result = await emoji_service.refund_failed_session(started["session_id"], "no_dice_value")
    assert result is not None
    assert result["refunded"] == 10_000
    assert result["players"] == [A]
    assert (await _wallet(A)) == 1_000_000
    txs = await tx_service.get_recent(A, 10)
    assert any(t["type"] == tx_service.EMOJI_GAME_REFUND for t in txs)


async def test_refund_failed_duel_session():
    await _fund(A, 1_000_000)
    await _fund(B, 1_000_000)
    lobby = await emoji_service.create_duel(A, "ball", 10_000)
    joined = await emoji_service.join_duel(lobby["game_id"], B)
    assert (await _wallet(A)) == 990_000
    assert (await _wallet(B)) == 990_000
    # Simulate failed dice
    result = await emoji_service.refund_failed_session(joined["session_id"], "invalid_dice_value")
    assert result is not None
    assert result["refunded"] == 20_000
    assert set(result["players"]) == {A, B}
    assert (await _wallet(A)) == 1_000_000
    assert (await _wallet(B)) == 1_000_000
    txs = await tx_service.get_recent(A, 10)
    assert any(t["type"] == tx_service.EMOJI_DUEL_REFUND for t in txs)


# ---------- slot resolver ----------


def test_slot_resolver_triple_bar():
    from services.emoji_games import SlotResultResolver, get_game_def
    game_def = get_game_def("slot")
    config = {"slot_payouts": {"BAR": {"triple": 64, "pair": 4}}}
    # value 1 = BAR BAR BAR
    res = SlotResultResolver.resolve(game_def, 1, 10_000, config)
    assert res["won"] is True
    assert res["multiplier"] == 64
    assert res["payout"] == 10_000 + 640_000


def test_slot_resolver_pair():
    from services.emoji_games import SlotResultResolver, get_game_def
    game_def = get_game_def("slot")
    config = {"slot_payouts": {"BAR": {"triple": 64, "pair": 4}}}
    # value 2 = BAR BAR GRAPE -> pair BAR
    res = SlotResultResolver.resolve(game_def, 2, 10_000, config)
    assert res["won"] is True
    assert res["multiplier"] == 4
    assert res["payout"] == 10_000 + 40_000


def test_slot_resolver_loss():
    from services.emoji_games import SlotResultResolver, get_game_def
    game_def = get_game_def("slot")
    # BAR GRAPE LEMON (no match) -> value 7
    res = SlotResultResolver.resolve(game_def, 7, 10_000, {})
    assert res["won"] is False
    assert res["payout"] == 0
