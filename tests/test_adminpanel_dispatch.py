"""Dispatch-level tests for the /adminpanel propagation fix.

Proves that the promo text detector no longer swallows ``/adminpanel``: with
the real Pyrogram filters registered in ``bot.py`` order, the update reaches
``cmd_adminpanel`` (and its callbacks are handled).  The dispatcher loop below
mirrors Pyrogram's own semantics (first-match, ``ContinuePropagation`` moves on
to the next handler, otherwise ``break``).
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("BOT_TOKEN", "123:testtoken")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("MONGO_DB_NAME", "unoitachi_tests_adminpanel_dispatch")
os.environ.setdefault("OWNER_ID", "1")

import pyrogram  # noqa: E402
from pyrogram.enums import ChatType  # noqa: E402
from pyrogram.types import CallbackQuery, Chat, Message, User  # noqa: E402

from handlers import adminpanel, chat_events, promo_detect  # noqa: E402


@pytest.fixture(autouse=True)
def sudo_is_owner(monkeypatch):
    """Owner id 1 is sudo regardless of the ambient config.OWNER_ID value."""

    async def fake_sudo(user_id):
        return user_id == 1

    monkeypatch.setattr("utils.permissions.is_sudo", fake_sudo)
    monkeypatch.setattr("handlers.adminpanel.is_sudo", fake_sudo)


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


@pytest.fixture
def app():
    a = FakeApp()
    promo_detect.register(a)
    adminpanel.register(a)
    chat_events.register(a)
    return a


@pytest.fixture
def client():
    return SimpleNamespace(me=SimpleNamespace(username="uno_reverse_god_bot"))


@pytest.fixture
def env(monkeypatch):
    async def fake_gate(message, feature=None):
        return True, None

    async def fake_ensure(*args, **kwargs):
        return None

    monkeypatch.setattr("handlers.common.check_gate", fake_gate)
    monkeypatch.setattr("handlers.common.identity_service.ensure_user", fake_ensure)
    monkeypatch.setattr(
        "handlers.promo_detect.promo_service.cache.candidates",
        lambda tokens: _async_candidates(tokens),
    )


async def _async_candidates(tokens):
    return []


def _msg(text, chat_type=ChatType.PRIVATE, user_id=1, is_bot=False):
    return Message(
        id=1,
        date=123,
        text=text,
        chat=Chat(id=100, type=chat_type),
        from_user=User(id=user_id, is_bot=is_bot, first_name="T", username="t"),
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
    """Mirror Pyrogram's message dispatcher for a single update."""
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


# ─── /adminpanel command reaches the handler ───────────────────────────────


def test_adminpanel_reaches_handler_in_private(app, client, env, monkeypatch):
    replies = []

    async def reply(client, message, text, **kwargs):
        replies.append(text)

    monkeypatch.setattr("handlers.adminpanel.reply_html", reply)

    message = _msg("/adminpanel")
    ran = asyncio.run(dispatch_message(app, client, message))

    assert ran == ["on_promo_text", "cmd_adminpanel"]
    assert "ADMIN PANEL" in replies[0]
    assert message.command == ["adminpanel"]


def test_adminpanel_reaches_handler_in_group(app, client, env, monkeypatch):
    replies = []

    async def reply(client, message, text, **kwargs):
        replies.append(text)

    monkeypatch.setattr("handlers.adminpanel.reply_html", reply)

    message = _msg("/adminpanel", chat_type=ChatType.SUPERGROUP)
    ran = asyncio.run(dispatch_message(app, client, message))

    assert ran == ["on_promo_text", "cmd_adminpanel"]
    assert "ADMIN PANEL" in replies[0]
    assert "on_group_message" not in ran


def test_adminpanel_denied_for_non_sudo(app, client, env, monkeypatch):
    denied = []

    async def reply(client, message, text, **kwargs):
        denied.append(text)

    async def not_sudo(user_id):
        return False

    monkeypatch.setattr("utils.permissions.is_sudo", not_sudo)
    monkeypatch.setattr("utils.permissions.reply_html", reply)

    message = _msg("/adminpanel", user_id=2)
    ran = asyncio.run(dispatch_message(app, client, message))

    assert "cmd_adminpanel" in ran
    assert any("not allowed" in text for text in denied)


def test_plain_group_text_reaches_group_hook(app, client, env, monkeypatch):
    registered = []

    async def fake_register(chat_id):
        registered.append(chat_id)

    monkeypatch.setattr(
        "handlers.chat_events.group_config_service.ensure_registered", fake_register
    )

    message = _msg("hello everyone", chat_type=ChatType.SUPERGROUP)
    ran = asyncio.run(dispatch_message(app, client, message))

    assert ran == ["on_promo_text", "on_group_message"]
    assert registered == [100]


# ─── /adminpanel callbacks ─────────────────────────────────────────────────


def test_callback_category_opens_economy(app, client, env, monkeypatch):
    edited, answered = [], []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append((text, reply_markup))

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    monkeypatch.setattr("handlers.adminpanel.edit_html", edit)
    monkeypatch.setattr("handlers.adminpanel.answer_callback", answer)

    ran = asyncio.run(
        dispatch_callback(app, client, _cbq("adminpanel:cat:economy"))
    )

    assert ran == ["cb_adminpanel"]
    assert "Economy" in edited[0][0]
    assert answered and answered[0][1] is False


def test_callback_close_deletes_message(app, client, env, monkeypatch):
    message = _msg("panel")
    deleted = []

    async def fake_delete():
        deleted.append(True)

    message.delete = fake_delete

    asyncio.run(dispatch_callback(app, client, _cbq("adminpanel:close", message=message)))

    assert deleted == [True]


def test_callback_denied_for_non_sudo(app, client, env, monkeypatch):
    edited, answered = [], []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append(text)

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    async def not_sudo(user_id):
        return False

    monkeypatch.setattr("handlers.adminpanel.is_sudo", not_sudo)
    monkeypatch.setattr("handlers.adminpanel.edit_html", edit)
    monkeypatch.setattr("handlers.adminpanel.answer_callback", answer)

    asyncio.run(dispatch_callback(app, client, _cbq("adminpanel:cat:economy", user_id=2)))

    assert edited == []
    assert answered and "Only the owner/sudo" in answered[0][0]
    assert answered[0][1] is True


def test_callback_unknown_category_warns(app, client, env, monkeypatch):
    edited, answered = [], []

    async def edit(client, message, text, reply_markup=None, **kwargs):
        edited.append(text)

    async def answer(client, callback, text="", show_alert=False):
        answered.append((text, show_alert))

    monkeypatch.setattr("handlers.adminpanel.edit_html", edit)
    monkeypatch.setattr("handlers.adminpanel.answer_callback", answer)

    asyncio.run(
        dispatch_callback(app, client, _cbq("adminpanel:cat:nope"))
    )

    assert edited == []
    assert answered and "Unknown category" in answered[0][0]
    assert answered[0][1] is True