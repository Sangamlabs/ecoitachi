"""Tests for Global Battle Missions system.

Pure logic plus mocked-I/O flows. Runs anywhere without a local MongoDB.
"""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import global_battle as gb_db
from database.mongo import mongo
from services.global_battle import missions as missions_service


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
                    # Apply projection
                    filtered = {}
                    for k, v in projection.items():
                        if v == 1:
                            filtered[k] = doc.get(k)
                        elif v == 0:
                            continue
                        else:
                            filtered[k] = doc.get(k)
                    # Always include _id unless explicitly excluded
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
            # Apply $setOnInsert fields
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
        # Check if document exists
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

    async def delete_one(self, filt):
        to_delete = [k for k, v in self.docs.items() if all(v.get(kk) == vv for kk, vv in filt.items())]
        for k in to_delete:
            del self.docs[k]
        return _Result(len(to_delete))

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
            "admins": _FakeCollection(),
        }

    def __getitem__(self, name):
        return self._collections[name]


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    db = _FakeDb()
    monkeypatch.setattr(mongo, "db", db)
    asyncio.run(gb_db.init_missions())
    yield


# ---------------------------------------------------------------------------
# Mission Initialization
# ---------------------------------------------------------------------------

def test_init_missions_creates_10_missions():
    # Create a fresh DB for this test to verify initial insertion
    fresh_db = _FakeDb()
    import database.mongo as mongo_module
    original_db = mongo_module.mongo.db
    mongo_module.mongo.db = fresh_db
    try:
        inserted = asyncio.run(gb_db.init_missions())
        assert inserted == 10  # All 10 missions inserted
    finally:
        mongo_module.mongo.db = original_db


def test_get_all_missions_returns_all():
    missions = asyncio.run(gb_db.get_all_missions())
    assert len(missions) == 10
    mission_ids = {m["mission_id"] for m in missions}
    expected = {"daily", "weekly", "monthly", "pay", "deposit", "withdraw", "stock", "buystock", "assets", "game"}
    assert mission_ids == expected


# ---------------------------------------------------------------------------
# Mission Progress
# ---------------------------------------------------------------------------

def test_increment_mission_progress():
    progress = asyncio.run(gb_db.increment_mission_progress(123, "daily"))
    assert progress["user_id"] == 123
    assert progress["mission_id"] == "daily"
    assert progress["progress"] == 1
    assert progress["completed"] is False

    # Second increment
    progress = asyncio.run(gb_db.increment_mission_progress(123, "daily"))
    assert progress["progress"] == 2


def test_complete_mission_marks_done():
    asyncio.run(gb_db.increment_mission_progress(123, "daily"))
    completed = asyncio.run(gb_db.complete_mission(123, "daily"))
    assert completed is not None
    assert completed["completed"] is True
    assert completed["completed_at"] > 0

    # Second complete should return None
    completed = asyncio.run(gb_db.complete_mission(123, "daily"))
    assert completed is None


def test_count_completed_missions():
    asyncio.run(gb_db.increment_mission_progress(123, "daily"))
    asyncio.run(gb_db.complete_mission(123, "daily"))
    asyncio.run(gb_db.increment_mission_progress(123, "weekly"))
    asyncio.run(gb_db.complete_mission(123, "weekly"))
    count = asyncio.run(gb_db.count_completed_missions(123))
    assert count == 2

    # Different user
    count = asyncio.run(gb_db.count_completed_missions(456))
    assert count == 0


# ---------------------------------------------------------------------------
# Mission Completion Recording
# ---------------------------------------------------------------------------

def test_record_command_completion_daily():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    progress = asyncio.run(gb_db.get_mission_progress(123, "daily"))
    assert progress is not None
    assert progress["completed"] is True


def test_record_command_completion_pay():
    asyncio.run(missions_service.record_command_completion(123, "pay"))
    progress = asyncio.run(gb_db.get_mission_progress(123, "pay"))
    assert progress is not None
    assert progress["completed"] is True


def test_record_command_completion_unknown_command_ignored():
    asyncio.run(missions_service.record_command_completion(123, "unknowncmd"))
    progress = asyncio.run(gb_db.get_mission_progress(123, "unknowncmd"))
    assert progress is None


def test_record_game_completion():
    asyncio.run(missions_service.record_game_completion(123, "fly"))
    progress = asyncio.run(gb_db.get_mission_progress(123, "game"))
    assert progress is not None
    assert progress["completed"] is True


def test_duplicate_mission_not_double_completed():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    progress = asyncio.run(gb_db.get_mission_progress(123, "daily"))
    assert progress["completed"] is True
    assert progress["progress"] == 1  # Not incremented again


# ---------------------------------------------------------------------------
# Global Event Unlock
# ---------------------------------------------------------------------------

def test_unlock_after_3_missions():
    # Complete 3 missions
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "weekly"))
    asyncio.run(missions_service.record_command_completion(123, "monthly"))

    unlocked = asyncio.run(gb_db.is_global_unlocked(123))
    assert unlocked is True


def test_not_unlocked_before_3_missions():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "weekly"))

    unlocked = asyncio.run(gb_db.is_global_unlocked(123))
    assert unlocked is False


def test_unlock_persists():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "weekly"))
    asyncio.run(missions_service.record_command_completion(123, "monthly"))

    unlocked1 = asyncio.run(gb_db.is_global_unlocked(123))
    unlocked2 = asyncio.run(gb_db.is_global_unlocked(123))
    assert unlocked1 is True
    assert unlocked2 is True


# ---------------------------------------------------------------------------
# Missions UI
# ---------------------------------------------------------------------------

def test_get_missions_ui_format():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "weekly"))

    data = asyncio.run(missions_service.get_missions_ui(123))
    assert data["completed_count"] == 2
    assert data["total_missions"] == 10
    assert data["required_for_unlock"] == 3
    assert data["unlocked"] is False
    assert len(data["missions"]) == 10

    daily = next(m for m in data["missions"] if m["mission_id"] == "daily")
    assert daily["completed"] is True

    weekly = next(m for m in data["missions"] if m["mission_id"] == "weekly")
    assert weekly["completed"] is True


def test_format_missions_message_locked():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    data = asyncio.run(missions_service.get_missions_ui(123))
    text = asyncio.run(missions_service.format_missions_message(data))
    assert "🔒 <b>LOCKED</b>" in text
    assert "1 / 10" in text


def test_format_missions_message_unlocked():
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "weekly"))
    asyncio.run(missions_service.record_command_completion(123, "monthly"))
    data = asyncio.run(missions_service.get_missions_ui(123))
    text = asyncio.run(missions_service.format_missions_message(data))
    assert "🔓 <b>UNLOCKED</b>" in text
    assert "ENTER GLOBAL EVENT" in text


# ---------------------------------------------------------------------------
# Admin Pre-Unlock
# ---------------------------------------------------------------------------

def test_admin_pre_unlock_on_unlock_check(monkeypatch):
    """Admin/Owner should get instant unlock when _check_global_unlock is called."""
    from utils.permissions import is_sudo
    
    # Mock is_sudo to return True for user 999
    async def fake_is_sudo(user_id):
        return user_id == 999
    monkeypatch.setattr("services.global_battle.missions.is_sudo", fake_is_sudo)
    
    # User 999 is admin, should get pre-unlocked
    asyncio.run(missions_service._check_global_unlock(999))
    unlocked = asyncio.run(gb_db.is_global_unlocked(999))
    assert unlocked is True
    
    # User 123 is not admin, should not be unlocked
    asyncio.run(missions_service._check_global_unlock(123))
    unlocked = asyncio.run(gb_db.is_global_unlocked(123))
    assert unlocked is False


def test_owner_pre_unlock_on_unlock_check(monkeypatch):
    """Owner should get instant unlock when _check_global_unlock is called."""
    from config import config
    
    owner_id = config.OWNER_ID
    
    # Owner should get pre-unlocked
    asyncio.run(missions_service._check_global_unlock(owner_id))
    unlocked = asyncio.run(gb_db.is_global_unlocked(owner_id))
    assert unlocked is True


def test_admin_pre_unlock_in_get_missions_ui(monkeypatch):
    """Admin should see pre_unlocked=True in get_missions_ui."""
    from utils.permissions import is_sudo
    
    async def fake_is_sudo(user_id):
        return user_id == 999
    monkeypatch.setattr("services.global_battle.missions.is_sudo", fake_is_sudo)
    
    # Complete some missions for admin
    asyncio.run(missions_service.record_command_completion(999, "daily"))
    asyncio.run(missions_service.record_command_completion(999, "weekly"))
    
    data = asyncio.run(missions_service.get_missions_ui(999))
    assert data["completed_count"] == 2
    assert data["unlocked"] is True
    assert data["pre_unlocked"] is True
    
    # Non-admin with 2 missions should not be unlocked
    asyncio.run(missions_service.record_command_completion(123, "daily"))
    asyncio.run(missions_service.record_command_completion(123, "weekly"))
    data = asyncio.run(missions_service.get_missions_ui(123))
    assert data["completed_count"] == 2
    assert data["unlocked"] is False
    assert data.get("pre_unlocked") is False


def test_format_missions_message_admin_pre_unlocked(monkeypatch):
    """Admin should see 'Admin Pre-Unlock' in formatted message."""
    from utils.permissions import is_sudo
    
    async def fake_is_sudo(user_id):
        return user_id == 999
    monkeypatch.setattr("services.global_battle.missions.is_sudo", fake_is_sudo)
    
    asyncio.run(missions_service.record_command_completion(999, "daily"))
    data = asyncio.run(missions_service.get_missions_ui(999))
    text = asyncio.run(missions_service.format_missions_message(data))
    assert "🔓 <b>UNLOCKED (Admin Pre-Unlock)</b>" in text
    assert "ENTER GLOBAL EVENT" in text


def test_format_missions_message_owner_pre_unlocked(monkeypatch):
    """Owner should see 'Admin Pre-Unlock' in formatted message."""
    from config import config
    from utils.permissions import is_sudo
    
    async def fake_is_sudo(user_id):
        return user_id == config.OWNER_ID
    monkeypatch.setattr("services.global_battle.missions.is_sudo", fake_is_sudo)
    
    owner_id = config.OWNER_ID
    data = asyncio.run(missions_service.get_missions_ui(owner_id))
    text = asyncio.run(missions_service.format_missions_message(data))
    assert "🔓 <b>UNLOCKED (Admin Pre-Unlock)</b>" in text
    assert "ENTER GLOBAL EVENT" in text