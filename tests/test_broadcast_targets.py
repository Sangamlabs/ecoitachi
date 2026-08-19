"""Unit tests for broadcast target discovery.

The target queries live in :mod:`services.broadcast`; these tests substitute a
fake MongoDB so they run anywhere (no local MongoDB required).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.mongo import mongo  # noqa: E402
from services import broadcast  # noqa: E402
from services import group_config as group_config_service  # noqa: E402


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


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *args, **kwargs):
        # Mimic Mongo: apply the filter against the fake docs.
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
    fake = _FakeDb(collections)
    monkeypatch.setattr(mongo, "db", fake)
    return fake


# ---------------------------------------------------------------------------
# get_target_users — DM eligibility
# ---------------------------------------------------------------------------

def test_dm_targets_include_only_starters(monkeypatch):
    _install_db(
        monkeypatch,
        {
            "users": [
                {"user_id": 1, "bot_started": True, "is_banned": False, "is_frozen": False, "is_bot": False},
                {"user_id": 2, "bot_started": False, "is_banned": False, "is_frozen": False, "is_bot": False},
                {"user_id": 3, "bot_started": True, "is_banned": True, "is_frozen": False, "is_bot": False},
                {"user_id": 4, "bot_started": True, "is_banned": False, "is_frozen": False, "is_bot": True},
                {"user_id": 5, "bot_started": True, "is_banned": False, "is_frozen": True, "is_bot": False},
                {"user_id": 6},  # legacy doc: no bot_started at all
            ]
        },
    )

    async def run():
        return await broadcast.get_target_users()

    import asyncio

    assert asyncio.run(run()) == [1]


def test_dm_targets_empty_when_nobody_started(monkeypatch):
    _install_db(
        monkeypatch,
        {"users": [{"user_id": 1, "bot_started": False}]},
    )

    import asyncio

    assert asyncio.run(broadcast.get_target_users()) == []


# ---------------------------------------------------------------------------
# get_target_chats — groups
# ---------------------------------------------------------------------------

def test_group_targets_include_enabled_groups(monkeypatch):
    _install_db(
        monkeypatch,
        {
            "group_config": [
                {"chat_id": -1001, "group_enabled": True},
                {"chat_id": -1002, "group_enabled": False},
                {"chat_id": -1003},  # defaults to enabled
            ]
        },
    )

    import asyncio

    assert asyncio.run(broadcast.get_target_chats()) == [-1001, -1003]


# ---------------------------------------------------------------------------
# Group auto-registration (ensure_registered)
# ---------------------------------------------------------------------------

class _UpsertCollection(_FakeCollection):
    def __init__(self):
        super().__init__([])
        self.calls = []

    async def update_one(self, filt, update, upsert=False):
        self.calls.append((filt, update, upsert))


def test_ensure_registered_is_idempotent(monkeypatch):
    coll = _UpsertCollection()
    fake = _FakeDb({})
    fake._collections["group_config"] = coll
    monkeypatch.setattr(mongo, "db", fake)

    import asyncio

    async def run():
        await group_config_service.ensure_registered(-100123)
        await group_config_service.ensure_registered(-100123)

    asyncio.run(run())
    # Both calls are upserts on the chat_id; $setOnInsert guarantees a doc is
    # only created once. Assert the correct filter + upsert flag were used.
    assert len(coll.calls) == 2
    for filt, update, upsert in coll.calls:
        assert filt == {"chat_id": -100123}
        assert upsert is True
        assert "$setOnInsert" in update


# ---------------------------------------------------------------------------
# Broadcast id + error classification (pure helpers)
# ---------------------------------------------------------------------------

def test_new_broadcast_id_format():
    bid = broadcast.new_broadcast_id()
    assert bid.startswith("BC-")
    assert len(bid) == 11


def test_classify_error_blocked_vs_failed():
    from pyrogram.errors import RPCError

    class _Err(RPCError):
        ID = "X"
        NAME = "X"

    blocked = _Err()
    blocked._message = "USER_IS_BLOCKED"  # pragma: no cover - fallback
    # RPCError message attribute is set via __init__ in real pyrogram; test the
    # classifier against explicit error strings instead.
    assert broadcast._classify_error(type("E", (), {"__str__": lambda s: "this user was blocked"})()) == "blocked"
    assert broadcast._classify_error(type("E", (), {"__str__": lambda s: "ChatForwardsRestricted"})()) == "failed"