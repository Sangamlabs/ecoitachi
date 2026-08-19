"""Unit tests for the /clear full-economy reset and recovery round-trip.

Covers:
  - economy.clear_economy(): wallet + bank + stock/asset holdings + cached
    value fields, while leaving earnings history and loans untouched.
  - security.manual_clear(): dump first, full reset, audit, fresh-DB
    verification, recovery ID.
  - dump -> clear -> restore round-trip reproducing the original economy.

Uses an in-memory fake MongoDB and monkeypatched economy primitives so these
run anywhere (no local MongoDB required).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from database.mongo import mongo  # noqa: E402
from services import economy  # noqa: E402
from services import security  # noqa: E402
from services import settings  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory MongoDB stand-in
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def replace_one(self, filt, doc, upsert=False):
        for key, existing in list(self.docs.items()):
            if all(existing.get(k) == v for k, v in filt.items()):
                self.docs[key] = doc
                return
        if upsert:
            self.docs[id(doc)] = doc

    async def update_one(self, filt, update):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                doc.update(update.get("$set", {}))
                return _Result(1)
        return _Result(0)

    def find(self, query=None):
        q = query or {}
        return _FakeCursor(
            [d for d in self.docs.values() if all(d.get(k) == v for k, v in q.items())]
        )


class _FakeDb:
    def __init__(self, names):
        self._collections = {name: _FakeCollection() for name in names}

    def __getitem__(self, name):
        return self._collections[name]


def _install_db(monkeypatch):
    monkeypatch.setattr(
        mongo,
        "db",
        _FakeDb(
            ["users", "stocks", "assets", "security_dumps", "security_events", "security_recovery"]
        ),
    )


# ---------------------------------------------------------------------------
# Shared in-memory economy state + fake economy primitives
# ---------------------------------------------------------------------------

def _make_state():
    return {
        "wallet": 100_000,
        "bank": 50_000,
        "stocks": {"AAPL": 10},
        "assets": {"gold": {"quantity": 5, "cost": 2_000}},
        "total_earned": 9_999,
        "monthly_earnings": 777,
        "monthly_rank": 3,
    }


def _install_economy(monkeypatch, state):
    async def fake_get_balance(user_id):
        return {"wallet": state["wallet"], "bank": state["bank"]}

    async def fake_get_user_economy_snapshot(user_id):
        return {
            "wallet": int(state["wallet"]),
            "bank": int(state["bank"]),
            "stocks": {k: v for k, v in state["stocks"].items()},
            "assets": {
                k: {"quantity": v["quantity"], "cost": v["cost"]}
                for k, v in state["assets"].items()
            },
        }

    async def fake_set_user_balance(user_id, field, value):
        state[field] = int(value)

    async def fake_set_user_stock(user_id, sym, quantity):
        if quantity is None or int(quantity) <= 0:
            state["stocks"].pop(sym, None)
        else:
            state["stocks"][sym] = int(quantity)

    async def fake_set_user_asset(user_id, aid, quantity):
        if quantity is None or int(quantity) <= 0:
            state["assets"].pop(aid, None)
        else:
            state["assets"][aid] = {"quantity": int(quantity), "cost": 0}

    async def fake_clear_economy(user_id, wallet_value):
        state["wallet"] = int(wallet_value)
        state["bank"] = 0
        state["stocks"] = {}
        state["assets"] = {}

    monkeypatch.setattr(economy, "get_balance", fake_get_balance)
    monkeypatch.setattr(economy, "get_user_economy_snapshot", fake_get_user_economy_snapshot)
    monkeypatch.setattr(economy, "set_user_balance", fake_set_user_balance)
    monkeypatch.setattr(economy, "set_user_stock", fake_set_user_stock)
    monkeypatch.setattr(economy, "set_user_asset", fake_set_user_asset)
    monkeypatch.setattr(economy, "clear_economy", fake_clear_economy)


def _install_user(monkeypatch, state, user_id=7):
    async def fake_user_exists(uid):
        return uid == user_id

    async def fake_get_user(uid):
        if uid != user_id:
            return None
        return {
            "user_id": uid,
            "wallet": state["wallet"],
            "bank": state["bank"],
            "total_earned": state["total_earned"],
            "monthly_earnings": state["monthly_earnings"],
            "monthly_rank": state["monthly_rank"],
        }

    monkeypatch.setattr(security.users_db, "user_exists", fake_user_exists)
    monkeypatch.setattr(security.users_db, "get_user", fake_get_user)


def _install_verification(monkeypatch, state, mismatch=False):
    async def fake_outstanding(user_id):
        return 0

    async def fake_net_worth(user):
        value = state["wallet"] + state["bank"]
        if mismatch:
            value += 1
        return value

    monkeypatch.setattr("database.loans.get_outstanding", fake_outstanding)
    monkeypatch.setattr("services.leaderboard.net_worth", fake_net_worth)


# ---------------------------------------------------------------------------
# economy.clear_economy()
# ---------------------------------------------------------------------------

def test_clear_economy_resets_full_economy(monkeypatch):
    _install_db(monkeypatch)
    balance_calls = []
    stock_calls = []
    asset_calls = []
    field_calls = []
    refresh_calls = []

    async def fake_set_user_balance(user_id, field, value):
        balance_calls.append((user_id, field, value))

    async def fake_set_user_stock(user_id, sym, quantity):
        stock_calls.append((user_id, sym, quantity))

    async def fake_set_user_asset(user_id, aid, quantity):
        asset_calls.append((user_id, aid, quantity))

    async def fake_set_user_field(user_id, field, value):
        field_calls.append((user_id, field, value))

    async def fake_holdings(user_id):
        return [{"symbol": "AAPL"}, {"symbol": "TSLA"}]

    async def fake_asset_holdings(user_id):
        return [{"asset_id": "gold"}]

    async def fake_refresh(user_id):
        refresh_calls.append(user_id)

    monkeypatch.setattr(economy, "set_user_balance", fake_set_user_balance)
    monkeypatch.setattr(economy, "set_user_stock", fake_set_user_stock)
    monkeypatch.setattr(economy, "set_user_asset", fake_set_user_asset)
    monkeypatch.setattr(economy.users_db, "set_user_field", fake_set_user_field)
    monkeypatch.setattr("database.stocks.get_user_holdings", fake_holdings)
    monkeypatch.setattr("database.asset_holdings.get_user_holdings", fake_asset_holdings)
    monkeypatch.setattr("services.assets.refresh_user_asset_value", fake_refresh)

    asyncio.run(economy.clear_economy(7, 50_000))

    assert balance_calls == [(7, "wallet", 50_000), (7, "bank", 0)]
    assert stock_calls == [(7, "AAPL", 0), (7, "TSLA", 0)]
    assert asset_calls == [(7, "gold", 0)]
    assert field_calls == [(7, "stocks_value", 0)]
    assert refresh_calls == [7]


def test_clear_economy_touches_only_economy_fields(monkeypatch):
    """Earnings history / loans are never written by the clear path."""
    _install_db(monkeypatch)
    touched = []

    async def fake_set_user_balance(user_id, field, value):
        touched.append(f"balance:{field}")

    async def fake_set_user_stock(user_id, sym, quantity):
        touched.append(f"stock:{sym}")

    async def fake_set_user_asset(user_id, aid, quantity):
        touched.append(f"asset:{aid}")

    async def fake_set_user_field(user_id, field, value):
        touched.append(f"field:{field}")

    async def fake_refresh(user_id):
        touched.append("refresh:asset_value")

    monkeypatch.setattr(economy, "set_user_balance", fake_set_user_balance)
    monkeypatch.setattr(economy, "set_user_stock", fake_set_user_stock)
    monkeypatch.setattr(economy, "set_user_asset", fake_set_user_asset)
    monkeypatch.setattr(economy.users_db, "set_user_field", fake_set_user_field)
    monkeypatch.setattr("database.stocks.get_user_holdings", _async_list([{"symbol": "A"}]))
    monkeypatch.setattr("database.asset_holdings.get_user_holdings", _async_list([{"asset_id": "g"}]))
    monkeypatch.setattr("services.assets.refresh_user_asset_value", fake_refresh)

    asyncio.run(economy.clear_economy(7, 50_000))

    assert touched == [
        "balance:wallet",
        "balance:bank",
        "stock:A",
        "field:stocks_value",
        "asset:g",
        "refresh:asset_value",
    ]
    assert all("earn" not in t and "loan" not in t and "rank" not in t for t in touched)


# ---------------------------------------------------------------------------
# security.manual_clear()
# ---------------------------------------------------------------------------

def _install_manual_clear_env(monkeypatch, state, mismatch=False, user_exists=True):
    _install_db(monkeypatch)
    _install_economy(monkeypatch, state)
    _install_user(monkeypatch, state, user_id=7)
    _install_verification(monkeypatch, state, mismatch=mismatch)

    if not user_exists:
        async def no_user(uid):
            return False
        monkeypatch.setattr(security.users_db, "user_exists", no_user)

    monkeypatch.setattr(settings, "get_starting_balance", _async_value(50_000))


def test_manual_clear_resets_full_economy(monkeypatch):
    state = _make_state()
    _install_manual_clear_env(monkeypatch, state)

    ok, msg, recovery_id = asyncio.run(security.manual_clear(999, 7))

    assert ok is True
    assert recovery_id.startswith("DUMP-")

    # Full current-economy reset through the economy engine.
    assert state["wallet"] == 50_000
    assert state["bank"] == 0
    assert state["stocks"] == {}
    assert state["assets"] == {}

    # Earnings history is preserved (only wallet/bank/holdings were written).
    fresh = asyncio.run(security.users_db.get_user(7))
    assert fresh["total_earned"] == 9_999
    assert fresh["monthly_earnings"] == 777
    assert fresh["monthly_rank"] == 3

    # Dump snapshot was captured before the reset.
    dumps = mongo.db["security_dumps"].docs.values()
    dump = next(d for d in dumps if d["dump_id"] == recovery_id)
    assert dump["snapshot"]["wallet"] == 100_000
    assert dump["snapshot"]["bank"] == 50_000
    assert dump["snapshot"]["stocks"] == {"AAPL": 10}
    assert dump["snapshot"]["assets"]["gold"]["quantity"] == 5
    assert dump["status"] == "active"

    # Audit trail: initiated + completed.
    events = list(mongo.db["security_events"].docs.values())
    types = {e["type"] for e in events}
    assert types == {"recovery_clear_initiated", "recovery_balance_cleared"}

    # Recovery state recorded.
    recovery = asyncio.run(security.sec_db.get_recovery_balance(7))
    assert recovery["recovery_balance"] == 50_000
    assert recovery["last_dump_id"] == recovery_id

    # Fresh-DB verification succeeded.
    assert "Verified against fresh database state" in msg


def test_manual_clear_verification_mismatch_warns(monkeypatch):
    state = _make_state()
    _install_manual_clear_env(monkeypatch, state, mismatch=True)

    ok, msg, recovery_id = asyncio.run(security.manual_clear(999, 7))

    assert ok is True
    assert recovery_id.startswith("DUMP-")
    assert "WARNING: post-clear verification mismatch" in msg
    assert state["wallet"] == 50_000 and state["bank"] == 0


def test_manual_clear_unknown_user_no_reset(monkeypatch):
    state = _make_state()
    _install_manual_clear_env(monkeypatch, state, user_exists=False)

    ok, msg, recovery_id = asyncio.run(security.manual_clear(999, 999))

    assert ok is False
    assert recovery_id is None
    assert "not found" in msg
    assert state["wallet"] == 100_000 and state["bank"] == 50_000


# ---------------------------------------------------------------------------
# dump -> clear -> restore round-trip
# ---------------------------------------------------------------------------

def test_dump_clear_restore_round_trip(monkeypatch):
    state = _make_state()
    _install_manual_clear_env(monkeypatch, state)
    original = {k: dict(v) if isinstance(v, dict) else v for k, v in state.items()}

    ok, _msg, recovery_id = asyncio.run(security.manual_clear(999, 7))
    assert ok is True

    # After a full clear the user is at the fresh-account state.
    assert state["wallet"] == 50_000
    assert state["bank"] == 0
    assert state["stocks"] == {} and state["assets"] == {}

    restored = asyncio.run(security.restore_from_dump(recovery_id, 7))
    assert restored is True

    # The dump reproduces the original economy in full (asset cost is not part
    # of the restore path — only quantity is reproduced, matching production).
    assert state["wallet"] == original["wallet"]
    assert state["bank"] == original["bank"]
    assert state["stocks"] == original["stocks"]
    assert state["assets"]["gold"]["quantity"] == original["assets"]["gold"]["quantity"]
    assert state["total_earned"] == original["total_earned"]

    # Dump is consumed (cannot be restored twice).
    consumed = asyncio.run(security.sec_db.get_dump(recovery_id))
    assert consumed is None
    assert asyncio.run(security.restore_from_dump(recovery_id, 7)) is False


def test_restore_from_unknown_dump_fails(monkeypatch):
    _install_db(monkeypatch)
    assert asyncio.run(security.restore_from_dump("DUMP-NOPE", 7)) is False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _async_value(value):
    async def _f(*args, **kwargs):
        return value
    return _f


def _async_list(items):
    async def _f(*args, **kwargs):
        return list(items)
    return _f
