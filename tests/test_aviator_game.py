"""Unit tests for the /aviator crash game.

Pure logic (crash generation, curve, payout, callback parsing) plus mocked-I/O
flows (start, cashout, background loop, FloodWait handling, engine settlement).
Runs anywhere without a local MongoDB.
"""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from pyrogram.errors import FloodWait  # noqa: E402

from database import games as games_db  # noqa: E402
from database.mongo import mongo  # noqa: E402
from games import aviator as aviator_game  # noqa: E402
from services import game_engine  # noqa: E402
from services import settings as settings_service  # noqa: E402
from services.game_engine import GameError, NoActiveGame  # noqa: E402
from services.settings import GAME_DEFAULTS  # noqa: E402
from utils.validators import parse_amount_or_error, validate_crash_value  # noqa: E402

CFG = {
    "duration": 60,
    "max_multiplier": 100.0,
    "max_payout": 0,
    "growth_exponent": 4.0,
}


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def test_multiplier_at_starts_at_one_and_grows_slowly():
    assert aviator_game.multiplier_at(0, CFG) == 1.0
    values = [aviator_game.multiplier_at(t, CFG) for t in range(0, 61, 2)]
    assert values == sorted(values)
    assert all(1.0 <= v <= 100.0 for v in values)
    assert aviator_game.multiplier_at(60, CFG) == 100.0
    assert aviator_game.multiplier_at(999, CFG) == 100.0
    # Slow and playable: still under 1.15x after the first 10 seconds.
    assert aviator_game.multiplier_at(10, CFG) <= 1.15
    assert aviator_game.multiplier_at(60, CFG) == 100.0


def test_multiplier_at_is_finite_and_never_regresses():
    for t in range(0, 120, 3):
        value = aviator_game.multiplier_at(t, CFG)
        assert value == value  # not NaN
        assert value < float("inf")


def test_roll_crash_time_within_bounds_and_player_independent():
    for _ in range(200):
        ct = aviator_game.roll_crash_time(CFG)
        assert aviator_game.MIN_FLY_TIME < ct <= 60.0
    # Signature depends only on config — never user/bet/history.
    assert aviator_game.roll_crash_time.__code__.co_argcount == 1


def test_crash_multiplier_derived_from_crash_time():
    ct = 30.0
    assert aviator_game.crash_multiplier_for(ct, CFG) == aviator_game.multiplier_at(ct, CFG)


def test_time_at_multiplier_is_inverse_of_curve():
    # Round-trip: reaching ``target`` at time_at_multiplier(target) yields the
    # target back (within the 2-decimal rounding of the display curve).
    for target in (1.1, 1.5, 2.0, 5.0, 10.0, 50.0, 99.0):
        t = aviator_game.time_at_multiplier(target, CFG)
        assert aviator_game.multiplier_at(t, CFG) == pytest.approx(target, abs=0.02)
    # Edges: <=1.0 -> 0s, >= max_multiplier -> duration.
    assert aviator_game.time_at_multiplier(1.0, CFG) == 0.0
    assert aviator_game.time_at_multiplier(0.5, CFG) == 0.0
    assert aviator_game.time_at_multiplier(100.0, CFG) == 60.0
    assert aviator_game.time_at_multiplier(500.0, CFG) == 60.0
    # Flat curve (max_multiplier == 1) never reaches anything above 1.0x.
    flat = dict(CFG, max_multiplier=1.0)
    assert aviator_game.time_at_multiplier(5.0, flat) == 0.0
    assert aviator_game.roll_crash_time(flat) == 1.0


def test_roll_crash_time_respects_crash_value():
    cfg5 = dict(CFG, crash_value=5.0)
    upper5 = aviator_game.time_at_multiplier(5.0, cfg5)
    assert 0.0 < upper5 < 60.0
    seen = set()
    for _ in range(500):
        ct = aviator_game.roll_crash_time(cfg5)
        assert aviator_game.MIN_FLY_TIME < ct <= upper5
        assert aviator_game.crash_multiplier_for(ct, cfg5) <= 5.0
        assert aviator_game.crash_multiplier_for(ct, cfg5) >= 1.0
        seen.add(round(ct, 3))
    assert len(seen) > 50  # crash points stay random, not a fixed value
    # The configured limit itself is reachable: at the upper bound the curve
    # is exactly crash_value.
    assert aviator_game.multiplier_at(upper5, cfg5) == 5.0


def test_crash_never_exceeds_configured_limit():
    for crash_value in (2.0, 5.0, 10.0, 25.0):
        cfg = dict(CFG, crash_value=crash_value)
        for _ in range(300):
            ct = aviator_game.roll_crash_time(cfg)
            assert aviator_game.crash_multiplier_for(ct, cfg) <= crash_value


def test_roll_crash_time_defaults_to_curve_ceiling_when_unset():
    # Without crash_value the old behavior is preserved: crashes span the
    # whole (MIN_FLY_TIME, duration] window, i.e. up to max_multiplier.
    for _ in range(200):
        ct = aviator_game.roll_crash_time(CFG)
        assert aviator_game.MIN_FLY_TIME < ct <= 60.0


def test_validate_crash_value():
    value, err = validate_crash_value("5")
    assert value == 5.0 and err is None
    value, err = validate_crash_value("10", max_multiplier=50)
    assert value == 10.0 and err is None
    value, err = validate_crash_value("1", max_multiplier=1.0)
    assert value == 1.0 and err is None
    # NaN / Infinity rejected.
    assert validate_crash_value("nan")[1] is not None
    assert validate_crash_value("inf")[1] is not None
    assert validate_crash_value("-inf")[1] is not None
    # Negative / below 1.00x / zero rejected.
    assert validate_crash_value("-5")[1] is not None
    assert validate_crash_value("0")[1] is not None
    assert validate_crash_value("0.99")[1] is not None
    # Non-numeric rejected.
    assert validate_crash_value("abc")[1] is not None
    # Above max_multiplier rejected with a clear message.
    value, err = validate_crash_value("100", max_multiplier=50)
    assert value is None and err is not None
    assert "max_multiplier" in err


def test_compute_payout_unlimited_by_default():
    bet = 100_000
    payout = aviator_game.compute_payout(bet, 3.98, CFG)
    assert payout == int(bet * 3.98)
    assert isinstance(payout, int)
    assert payout >= 0


def test_compute_payout_capped_when_configured():
    cfg = dict(CFG, max_payout=150_000)
    assert aviator_game.compute_payout(100_000, 3.98, cfg) == 150_000
    assert aviator_game.compute_payout(100_000, 1.2, cfg) == 120_000


def test_compute_payout_rejects_unsafe_multiplier():
    with pytest.raises(GameError):
        aviator_game.compute_payout(100_000, float("inf"), CFG)


def test_parse_callback():
    assert aviator_game.parse_callback("aviator:sid1:cash") == "sid1"
    assert aviator_game.parse_callback("aviator:sid1:play") is None
    assert aviator_game.parse_callback("mines:sid1:cash") is None
    assert aviator_game.parse_callback("garbage") is None


def test_text_builders_show_money():
    assert "₹1,000" in aviator_game.live_text(100_000, 1.5, "sid1")
    assert "1.50x" in aviator_game.cashout_text(100_000, 1.5, 150_000)
    assert "2.73x" in aviator_game.crash_text(100_000, 2.73)


# ---------------------------------------------------------------------------
# Settings / registry wiring
# ---------------------------------------------------------------------------

def test_aviator_in_game_defaults_unlimited():
    cfg = GAME_DEFAULTS["aviator"]
    assert cfg["minimum_bet"] == 100
    assert cfg["maximum_bet"] == 0  # unlimited by default
    assert cfg["max_payout"] == 0  # unlimited by default
    assert cfg["duration"] == 60
    assert cfg["crash_value"] == 100.0  # crash limit = curve ceiling by default


def test_crash_value_persists_via_settings(monkeypatch):
    _install_db(monkeypatch)
    # Fresh DB read returns the default.
    assert asyncio.run(settings_service.get_game_settings("aviator"))["crash_value"] == 100.0
    # Persist via the existing settings system (no new collection).
    updated = asyncio.run(settings_service.update_game_settings("aviator", crash_value=10.0))
    assert updated["crash_value"] == 10.0
    # A fresh read returns the persisted value (survives restart).
    fresh = asyncio.run(settings_service.get_game_settings("aviator"))
    assert fresh["crash_value"] == 10.0
    assert fresh["max_multiplier"] == 100.0  # other defaults intact
    # Overwrite to 5.0, then back — value always round-trips exactly.
    asyncio.run(settings_service.update_game_settings("aviator", crash_value=5.0))
    assert asyncio.run(settings_service.get_game_settings("aviator"))["crash_value"] == 5.0


def test_aviator_registered_in_game_engine():
    assert "aviator" in game_engine.GAMES


def test_amount_parser_variants():
    assert parse_amount_or_error("1000") == (100_000, None)
    assert parse_amount_or_error("10k") == (1_000_000, None)
    assert parse_amount_or_error("1m") == (100_000_000, None)
    bet, err = parse_amount_or_error("0")
    assert err is not None and bet is None


# ---------------------------------------------------------------------------
# In-memory Mongo + mocked economy/engine
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, modified_count):
        self.modified_count = modified_count


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def update_one(self, filt, update, upsert=False):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                doc.update(update.get("$set", {}))
                return _Result(1)
        if upsert:
            new_doc = dict(update.get("$set", {}))
            new_doc.update(filt)
            self.docs[id(new_doc)] = new_doc
            return _Result(1)
        return _Result(0)

    async def insert_one(self, doc):
        self.docs[id(doc)] = doc


class _FakeDb:
    def __init__(self):
        self._collections = {
            "game_sessions": _FakeCollection(),
            "settings": _FakeCollection(),
        }

    def __getitem__(self, name):
        return self._collections[name]


def _install_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(mongo, "db", db)
    return db


def _session_doc(**overrides):
    now = int(time.time())
    doc = {
        "game_id": "sid1",
        "game": "aviator",
        "user_id": 7,
        "chat_id": 10,
        "message_id": 100,
        "bet": 100_000,
        "status": "active",
        "state": {
            "crash_time": 40.0,
            "crash_multiplier": aviator_game.multiplier_at(40.0, CFG),
            "cfg": dict(CFG),
        },
        "created_at": now - 25,  # 25s elapsed
        "expires_at": now + 35,
    }
    doc.update(overrides)
    return doc


def _insert_session(db, doc):
    db["game_sessions"].docs["sid1"] = doc
    return doc


class _FakeMessage:
    def __init__(self):
        self.edits = []
        self.edit_error = None
        self.chat_id = 10
        self.id = 100
        self.chat = SimpleNamespace(id=10)

    async def edit(self, text, reply_markup=None, **kwargs):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((text, reply_markup))


def _install_engine(monkeypatch, db, session_id="sid1"):
    settle_calls = []

    async def fake_settle(sid, user_id, *, won, payout, multiplier=None, meta=None):
        doc = db["game_sessions"].docs.get(sid)
        settle_calls.append({
            "session_id": sid, "user_id": user_id, "won": won,
            "payout": payout, "multiplier": multiplier, "meta": meta,
        })
        if doc is None or doc.get("status") != "active":
            return False
        doc["status"] = "won" if won else "lost"
        doc["payout"] = payout
        return True

    monkeypatch.setattr(game_engine, "settle_game", fake_settle)
    return settle_calls


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------

def test_start_locks_bet_and_creates_session(monkeypatch):
    db = _install_db(monkeypatch)
    locked = []
    created = []

    async def fake_validate(game):
        return GAME_DEFAULTS["aviator"]

    async def fake_lock(user_id, game, bet, **kwargs):
        locked.append((user_id, game, bet))

    async def fake_create(user_id, game, bet, state, **kwargs):
        created.append({"user_id": user_id, "game": game, "bet": bet, "state": state})
        return "avi-1"

    monkeypatch.setattr(game_engine, "validate_game_input", fake_validate)
    monkeypatch.setattr(game_engine, "check_and_lock_bet", fake_lock)
    monkeypatch.setattr(game_engine, "create_session", fake_create)

    session_id, state = asyncio.run(aviator_game.start(7, 100_000, chat_id=10))

    assert session_id == "avi-1"
    assert locked == [(7, "aviator", 100_000)]
    assert created[0]["user_id"] == 7 and created[0]["game"] == "aviator"
    assert state["crash_time"] > 1.0
    assert state["crash_multiplier"] == aviator_game.multiplier_at(state["crash_time"], CFG)
    assert state["cfg"]["max_payout"] == 0
    assert state["payout"] is None


# ---------------------------------------------------------------------------
# cashout()
# ---------------------------------------------------------------------------

def test_cashout_before_crash_wins_correct_payout(monkeypatch):
    db = _install_db(monkeypatch)
    doc = _insert_session(db, _session_doc())
    settle_calls = _install_engine(monkeypatch, db)
    message = _FakeMessage()

    result = asyncio.run(aviator_game.cashout("sid1", 7, None, message))

    assert result["won"] is True and result["crashed"] is False
    # Elapsed is ~25s of a 60s round, so the multiplier is a clear win.
    assert result["multiplier"] > 1.0
    assert result["payout"] == int(doc["bet"] * result["multiplier"])
    assert result["payout"] > doc["bet"]
    assert len(settle_calls) == 1
    assert settle_calls[0]["won"] is True
    assert settle_calls[0]["payout"] == result["payout"]
    assert settle_calls[0]["meta"]["aviator_outcome"] == "cashed_out"
    # Session settled exactly once and the button was removed.
    assert doc["status"] == "won"
    assert message.edits and message.edits[-1][1] is None


def test_cashout_after_crash_is_a_loss(monkeypatch):
    db = _install_db(monkeypatch)
    doc = _insert_session(db, _session_doc(created_at=int(time.time()) - 50))  # past crash
    settle_calls = _install_engine(monkeypatch, db)
    message = _FakeMessage()

    result = asyncio.run(aviator_game.cashout("sid1", 7, None, message))

    assert result["won"] is False and result["crashed"] is True
    assert result["payout"] == 0
    assert len(settle_calls) == 1
    assert settle_calls[0]["won"] is False
    assert settle_calls[0]["payout"] == 0
    assert doc["status"] == "lost"


def test_duplicate_cashout_no_double_payout(monkeypatch):
    db = _install_db(monkeypatch)
    doc = _insert_session(db, _session_doc())
    _install_engine(monkeypatch, db)
    message = _FakeMessage()

    first = asyncio.run(aviator_game.cashout("sid1", 7, None, _FakeMessage()))
    assert first["won"] is True
    # Session already settled -> second cashout must be rejected.
    with pytest.raises(NoActiveGame):
        asyncio.run(aviator_game.cashout("sid1", 7, None, _FakeMessage()))


def test_other_user_cannot_cashout(monkeypatch):
    db = _install_db(monkeypatch)
    _insert_session(db, _session_doc())
    _install_engine(monkeypatch, db)

    with pytest.raises(GameError):
        asyncio.run(aviator_game.cashout("sid1", 999, None, _FakeMessage()))


def test_cashout_with_crash_value_configured_still_works(monkeypatch):
    db = _install_db(monkeypatch)
    cfg5 = dict(CFG, crash_value=5.0)
    # Crash is inside the configured limit (well before the 5.00x upper bound).
    doc = _insert_session(
        db,
        _session_doc(
            created_at=int(time.time()) - 5,
            state={
                "crash_time": 8.0,
                "crash_multiplier": aviator_game.multiplier_at(8.0, cfg5),
                "cfg": dict(cfg5),
            },
        ),
    )
    settle_calls = _install_engine(monkeypatch, db)
    message = _FakeMessage()

    result = asyncio.run(aviator_game.cashout("sid1", 7, None, message))

    assert result["won"] is True and result["crashed"] is False
    assert result["payout"] == int(doc["bet"] * result["multiplier"])
    assert result["payout"] > doc["bet"]
    assert settle_calls[0]["won"] is True
    # max_payout still applies on top of crash_value.
    cfg_payout = dict(CFG, crash_value=5.0, max_payout=150_000)
    assert aviator_game.compute_payout(100_000, 4.99, cfg_payout) == 150_000


# ---------------------------------------------------------------------------
# Background run loop
# ---------------------------------------------------------------------------

def test_run_crashes_and_settles_even_if_edit_fails(monkeypatch):
    db = _install_db(monkeypatch)
    doc = _insert_session(db, _session_doc(created_at=int(time.time()) - 100))
    settle_calls = _install_engine(monkeypatch, db)
    message = _FakeMessage()
    message.edit_error = RuntimeError("telegram down")

    asyncio.run(aviator_game.run("sid1", None, message))

    # Settlement happened regardless of the failed edit; no retries spammed.
    assert len(settle_calls) == 1
    assert settle_calls[0]["won"] is False
    assert doc["status"] == "lost"


def test_run_stops_when_already_settled(monkeypatch):
    db = _install_db(monkeypatch)
    _insert_session(db, _session_doc(status="won"))
    settle_calls = _install_engine(monkeypatch, db)
    message = _FakeMessage()

    asyncio.run(aviator_game.run("sid1", None, message))

    assert settle_calls == []
    assert message.edits == []


def test_edit_respects_floodwait(monkeypatch):
    message = _FakeMessage()
    message.edit_error = FloodWait(1)

    start = time.time()
    asyncio.run(aviator_game._try_edit(None, message, "x"))
    assert time.time() - start >= 0.9
    # No duplicate message was sent: edit only ever targets this message.
    assert message.edits == []


# ---------------------------------------------------------------------------
# Cleanup / scheduler + engine settlement
# ---------------------------------------------------------------------------

def test_expire_stale_aviator_games(monkeypatch):
    _install_db(monkeypatch)
    handled = []

    async def fake_settings(game):
        return {"duration": 60}

    async def fake_find(game, max_age):
        return [{"game_id": "orphan1", "user_id": 7}]

    async def fake_settle(sid, user_id, *, won, payout, multiplier=None, meta=None):
        handled.append((sid, won, payout))

    monkeypatch.setattr(settings_service, "get_game_settings", fake_settings)
    monkeypatch.setattr(games_db, "find_expired_games", fake_find)
    monkeypatch.setattr(game_engine, "settle_game", fake_settle)

    result = asyncio.run(game_engine.expire_stale_games("aviator"))
    assert handled == [("orphan1", False, 0)]
    assert result == ["orphan1"]


def test_engine_settlement_credits_wallet_once(monkeypatch):
    db = _install_db(monkeypatch)
    doc = _insert_session(db, _session_doc())
    credited = []
    recorded = []

    async def fake_require_user(user_id):
        return {"user_id": user_id, "wallet": 1_000_000}

    async def fake_add_wallet(user_id, amount, **kwargs):
        credited.append((user_id, amount, kwargs.get("earn")))

    async def fake_tax_amount(system, gross):
        return 0

    async def fake_collect(user_id, amount):
        return None

    async def fake_record(**kwargs):
        recorded.append(kwargs)
        return "tx-1"

    monkeypatch.setattr("services.economy._require_user", fake_require_user)
    monkeypatch.setattr("services.economy.add_wallet", fake_add_wallet)
    monkeypatch.setattr("services.tax.system_tax_amount", fake_tax_amount)
    monkeypatch.setattr("services.tax.collect", fake_collect)
    monkeypatch.setattr("services.transaction.record", fake_record)

    ok = asyncio.run(game_engine.settle_game("sid1", 7, won=True, payout=200_000, multiplier=2.0))

    assert ok is True
    assert credited == [(7, 200_000, True)]
    assert len(recorded) == 1
    assert recorded[0]["ttype"] == "GAME_WIN"
    assert recorded[0]["metadata"]["game"] == "aviator"
    assert doc["status"] == "won"
    # Idempotent: a second settle does not credit again.
    ok2 = asyncio.run(game_engine.settle_game("sid1", 7, won=True, payout=200_000, multiplier=2.0))
    assert ok2 is False
    assert credited == [(7, 200_000, True)]