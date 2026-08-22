"""Tests for Global Battle Profile & Stats Service."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import global_battle as gb_db
from database.mongo import mongo
from services.global_battle import profile as profile_service
from services import settings as settings_service


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
                if "$setOnInsert" in update:
                    pass
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


class _Result:
    def __init__(self, modified_count, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(mongo, "db", db)
    import asyncio
    asyncio.run(gb_db.init_missions())
    asyncio.run(db["settings"].insert_one({"key": "global", "starting_balance": 50000, "global_battle": {"rs_per_gb": 100, "base_hp": 100, "hp_per_stat": 5, "melee_scaling": 1.0, "ability_scaling": 1.0, "durability_scaling": 1.0}}))
    yield


# ---------------------------------------------------------------------------
# XP / Level Tests
# ---------------------------------------------------------------------------

def test_calculate_xp_for_level():
    # Level 1 = 0 XP
    assert asyncio.run(profile_service.calculate_xp_for_level(1)) == 0
    # Level 2 = base_xp (1000)
    assert asyncio.run(profile_service.calculate_xp_for_level(2)) == 1000
    # Level 3 = 1000 + 1500 = 2500
    assert asyncio.run(profile_service.calculate_xp_for_level(3)) == 2500


def test_get_xp_to_next_level():
    # 0 XP -> level 1, need 1000
    level, need = asyncio.run(profile_service.get_xp_to_next_level(0))
    assert level == 1 and need == 1000

    # 500 XP -> level 1, need 500 more
    level, need = asyncio.run(profile_service.get_xp_to_next_level(500))
    assert level == 1 and need == 500

    # 1000 XP -> level 2, need 1500 more (for level 3)
    level, need = asyncio.run(profile_service.get_xp_to_next_level(1000))
    assert level == 2 and need == 1500


def test_add_xp_no_levelup():
    asyncio.run(gb_db.get_or_create_profile(123))
    result = asyncio.run(profile_service.add_xp(123, 500))
    assert result["leveled_up"] is False
    assert result["new_level"] == 1
    assert result["total_xp"] == 500


def test_add_xp_with_levelup():
    asyncio.run(gb_db.get_or_create_profile(123))
    result = asyncio.run(profile_service.add_xp(123, 1000))
    assert result["leveled_up"] is True
    assert result["old_level"] == 1
    assert result["new_level"] == 2
    assert result["total_xp"] == 1000


def test_add_xp_multiple_levelups():
    asyncio.run(gb_db.get_or_create_profile(123))
    result = asyncio.run(profile_service.add_xp(123, 5000))
    assert result["leveled_up"] is True
    assert result["new_level"] > 2


# ---------------------------------------------------------------------------
# Stat Calculation Tests
# ---------------------------------------------------------------------------

def test_calculate_max_hp():
    asyncio.run(gb_db.get_or_create_profile(123))
    hp = asyncio.run(profile_service.calculate_max_hp(123))
    assert hp == 100  # base 100 + 0 * 5

    # Add health stat
    asyncio.run(profile_service.add_stat_point(123, "health"))
    hp = asyncio.run(profile_service.calculate_max_hp(123))
    assert hp == 105  # 100 + 1 * 5


def test_calculate_melee_damage():
    asyncio.run(gb_db.get_or_create_profile(123))
    dmg = asyncio.run(profile_service.calculate_melee_damage(123, 10))
    assert dmg == 10  # base 10 + 0 * 1.0

    asyncio.run(profile_service.add_stat_point(123, "melee"))
    dmg = asyncio.run(profile_service.calculate_melee_damage(123, 10))
    assert dmg == 11  # 10 + 1 * 1.0


def test_calculate_ability_bonus():
    asyncio.run(gb_db.get_or_create_profile(123))
    bonus = asyncio.run(profile_service.calculate_ability_bonus(123))
    assert bonus["crit_chance"] == 0.0
    assert bonus["crit_damage"] == 1.0
    assert bonus["damage_buff"] == 0.0

    asyncio.run(profile_service.add_stat_point(123, "ability"))
    bonus = asyncio.run(profile_service.calculate_ability_bonus(123))
    assert bonus["crit_chance"] > 0.0
    assert bonus["crit_damage"] > 1.0


def test_calculate_effective_durability():
    asyncio.run(gb_db.get_or_create_profile(123))
    dur = asyncio.run(profile_service.calculate_effective_durability(123, 100))
    assert dur == 100  # base 100 + 0 * 1.0

    asyncio.run(profile_service.add_stat_point(123, "durability"))
    dur = asyncio.run(profile_service.calculate_effective_durability(123, 100))
    assert dur == 101  # 100 + 1 * 1.0


# ---------------------------------------------------------------------------
# Stat Point Tests
# ---------------------------------------------------------------------------

def test_add_stat_point():
    asyncio.run(gb_db.get_or_create_profile(123))
    result = asyncio.run(profile_service.add_stat_point(123, "health"))
    assert result["stat"] == "health"
    assert result["new_value"] == 1

    result = asyncio.run(profile_service.add_stat_point(123, "melee"))
    assert result["stat"] == "melee"
    assert result["new_value"] == 1

    # Second point
    result = asyncio.run(profile_service.add_stat_point(123, "health"))
    assert result["new_value"] == 2


def test_add_stat_point_invalid():
    asyncio.run(gb_db.get_or_create_profile(123))
    with pytest.raises(ValueError):
        asyncio.run(profile_service.add_stat_point(123, "invalid_stat"))


# ---------------------------------------------------------------------------
# Battle Rewards
# ---------------------------------------------------------------------------

def test_grant_battle_rewards():
    asyncio.run(gb_db.get_or_create_profile(123))
    result = asyncio.run(profile_service.grant_battle_rewards(
        123, xp=100, gb_coins=50, stat_points={"health": 1, "melee": 2}
    ))
    assert "xp" in result
    assert "gb_coins" in result
    assert result["gb_coins"] == 50
    assert "stat_points" in result
    assert result["stat_points"]["health"] == 1
    assert result["stat_points"]["melee"] == 2

    # Verify stats applied
    profile = asyncio.run(gb_db.get_profile(123))
    assert profile["health_stat"] == 1
    assert profile["melee_stat"] == 2