"""Tests for Global Battle Currency conversion."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import global_battle as gb_db, users as users_db
from database.mongo import mongo
from services.global_battle import currency as currency_service
from services import economy, transaction as tx_service
from utils.money import MoneyError


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, modified_count, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query, projection=None):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                if projection:
                    filtered = {}
                    for k, v in projection.items():
                        if v == 1:
                            filtered[k] = doc.get(k)
                        elif v == 0:
                            continue
                        else:
                            filtered[k] = doc.get(k)
                    if projection.get("_id") != 0 and "_id" in doc:
                        filtered["_id"] = doc["_id"]
                    return filtered
                return doc
        return None

    async def find_one_and_update(self, filt, update, upsert=False, return_document=False):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                if "$inc" in update:
                    for k, v in update["$inc"].items():
                        doc[k] = doc.get(k, 0) + v
                if "$set" in update:
                    doc.update(update["$set"])
                if return_document:
                    return doc
                return doc
        if upsert:
            new_doc = dict(update.get("$set", {}))
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            new_doc.update(filt)
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    new_doc[k] = new_doc.get(k, 0) + v
            new_doc_id = id(new_doc)
            self.docs[new_doc_id] = new_doc
            if return_document:
                return new_doc
            return new_doc
        return None

    async def update_one(self, filt, update, upsert=False):
        existing = None
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                existing = doc
                break

        if existing:
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    existing[k] = existing.get(k, 0) + v
            if "$set" in update:
                existing.update(update["$set"])
            return _Result(1)
        if upsert:
            new_doc = dict(filt)
            if "$setOnInsert" in update:
                new_doc.update(update["$setOnInsert"])
            if "$set" in update:
                new_doc.update(update["$set"])
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    new_doc[k] = new_doc.get(k, 0) + v
            new_doc_id = id(new_doc)
            self.docs[new_doc_id] = new_doc
            return _Result(1, upserted_id=new_doc_id)
        return _Result(0)

    async def insert_one(self, doc):
        self.docs[id(doc)] = doc

    def find(self, query=None):
        class _Cursor:
            def __init__(self, docs):
                self._docs = [doc for doc in docs if not query or all(doc.get(k) == v for k, v in query.items())]

            def sort(self, key, direction=1):
                reverse = direction == -1
                if isinstance(key, str):
                    self._docs.sort(key=lambda d: d.get(key, ""), reverse=reverse)
                else:
                    self._docs.sort(key=lambda d: d.get(key[0], ""), reverse=reverse)
                return self

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._docs:
                    return self._docs.pop(0)
                raise StopAsyncIteration

        return _Cursor(list(self.docs.values()))

    async def count_documents(self, query):
        count = 0
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                count += 1
        return count


class _FakeDb:
    def __init__(self):
        self._collections = {
            "global_missions": _FakeCollection(),
            "global_mission_progress": _FakeCollection(),
            "global_profiles": _FakeCollection(),
            "users": _FakeCollection(),
            "counters": _FakeCollection(),
            "settings": _FakeCollection(),
            "transactions": _FakeCollection(),
            "admins": _FakeCollection(),
        }

    def __getitem__(self, name):
        return self._collections[name]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(mongo, "db", db)
    import asyncio
    asyncio.run(gb_db.init_missions())
    asyncio.run(db["settings"].insert_one({"key": "global", "starting_balance": 50000, "global_battle": {"rs_per_gb": 100}}))
    yield


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _mock_economy_ok(monkeypatch, wallet=50000, gb_balance=0):
    """Mock economy service to avoid fake DB $gte operator issues."""
    async def fake_remove_wallet(user_id, amount, *, spend=True, from_transaction=None):
        return {"wallet": wallet - amount, "bank": 0}

    async def fake_add_wallet(user_id, amount, *, earn=True, from_transaction=None):
        return {"wallet": wallet + amount, "bank": 0}

    async def fake_get_balance(user_id):
        return {"wallet": wallet, "bank": 0, "net_worth": wallet}

    monkeypatch.setattr("services.global_battle.currency.economy.remove_wallet", fake_remove_wallet)
    monkeypatch.setattr("services.global_battle.currency.economy.add_wallet", fake_add_wallet)
    monkeypatch.setattr("services.global_battle.currency.economy.get_balance", fake_get_balance)
    monkeypatch.setattr("services.global_battle.currency.economy._require_user", AsyncMock(return_value={"user_id": 123, "wallet": wallet}))
    monkeypatch.setattr("services.global_battle.currency.economy.ensure_active", AsyncMock())


# ---------------------------------------------------------------------------
# Conversion Rate
# ---------------------------------------------------------------------------

def test_get_conversion_rate_default():
    rate = asyncio.run(currency_service.get_conversion_rate())
    assert rate == 100


# ---------------------------------------------------------------------------
# GB Balance
# ---------------------------------------------------------------------------

def test_get_gb_balance_default_zero():
    bal = asyncio.run(currency_service.get_gb_balance(123))
    assert bal == 0


def test_add_gb_coins():
    asyncio.run(currency_service.add_gb_coins(123, 500))
    bal = asyncio.run(currency_service.get_gb_balance(123))
    assert bal == 500


def test_remove_gb_coins(monkeypatch):
    # Mock the DB operations for this test
    profile = asyncio.run(gb_db.get_or_create_profile(123))
    profile["gb_coins"] = 500
    
    async def fake_find_one_and_update(filt, update, return_document=False):
        for doc in mongo.db[gb_db.PROFILES].docs.values():
            if doc.get("user_id") == 123 and doc.get("gb_coins", 0) >= 200:
                doc["gb_coins"] -= 200
                doc["updated_at"] = 123
                return doc
        return None
    
    monkeypatch.setattr(mongo.db[gb_db.PROFILES], "find_one_and_update", fake_find_one_and_update)
    
    bal = asyncio.run(currency_service.remove_gb_coins(123, 200))
    assert bal == 300


def test_remove_gb_coins_insufficient():
    asyncio.run(currency_service.add_gb_coins(123, 100))
    with pytest.raises(Exception) as exc:
        asyncio.run(currency_service.remove_gb_coins(123, 200))
    assert "InsufficientBalance" in str(type(exc.value))


# ---------------------------------------------------------------------------
# RS → GB Conversion (with mocked economy)
# ---------------------------------------------------------------------------

def test_convert_rs_to_gb_success(monkeypatch):
    _mock_economy_ok(monkeypatch, wallet=50000)
    
    # Mock transaction recording
    recorded = []
    async def fake_record(**kwargs):
        recorded.append(kwargs)
        return "tx-1"
    import services.transaction as tx_service
    monkeypatch.setattr(tx_service, "record", fake_record)

    user = asyncio.run(users_db.get_or_create_user(123))
    user["wallet"] = 50000

    result = asyncio.run(currency_service.convert_rs_to_gb(123, 10000))
    assert result["rs_spent"] == 10000
    assert result["gb_received"] == 100
    assert result["rate"] == 100
    assert result["new_rs_wallet"] == 40000
    assert result["new_gb_balance"] == 100
    assert len(recorded) == 2  # GB_CONVERT + GB_CONVERT_RS


def test_convert_rs_to_gb_insufficient_rs(monkeypatch):
    _mock_economy_ok(monkeypatch, wallet=5000)
    
    user = asyncio.run(users_db.get_or_create_user(123))
    user["wallet"] = 5000

    with pytest.raises(Exception) as exc:
        asyncio.run(currency_service.convert_rs_to_gb(123, 10000))
    assert "InsufficientBalance" in str(type(exc.value))


def test_convert_rs_to_gb_not_multiple_of_rate(monkeypatch):
    _mock_economy_ok(monkeypatch, wallet=50000)
    
    user = asyncio.run(users_db.get_or_create_user(123))
    user["wallet"] = 50000
    
    # 15050 is not a multiple of 100
    with pytest.raises(Exception) as exc:
        asyncio.run(currency_service.convert_rs_to_gb(123, 15050))
    assert "CurrencyError" in str(type(exc.value))
    assert "multiple of 100 RS" in str(exc.value)


def test_convert_rs_to_gb_negative_amount():
    with pytest.raises(Exception) as exc:
        asyncio.run(currency_service.convert_rs_to_gb(123, -10000))
    assert "MoneyError" in str(type(exc.value))


def test_convert_rs_to_gb_zero_amount():
    with pytest.raises(Exception) as exc:
        asyncio.run(currency_service.convert_rs_to_gb(123, 0))
    assert "MoneyError" in str(type(exc.value))


def test_convert_records_transactions(monkeypatch):
    _mock_economy_ok(monkeypatch, wallet=50000)
    
    recorded = []
    async def fake_record(**kwargs):
        recorded.append(kwargs)
        return "tx-1"
    import services.transaction as tx_service
    monkeypatch.setattr(tx_service, "record", fake_record)

    user = asyncio.run(users_db.get_or_create_user(123))
    user["wallet"] = 50000

    result = asyncio.run(currency_service.convert_rs_to_gb(123, 10000))

    # Check that transaction was recorded
    assert len(recorded) == 2
    types = {tx["ttype"] for tx in recorded}
    assert "GB_CONVERT" in types
    assert "GB_CONVERT_RS" in types


def test_convert_idempotent_on_duplicate(monkeypatch):
    """Running conversion twice with different amounts should both succeed."""
    _mock_economy_ok(monkeypatch, wallet=100000)
    
    user = asyncio.run(users_db.get_or_create_user(123))
    user["wallet"] = 100000
    
    r1 = asyncio.run(currency_service.convert_rs_to_gb(123, 10000))
    r2 = asyncio.run(currency_service.convert_rs_to_gb(123, 20000))

    assert r1["gb_received"] == 100
    assert r2["gb_received"] == 200
    assert r1["tx_id"] != r2["tx_id"]