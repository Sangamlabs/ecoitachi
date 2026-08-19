"""Unit tests for leaderboard exclusions and the /clearlb engine.

Uses a fake MongoDB and monkeypatched economy/transaction services so these
run anywhere (no local MongoDB required).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from config import config  # noqa: E402
from database.mongo import mongo  # noqa: E402
from services import economy  # noqa: E402
from services import leaderboard  # noqa: E402
from services import transaction as tx_service  # noqa: E402


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._it = None

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    def sort(self, *args, **kwargs):
        # leaderboard sorts by a single [field, -1] pair
        key = args[0][0][0]
        desc = args[0][0][1] == -1
        self._docs = sorted(self._docs, key=lambda d: d.get(key, 0), reverse=desc)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *args, **kwargs):
        query = args[0] if args else {}
        matched = []
        for doc in self._docs:
            ok = True
            for key, cond in query.items():
                value = doc.get(key)
                if isinstance(cond, dict) and "$ne" in cond:
                    if value == cond["$ne"]:
                        ok = False
                        break
                elif cond is False:
                    if value is False:
                        continue
                    if value is None:
                        # $ne-True semantics: missing field is NOT True
                        continue
                    ok = False
                    break
                elif cond is True:
                    if not value:
                        ok = False
                        break
                else:
                    if value != cond:
                        ok = False
                        break
            if ok:
                matched.append(doc)
        return _FakeCursor(matched)


class _FakeDb:
    def __init__(self, collections: dict[str, list]):
        self._collections = {
            name: _FakeCollection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self._collections[name]


def _install_db(monkeypatch, collections: dict[str, list]):
    monkeypatch.setattr(mongo, "db", _FakeDb(collections))


_USERS = [
    {"user_id": 1, "wallet": 1000, "bank": 0, "monthly_earnings": 50, "is_banned": False},
    {"user_id": 2, "wallet": 2000, "bank": 0, "monthly_earnings": 40, "is_banned": False},
    # leaderboard-excluded: must never appear
    {"user_id": 3, "wallet": 99999, "bank": 0, "monthly_earnings": 9999, "is_banned": False, "leaderboard_excluded": True},
    # banned: must never appear
    {"user_id": 4, "wallet": 88888, "bank": 0, "monthly_earnings": 8888, "is_banned": True},
    # legacy doc: missing leaderboard_excluded should still be eligible
    {"user_id": 5, "wallet": 3000, "bank": 0, "monthly_earnings": 30, "is_banned": False},
]


def test_top_net_worth_excludes_excluded_and_banned(monkeypatch):
    _install_db(monkeypatch, {"users": _USERS, "stocks": [], "assets": []})

    class _EmptyStocks:
        async def get_user_holdings(self, user_id):
            return []

        async def get_asset(self, symbol):
            return None

    monkeypatch.setattr(leaderboard, "stocks_db", _EmptyStocks())
    import services.assets as assets_svc

    monkeypatch.setattr(assets_svc, "live_asset_value", _async_lambda(0))
    monkeypatch.setattr("database.loans.get_outstanding", _async_lambda(0))

    result = asyncio.run(leaderboard.top_net_worth(10))
    ids = {u["user_id"] for u in result}
    assert 3 not in ids and 4 not in ids
    assert ids == {1, 2, 5}


def test_top_monthly_excludes_excluded_and_banned(monkeypatch):
    _install_db(monkeypatch, {"users": _USERS})
    result = asyncio.run(leaderboard.top_monthly(10))
    ids = [u["user_id"] for u in result]
    assert ids == [1, 2, 5]  # sorted by monthly_earnings desc


def test_top_bank_excludes_excluded_and_banned(monkeypatch):
    _install_db(monkeypatch, {"users": _USERS})
    result = asyncio.run(leaderboard.top_bank(10))
    ids = {u["user_id"] for u in result}
    assert 3 not in ids and 4 not in ids


# ---------------------------------------------------------------------------
# apply_clearlb
# ---------------------------------------------------------------------------

def _async_lambda(value):
    async def _f(*args, **kwargs):
        return value

    return _f


def test_apply_clearlb_deducts_and_audits(monkeypatch):
    _install_db(monkeypatch, {"users": _USERS})

    monkeypatch.setattr(leaderboard, "top_net_worth", _async_lambda(_USERS[:2]))
    removed = []
    recorded = []

    async def fake_get_balance(user_id):
        return {"wallet": next(u["wallet"] for u in _USERS if u["user_id"] == user_id), "bank": 0}

    async def fake_admin_remove(user_id, amount, actor_id):
        removed.append((user_id, amount, actor_id))

    async def fake_record(**kwargs):
        recorded.append(kwargs)
        return f"tx-{kwargs['user_id']}"

    monkeypatch.setattr(economy, "get_balance", fake_get_balance)
    monkeypatch.setattr(economy, "admin_remove", fake_admin_remove)
    monkeypatch.setattr(tx_service, "record", fake_record)

    old_owner = config.OWNER_ID
    config.OWNER_ID = 2  # treat user 2 as the owner to test the skip
    try:
        result = asyncio.run(leaderboard.apply_clearlb(amount=500, user_count=2, actor_id=99))
    finally:
        config.OWNER_ID = old_owner

    assert [r[0] for r in removed] == [1]
    assert result["skipped"][0]["reason"] == "owner"
    assert len(recorded) == 1
    assert recorded[0]["ttype"] == "ADMIN_REMOVE"
    assert recorded[0]["metadata"]["reason"] == "clearlb"


def test_apply_clearlb_skips_insufficient(monkeypatch):
    _install_db(monkeypatch, {"users": _USERS})
    monkeypatch.setattr(leaderboard, "top_net_worth", _async_lambda(_USERS[:1]))

    async def fake_get_balance(user_id):
        return {"wallet": 100, "bank": 0}

    async def fake_admin_remove(user_id, amount, actor_id):
        raise economy.InsufficientBalance(amount, 100)

    monkeypatch.setattr(economy, "get_balance", fake_get_balance)
    monkeypatch.setattr(economy, "admin_remove", fake_admin_remove)
    monkeypatch.setattr(tx_service, "record", _async_lambda("never"))

    result = asyncio.run(leaderboard.apply_clearlb(amount=500, user_count=1, actor_id=99))
    assert result["done"] == []
    assert result["skipped"][0]["reason"].startswith("Insufficient balance")


def test_apply_clearlb_rejects_bad_input(monkeypatch):
    _install_db(monkeypatch, {"users": _USERS})
    with pytest.raises(ValueError):
        asyncio.run(leaderboard.apply_clearlb(amount=0, user_count=1, actor_id=1))
    with pytest.raises(ValueError):
        asyncio.run(leaderboard.apply_clearlb(amount=100, user_count=0, actor_id=1))