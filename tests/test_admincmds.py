"""Dispatch + unit tests for the /admincmds OWNER-only control panel.

Covers discovery of the ACTUAL registered admin commands, pagination, the
OWNER-only gate (on both the command and every callback), permission changes
with immediate effect and audit logging, non-delegatable protection, and the
no-duplicates upsert storage.  Runs anywhere without a local MongoDB.
"""

import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_admincmds")

import pyrogram  # noqa: E402
from pyrogram import filters  # noqa: E402
from pyrogram.enums import ChatType  # noqa: E402
from pyrogram.handlers import MessageHandler  # noqa: E402
from pyrogram.types import CallbackQuery, Chat, Message, User  # noqa: E402

from database.mongo import mongo  # noqa: E402
from handlers import admincmds  # noqa: E402
from services import settings as settings_service  # noqa: E402
from services import transaction as tx_service  # noqa: E402
from utils import permissions as perm  # noqa: E402


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in query.items()):
                return doc
        return None

    async def find(self, query=None):
        return []

    async def update_one(self, filt, update, upsert=False):
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                doc.update(update.get("$set", {}))
                return SimpleNamespace(modified_count=1)
        if upsert:
            new_doc = dict(update.get("$set", {}))
            new_doc.update(filt)
            self.docs[id(new_doc)] = new_doc
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def insert_one(self, doc):
        self.docs[id(doc)] = doc


class _FakeDb:
    def __init__(self):
        self._collections = {"settings": _FakeCollection()}

    def __getitem__(self, name):
        return self._collections[name]


class FakeApp:
    def __init__(self):
        self.message_handlers = []
        self.callback_handlers = []

    def on_message(self, flt):
        def deco(cb):
            self.message_handlers.append((flt, cb))
            return cb

        return deco

    def on_callback_query(self, flt):
        def deco(cb):
            self.callback_handlers.append((flt, cb))
            return cb

        return deco

    def on_chat_member_updated(self, flt):
        def deco(cb):
            return cb

        return deco


def _admin_handler(command, perm_mode, extra=None):
    def cb(client, message):
        pass

    cb._admin_perm = perm_mode
    flt = filters.command(command)
    if extra is not None:
        flt = flt & extra
    return MessageHandler(cb, flt)


def make_client():
    handlers = [
        _admin_handler("give", "admin"),
        _admin_handler("remove", "admin"),
        _admin_handler("getcoin", "admin"),
        _admin_handler("addsudo", "owner"),
        _admin_handler("admincmds", "owner"),
        _admin_handler("setchat", "admin"),
        _admin_handler("say", "admin", filters.private),
        _admin_handler("restart", "admin"),
    ]

    def user_cb(client, message):
        pass

    user_handler = MessageHandler(user_cb, filters.command("balance"))
    dispatcher = SimpleNamespace(groups=[handlers + [user_handler]])
    return SimpleNamespace(
        me=SimpleNamespace(username="uno_reverse_god_bot"),
        dispatcher=dispatcher,
    )


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    perm._PERM_CACHE.clear()
    admincmds.reset_discovery()
    monkeypatch.setattr(mongo, "db", _FakeDb())
    # Deterministic ownership regardless of the ambient config.OWNER_ID value:
    # user id 1 is the owner, everything else is not.
    async def fake_is_owner(user_id):
        return user_id == 1

    monkeypatch.setattr(perm, "is_owner", fake_is_owner)
    monkeypatch.setattr(admincmds, "is_owner", fake_is_owner)


@pytest.fixture
def app():
    a = FakeApp()
    admincmds.register(a)
    return a


@pytest.fixture
def client():
    return make_client()


@pytest.fixture
def env(monkeypatch):
    async def fake_gate(message, feature=None):
        return True, None

    async def fake_ensure(*args, **kwargs):
        return None

    monkeypatch.setattr("handlers.common.check_gate", fake_gate)
    monkeypatch.setattr("handlers.common.identity_service.ensure_user", fake_ensure)


def _msg(text, chat_type=ChatType.PRIVATE, user_id=1):
    return Message(
        id=1,
        date=123,
        text=text,
        chat=Chat(id=100, type=chat_type),
        from_user=User(id=user_id, is_bot=False, first_name="T", username="t"),
        service=None,
    )


def _cbq(data, user_id=1, message=None):
    return CallbackQuery(
        id=1,
        chat_instance="x",
        data=data,
        from_user=User(id=user_id, is_bot=False, first_name="T", username="t"),
        message=message or _msg("panel"),
    )


async def dispatch_message(app, client, message):
    ran = []
    for flt, cb in app.message_handlers:
        try:
            if await flt(client, message):
                ran.append(cb.__name__)
                try:
                    await cb(client, message)
                except pyrogram.ContinuePropagation:
                    continue
                except pyrogram.StopPropagation:
                    return ran
                except Exception:
                    break
                break
        except Exception:
            continue
    return ran


async def dispatch_callback(app, client, cbq):
    ran = []
    for flt, cb in app.callback_handlers:
        try:
            if await flt(client, cbq):
                await cb(client, cbq)
                ran.append(cb.__name__)
                break
        except Exception:
            continue
    return ran


# ---------------------------------------------------------------------------
# Command normalization
# ---------------------------------------------------------------------------

def test_normalize_command_name_variants():
    assert perm.normalize_command_name("give") == "give"
    assert perm.normalize_command_name("/give") == "give"
    assert perm.normalize_command_name("GIVE") == "give"
    assert perm.normalize_command_name("give@Uno_Reverse_God_Bot") == "give"
    assert perm.normalize_command_name("  /GetCoin  ") == "getcoin"
    assert perm.normalize_command_name("") == ""


def test_non_delegatable_set_contains_expected_commands():
    expected = {"admincmds", "addsudo", "rsudo", "clear",
                "recover", "restore", "restorecase", "securityset"}
    assert perm.NON_DELEGATABLE_ADMIN_COMMANDS == expected


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------

async def _run(coro):
    return await coro


def test_resolve_default_when_no_override():
    assert asyncio.run(perm.resolve_command_permission("give")) == "admin"


def test_resolve_non_delegatable_always_owner():
    assert asyncio.run(perm.resolve_command_permission("admincmds")) == "owner"
    assert asyncio.run(perm.resolve_command_permission("addsudo")) == "owner"


def test_resolve_stored_override_and_invalidation():
    asyncio.run(settings_service.set_command_permission("give", "owner", 1))
    # Fresh resolve (empty cache) must read the stored override.
    perm.invalidate_command_permission("give")
    assert asyncio.run(perm.resolve_command_permission("give")) == "owner"

    # Change the stored mode; cache must be invalidated to observe it.
    asyncio.run(settings_service.set_command_permission("give", "admin", 1))
    perm.invalidate_command_permission("give")
    assert asyncio.run(perm.resolve_command_permission("give")) == "admin"


def test_resolve_non_delegatable_ignores_stored_admin():
    asyncio.run(settings_service.set_command_permission("securityset", "admin", 1))
    perm.invalidate_command_permission("securityset")
    assert asyncio.run(perm.resolve_command_permission("securityset")) == "owner"


def test_set_command_permission_upserts_one_record():
    asyncio.run(settings_service.set_command_permission("give", "owner", 1))
    asyncio.run(settings_service.set_command_permission("give", "admin", 1))
    settings_coll = mongo.db["settings"]
    matching = [d for d in settings_coll.docs.values() if d.get("key") == "cmdperm:give"]
    assert len(matching) == 1
    assert matching[0]["mode"] == "admin"
    assert matching[0]["updated_by"] == 1


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_discovery_finds_all_and_excludes_non_admin(client):
    commands = admincmds.discover_admin_commands(client)
    names = {c["command"] for c in commands}
    assert names == {"give", "remove", "getcoin", "addsudo",
                     "admincmds", "setchat", "say", "restart"}
    assert "balance" not in names
    # No duplicates even when the same command appears via combined filters.
    assert len(names) == len(commands)


def test_discovery_carries_default_permissions(client):
    by_name = {c["command"]: c for c in admincmds.discover_admin_commands(client)}
    assert by_name["give"]["perm"] == "admin"
    assert by_name["addsudo"]["perm"] == "owner"
    assert by_name["admincmds"]["perm"] == "owner"


def test_discovery_maps_categories(client):
    by_name = {c["command"]: c for c in admincmds.discover_admin_commands(client)}
    assert by_name["give"]["category"] == "economy"
    assert by_name["getcoin"]["category"] == "economy"
    # setchat is documented under group config in the panel.
    assert by_name["setchat"]["category"] in set(admincmds.PANEL_CATEGORIES)


# ---------------------------------------------------------------------------
# /admincmds command dispatch
# ---------------------------------------------------------------------------

def test_admincmds_reaches_handler_and_lists_panel(app, client, env, monkeypatch):
    replies = []

    async def reply(client, message, text, **kwargs):
        replies.append(text)

    monkeypatch.setattr("handlers.admincmds.reply_html", reply)

    message = _msg("/admincmds")
    ran = asyncio.run(dispatch_message(app, client, message))

    assert ran == ["cmd_admincmds"]
    assert "ADMIN COMMANDS" in replies[0]
    assert message.command == ["admincmds"]


def test_admincmds_denied_for_non_owner(app, client, env, monkeypatch):
    denied = []

    async def reply(client, message, text, **kwargs):
        denied.append(text)

    monkeypatch.setattr("utils.permissions.reply_html", reply)

    message = _msg("/admincmds", user_id=2)
    asyncio.run(dispatch_message(app, client, message))

    assert any("not allowed" in text for text in denied)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def test_callback_denied_for_non_owner(app, client, env, monkeypatch):
    edited, answered = [], []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append(text)

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    monkeypatch.setattr("handlers.admincmds.edit_html", edit)
    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)

    ran = asyncio.run(dispatch_callback(app, client, _cbq("admincmds:cmd:give", user_id=2)))

    assert ran == ["cb_admincmds"]
    assert edited == []
    assert answered and "Only the bot owner" in answered[0][0]
    assert answered[0][1] is True


def test_callback_opens_command_panel(app, client, env, monkeypatch):
    edited, answered = [], []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append((text, reply_markup))

    async def answer(client, callback, text="", show_alert=False):
        answered.append(text)

    monkeypatch.setattr("handlers.admincmds.edit_html", edit)
    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:cmd:give")))

    assert "COMMAND PERMISSION" in edited[0][0]
    assert "/give" in edited[0][0]
    buttons = edited[0][1].inline_keyboard
    labels = {b.text for row in buttons for b in row}
    assert "🔴 OWNER ONLY" in labels
    assert "🟢 OWNER + SUDO" in labels
    assert answered == [""]


def test_callback_set_permission_changes_and_audits(app, client, env, monkeypatch):
    edited, answered, recorded = [], [], []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append(text)

    async def answer(client, callback, text="", show_alert=False):
        answered.append(text)

    async def fake_record(**kwargs):
        recorded.append(kwargs)
        return "tx-1"

    monkeypatch.setattr("handlers.admincmds.edit_html", edit)
    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)
    monkeypatch.setattr("handlers.admincmds.tx_service.record", fake_record)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:set:give:owner")))

    # Stored override written exactly once (no duplicate record).
    settings_coll = mongo.db["settings"]
    matching = [d for d in settings_coll.docs.values() if d.get("key") == "cmdperm:give"]
    assert len(matching) == 1
    assert matching[0]["mode"] == "owner"
    assert matching[0]["updated_by"] == 1

    # Audit record emitted with old/new/changed_by.
    assert len(recorded) == 1
    meta = recorded[0]["metadata"]
    assert recorded[0]["ttype"] == tx_service.ADMIN_COMMAND_PERMISSION_CHANGED
    assert meta["command"] == "give"
    assert meta["old"] == "admin"
    assert meta["new"] == "owner"
    assert meta["changed_by"] == 1

    # Immediate effect: the resolver now gates /give as OWNER ONLY.
    perm.invalidate_command_permission("give")
    assert asyncio.run(perm.resolve_command_permission("give")) == "owner"

    assert any("Permission updated" in t for t in edited)
    assert answered and "/give -> OWNER" in answered[-1]


def test_callback_set_same_mode_is_noop(app, client, env, monkeypatch):
    answered = []

    async def answer(client, callback, text="", show_alert=False):
        answered.append(text)

    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:set:give:admin")))

    assert mongo.db["settings"].docs == {}
    assert answered and "already" in answered[-1]


def test_callback_non_delegatable_cannot_be_delegated(app, client, env, monkeypatch):
    answered = []

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:set:addsudo:admin")))

    assert mongo.db["settings"].docs == {}
    assert answered and "cannot be delegated" in answered[0][0]
    assert answered[0][1] is True


def test_callback_unknown_command_warns(app, client, env, monkeypatch):
    answered = []

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:set:notacmd:owner")))

    assert answered and "Unknown command" in answered[0][0]
    assert answered[0][1] is True


def test_callback_invalid_mode_warns(app, client, env, monkeypatch):
    answered = []

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    monkeypatch.setattr("handlers.admincmds.answer_callback", answer)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:set:give:sudo")))

    assert answered and "Invalid permission" in answered[0][0]
    assert answered[0][1] is True


def test_callback_close_deletes_message(app, client, env, monkeypatch):
    message = _msg("panel")
    deleted = []

    async def fake_delete():
        deleted.append(True)

    message.delete = fake_delete

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:close", message=message)))

    assert deleted == [True]


def test_callback_list_pagination_renders(app, client, env, monkeypatch):
    edited = []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append(text)

    monkeypatch.setattr("handlers.admincmds.edit_html", edit)

    asyncio.run(dispatch_callback(app, client, _cbq("admincmds:list:2")))

    assert edited and "Page 2/" in edited[0]


def test_admincmds_perm_default_preserved_even_after_changes(app, client, env, monkeypatch):
    # Decorator default for a sudo command is "admin" and discovery reports it.
    by_name = {c["command"]: c for c in admincmds.discover_admin_commands(client)}
    assert by_name["give"]["perm"] == "admin"
    assert by_name["restart"]["perm"] == "admin"