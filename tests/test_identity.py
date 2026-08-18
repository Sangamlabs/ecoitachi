"""Unit tests for the central identity layer and UID helpers.

These tests need no database: they exercise pure helpers (UID validation /
username normalization) and resolver precedence with a monkeypatched database,
so they run anywhere.
"""

from database import users as users_db
from services import identity as identity_service


class _FakeUser:
    def __init__(self, user_id, username=None, first_name=None):
        self.id = user_id
        self.username = username
        self.first_name = first_name


class _FakeReply:
    def __init__(self, from_user):
        self.from_user = from_user


class _FakeMessage:
    def __init__(self, reply_to_message=None):
        self.reply_to_message = reply_to_message


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_is_uid_matches_valid_formats():
    assert users_db.is_uid("UID-000001") is True
    assert users_db.is_uid("UID-1234567") is True
    assert users_db.is_uid("uid-0042") is True


def test_is_uid_rejects_garbage():
    assert users_db.is_uid("123456") is False
    assert users_db.is_uid("UID") is False
    assert users_db.is_uid("UID-12") is False
    assert users_db.is_uid("uid-xyz") is False
    assert users_db.is_uid("") is False


def test_normalize_username_lowercases_and_strips_at():
    assert users_db._normalize_username("TestUser") == "testuser"
    assert users_db._normalize_username("@TestUser") == "testuser"
    assert users_db._normalize_username(None) is None
    assert users_db._normalize_username("") is None


# ---------------------------------------------------------------------------
# Resolver precedence
# ---------------------------------------------------------------------------

def _patch_db(monkeypatch, *, existing=None, users_by_username=None):
    """Monkeypatch users_db so identity.resolve_user needs no MongoDB."""
    calls = {"created": [], "touched": []}

    async def fake_get_or_create(user_id, username=None, first_name=None, is_bot=False):
        calls["created"].append(user_id)
        return {"user_id": user_id, "unique_user_id": f"UID-{user_id:06d}", "is_bot": is_bot}

    async def fake_touch(user_id, username=None, first_name=None):
        calls["touched"].append(user_id)

    async def fake_assign_uid(user_id):
        return f"UID-{user_id:06d}"

    async def fake_get_user(user_id):
        return {"user_id": user_id, "unique_user_id": f"UID-{user_id:06d}"}

    monkeypatch.setattr(users_db, "get_or_create_user", fake_get_or_create)
    monkeypatch.setattr(users_db, "touch_user", fake_touch)
    monkeypatch.setattr(users_db, "assign_uid", fake_assign_uid)
    monkeypatch.setattr(users_db, "get_user", fake_get_user)
    return calls


class _FakeClient:
    async def get_users(self, username):
        raise ValueError("not reachable in these tests")


async def test_resolve_prefers_reply_over_arg(monkeypatch):
    calls = _patch_db(monkeypatch)
    message = _FakeMessage(reply_to_message=_FakeReply(_FakeUser(99, "bob", "Bob")))
    doc = await identity_service.resolve_user(_FakeClient(), message, "123456", create=True)
    assert doc["user_id"] == 99
    assert 99 in calls["created"]


async def test_resolve_numeric_creates_when_missing(monkeypatch):
    calls = _patch_db(monkeypatch)
    doc = await identity_service.resolve_user(_FakeClient(), _FakeMessage(), "555", create=True)
    assert doc["user_id"] == 555
    assert 555 in calls["created"]


async def test_resolve_no_arg_no_reply_returns_none(monkeypatch):
    calls = _patch_db(monkeypatch)
    doc = await identity_service.resolve_user(_FakeClient(), _FakeMessage(), None, create=True)
    assert doc is None
    assert calls["created"] == []
    assert calls["touched"] == []


async def test_resolve_uid_lookup(monkeypatch):
    _patch_db(monkeypatch)
    async def fake_get_user_by_uid(uid):
        assert uid == "UID-000042"
        return {"user_id": 42, "unique_user_id": "UID-000042"}

    monkeypatch.setattr(users_db, "get_user_by_uid", fake_get_user_by_uid)
    doc = await identity_service.resolve_user(_FakeClient(), _FakeMessage(), "UID-000042", create=True)
    assert doc["user_id"] == 42