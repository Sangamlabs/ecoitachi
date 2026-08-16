"""Integration tests for Blackjack (USER VS BOT, two cards each).

Run with:  pytest tests/test_blackjack.py -v
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_blackjack")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("CATBOX_ENABLED", "false")

from database import users as users_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import blackjack as blackjack_service  # noqa: E402
from services import economy, settings as settings_service  # noqa: E402
from services import transaction as tx_service  # noqa: E402
from utils.cooldown import cooldown_manager  # noqa: E402

A, ADMIN = 9601, 1


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
    await db.users.delete_many({"user_id": {"$in": [A]}})
    await db.transactions.delete_many({"user_id": {"$in": [A]}})
    await db.game_cooldowns.delete_many({})
    await db.settings.delete_many({})
    await settings_service.ensure_indexes()
    await settings_service.update_settings(starting_balance=0)
    await users_db.get_or_create_user(A, "user_a", "User A")
    await cooldown_manager.clear("blackjack", A)
    yield
    await mongo.close()


async def _fund(user_id: int, amount: int) -> None:
    await economy.admin_give(user_id, amount, ADMIN)


async def _wallet(user_id: int) -> int:
    return (await economy.get_balance(user_id))["wallet"]


# ---------- pure logic ----------


def test_card_values():
    assert blackjack_service.card_value("2") == 2
    assert blackjack_service.card_value("10") == 10
    assert blackjack_service.card_value("J") == 10
    assert blackjack_service.card_value("Q") == 10
    assert blackjack_service.card_value("K") == 10
    assert blackjack_service.card_value("A") == 11


def test_hand_total():
    assert blackjack_service.hand_total(["A", "K"]) == 21
    assert blackjack_service.hand_total(["10", "7"]) == 17
    assert blackjack_service.hand_total(["A", "A"]) == 12  # 11 + 1
    assert blackjack_service.hand_total(["A", "9"]) == 20  # 11 + 9
    assert blackjack_service.hand_total(["A", "A", "A"]) == 13  # 11 + 1 + 1


def test_evaluate_win():
    config = {"multiplier": 1.0}
    result = blackjack_service.evaluate(["A", "K"], ["10", "5"], 10_000, config)
    assert result["outcome"] == "win"
    assert result["payout"] == 20_000
    assert result["profit"] == 10_000


def test_evaluate_loss():
    config = {"multiplier": 1.0}
    result = blackjack_service.evaluate(["10", "5"], ["A", "9"], 10_000, config)
    assert result["outcome"] == "loss"
    assert result["payout"] == 0


def test_evaluate_draw_refunds():
    config = {"multiplier": 1.0}
    result = blackjack_service.evaluate(["10", "7"], ["J", "7"], 10_000, config)
    assert result["outcome"] == "draw"
    assert result["payout"] == 0


def test_evaluate_multiplier_payout():
    config = {"multiplier": 2.0}
    result = blackjack_service.evaluate(["A", "K"], ["10", "5"], 10_000, config)
    assert result["outcome"] == "win"
    assert result["payout"] == 30_000  # bet + 2x bet


def test_deal_consumes_deck():
    deck = blackjack_service.build_deck()
    hand, deck = blackjack_service.deal(deck, 2)
    assert len(hand) == 2
    assert len(deck) == 50


# ---------- full round flow ----------


async def test_play_round_money_flow():
    await _fund(A, 1_000_000)
    before = await _wallet(A)
    result = await blackjack_service.play(A, 10_000)
    after = await _wallet(A)
    txs = await tx_service.get_recent(A, 10)
    outcome_tx = next(
        (t for t in txs if t["type"] in (
            tx_service.BLACKJACK_WIN, tx_service.BLACKJACK_LOSS, tx_service.BLACKJACK_DRAW
        )),
        None,
    )
    assert outcome_tx is not None
    assert len(result["user_cards"]) == 2
    assert len(result["bot_cards"]) == 2
    assert 4 <= result["user_total"] <= 21
    assert 4 <= result["bot_total"] <= 21
    if result["outcome"] == "win":
        assert after == before - 10_000 + result["payout"]
    elif result["outcome"] == "loss":
        assert after == before - 10_000
    else:
        assert after == before


async def test_blackjack_cooldown_blocks():
    await _fund(A, 1_000_000)
    await blackjack_service.play(A, 10_000)
    with pytest.raises(blackjack_service.BlackjackCooldown):
        await blackjack_service.play(A, 10_000)


async def test_blackjack_bet_limits():
    await _fund(A, 1_000_000)
    await cooldown_manager.clear("blackjack", A)
    with pytest.raises(blackjack_service.BlackjackError):
        await blackjack_service.play(A, 10)  # below default 100
    await cooldown_manager.clear("blackjack", A)
    await settings_service.update_blackjack_config(minimum_bet=100, maximum_bet=500)
    with pytest.raises(blackjack_service.BlackjackError):
        await blackjack_service.play(A, 600)


async def test_blackjack_disabled():
    await _fund(A, 1_000_000)
    await settings_service.update_blackjack_config(enabled=False)
    with pytest.raises(blackjack_service.BlackjackDisabled):
        await blackjack_service.play(A, 10_000)


async def test_blackjack_insufficient_balance():
    await _fund(A, 50)
    with pytest.raises(economy.InsufficientBalance):
        await blackjack_service.play(A, 100)


async def test_blackjack_win_tax_withheld():
    await settings_service.update_system_taxes(blackjack=5.0)
    await cooldown_manager.clear("blackjack", A)
    await _fund(A, 10_000_000)
    result = await blackjack_service.play(A, 100_000)
    if result["outcome"] == "win":
        # payout 200_000, 5% tax -> 190_000 net
        txs = await tx_service.get_recent(A, 10)
        win_tx = next((t for t in txs if t["type"] == tx_service.BLACKJACK_WIN), None)
        assert win_tx is not None
        assert win_tx["metadata"]["tax"] == 10_000
        assert win_tx["metadata"]["gross_payout"] == 200_000
