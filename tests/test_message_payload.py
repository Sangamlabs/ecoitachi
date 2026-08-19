"""Unit tests for the central broadcast message extractor/serializer.

Covers the required cases: plain text, bold/italic/underline/strikethrough/
spoiler/code/pre/text-link/mention/custom-emoji/blockquote entities, photo,
video, GIF/animation, document, sticker, voice, video note, audio, media
spoiler, inline URL buttons, the HTML fallback, and the payload-vs-copy
routing in the broadcast service.
"""

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from pyrogram.enums import MessageEntityType, ParseMode  # noqa: E402
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, User  # noqa: E402

from database.mongo import mongo  # noqa: E402
from services import broadcast, message_payload  # noqa: E402


class _Media:
    def __init__(self, file_id):
        self.file_id = file_id


class _Msg:
    """Minimal stand-in for a pyrogram Message exposing the read attributes."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _entity(mtype, offset, length, **extra):
    return MessageEntity(type=mtype, offset=offset, length=length, **extra)


def _msg(**kwargs):
    return _Msg(**kwargs)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def test_plain_text():
    payload = message_payload.extract_message(_msg(text="Hello world", entities=[]))
    assert payload["type"] == "text"
    assert payload["text"] == "Hello world"
    assert payload["entities"] == []
    assert "use_html" not in payload


def test_bold():
    payload = message_payload.extract_message(
        _msg(text="Hello bold world", entities=[_entity(MessageEntityType.BOLD, 6, 4)])
    )
    assert payload["entities"] == [{"type": "BOLD", "offset": 6, "length": 4}]


def test_italic():
    payload = message_payload.extract_message(
        _msg(text="Hello *w*", entities=[_entity(MessageEntityType.ITALIC, 6, 3)])
    )
    assert payload["entities"][0]["type"] == "ITALIC"


def test_underline():
    payload = message_payload.extract_message(
        _msg(text="hello", entities=[_entity(MessageEntityType.UNDERLINE, 0, 5)])
    )
    assert payload["entities"][0]["type"] == "UNDERLINE"


def test_strikethrough():
    payload = message_payload.extract_message(
        _msg(text="hello", entities=[_entity(MessageEntityType.STRIKETHROUGH, 0, 5)])
    )
    assert payload["entities"][0]["type"] == "STRIKETHROUGH"


def test_spoiler():
    payload = message_payload.extract_message(
        _msg(text="hey spoiler", entities=[_entity(MessageEntityType.SPOILER, 4, 7)])
    )
    assert payload["entities"][0] == {"type": "SPOILER", "offset": 4, "length": 7}


def test_code():
    payload = message_payload.extract_message(
        _msg(text="run `x`", entities=[_entity(MessageEntityType.CODE, 4, 3)])
    )
    assert payload["entities"][0]["type"] == "CODE"


def test_pre():
    payload = message_payload.extract_message(
        _msg(text="code", entities=[_entity(MessageEntityType.PRE, 0, 4, language="python")])
    )
    assert payload["entities"][0] == {
        "type": "PRE", "offset": 0, "length": 4, "language": "python",
    }


def test_text_link():
    payload = message_payload.extract_message(
        _msg(
            text="visit here",
            entities=[_entity(MessageEntityType.TEXT_LINK, 6, 4, url="https://example.com")],
        )
    )
    assert payload["entities"][0] == {
        "type": "TEXT_LINK", "offset": 6, "length": 4, "url": "https://example.com",
    }


def test_mention():
    user = User(id=123, first_name="Sangam", last_name="Yadav", username="sangam")
    payload = message_payload.extract_message(
        _msg(text="hi @sangam", entities=[_entity(MessageEntityType.TEXT_MENTION, 3, 7, user=user)])
    )
    assert payload["entities"][0]["type"] == "TEXT_MENTION"
    assert payload["entities"][0]["user"]["id"] == 123
    assert payload["entities"][0]["user"]["username"] == "sangam"


def test_custom_emoji():
    payload = message_payload.extract_message(
        _msg(
            text="\U0001f4af hi",
            entities=[_entity(MessageEntityType.CUSTOM_EMOJI, 0, 1, custom_emoji_id=5368324170671202286)],
        )
    )
    assert payload["entities"][0]["type"] == "CUSTOM_EMOJI"
    assert payload["entities"][0]["custom_emoji_id"] == 5368324170671202286


def test_blockquote():
    payload = message_payload.extract_message(
        _msg(text="quoted", entities=[_entity(MessageEntityType.BLOCKQUOTE, 0, 6)])
    )
    assert payload["entities"][0]["type"] == "BLOCKQUOTE"


def test_literal_html_without_entities_sets_use_html():
    payload = message_payload.extract_message(
        _msg(text="<b>Bold</b> and <tg-spoiler>hidden</tg-spoiler>", entities=[])
    )
    assert payload["use_html"] is True


# ---------------------------------------------------------------------------
# Media extraction
# ---------------------------------------------------------------------------

def test_photo_with_formatted_caption():
    payload = message_payload.extract_message(
        _msg(
            photo=_Media("photo_id"),
            caption="<b>Bold</b> caption",
            caption_entities=[],
        )
    )
    assert payload["type"] == "photo"
    assert payload["file_id"] == "photo_id"
    assert payload["caption"] == "<b>Bold</b> caption"
    assert payload["use_html"] is True


def test_photo_caption_entities_preserved():
    payload = message_payload.extract_message(
        _msg(
            photo=_Media("photo_id"),
            caption="pic caption",
            caption_entities=[_entity(MessageEntityType.BOLD, 4, 7)],
        )
    )
    assert payload["caption_entities"] == [{"type": "BOLD", "offset": 4, "length": 7}]
    assert "use_html" not in payload


def test_video_with_formatted_caption():
    payload = message_payload.extract_message(
        _msg(video=_Media("video_id"), caption="vid", caption_entities=[])
    )
    assert payload["type"] == "video"
    assert payload["file_id"] == "video_id"
    assert payload["caption"] == "vid"


def test_video_has_spoiler():
    payload = message_payload.extract_message(
        _msg(video=_Media("video_id"), caption=None, caption_entities=[], has_media_spoiler=True)
    )
    assert payload["has_spoiler"] is True


def test_gif_with_formatted_caption():
    payload = message_payload.extract_message(
        _msg(animation=_Media("gif_id"), caption="gif cap", caption_entities=[])
    )
    assert payload["type"] == "animation"
    assert payload["file_id"] == "gif_id"
    assert payload["caption"] == "gif cap"


def test_gif_has_spoiler():
    payload = message_payload.extract_message(
        _msg(animation=_Media("gif_id"), caption=None, caption_entities=[], has_media_spoiler=True)
    )
    assert payload["has_spoiler"] is True


def test_document_with_caption():
    payload = message_payload.extract_message(
        _msg(
            document=_Media("doc_id"),
            caption="read me",
            caption_entities=[_entity(MessageEntityType.ITALIC, 0, 7)],
        )
    )
    assert payload["type"] == "document"
    assert payload["file_id"] == "doc_id"
    assert payload["caption"] == "read me"
    assert payload["caption_entities"][0]["type"] == "ITALIC"


def test_sticker_voice_video_note_audio():
    sticker = message_payload.extract_message(_msg(sticker=_Media("st_id")))
    assert sticker["type"] == "sticker" and sticker["file_id"] == "st_id"
    voice = message_payload.extract_message(_msg(voice=_Media("vo_id"), caption="note"))
    assert voice["type"] == "voice" and voice["caption"] == "note"
    vnote = message_payload.extract_message(_msg(video_note=_Media("vn_id")))
    assert vnote["type"] == "video_note"
    audio = message_payload.extract_message(_msg(audio=_Media("au_id")))
    assert audio["type"] == "audio"


def test_unsupported_message_type_is_none():
    assert message_payload.extract_message(_msg(poll=object())) is None
    assert message_payload.extract_message(_msg()) is None


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------

def test_url_buttons_carried_callback_dropped():
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Visit", url="https://example.com")],
            [InlineKeyboardButton("Danger", callback_data="evil")],
        ]
    )
    payload = message_payload.extract_message(
        _msg(text="go", entities=[], reply_markup=markup)
    )
    assert payload["buttons"] == [[{"text": "Visit", "url": "https://example.com"}]]


def test_no_buttons_when_no_reply_markup():
    payload = message_payload.extract_message(_msg(text="hi", entities=[]))
    assert payload["buttons"] == []


# ---------------------------------------------------------------------------
# build_send_kwargs — the outgoing Pyrogram call
# ---------------------------------------------------------------------------

def test_build_kwargs_text_entities():
    payload = message_payload.extract_message(
        _msg(text="Hello bold", entities=[_entity(MessageEntityType.BOLD, 6, 4)])
    )
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_message"
    assert kwargs["chat_id"] == 100
    assert kwargs["text"] == "Hello bold"
    assert kwargs["entities"][0].type == MessageEntityType.BOLD
    assert kwargs["entities"][0].offset == 6
    assert kwargs["entities"][0].length == 4
    assert "parse_mode" not in kwargs


def test_build_kwargs_text_html_fallback():
    payload = message_payload.extract_message(_msg(text="<b>Bold</b>", entities=[]))
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_message"
    assert kwargs["parse_mode"] == ParseMode.HTML
    assert "entities" not in kwargs


def test_build_kwargs_video_spoiler():
    payload = message_payload.extract_message(
        _msg(video=_Media("v1"), caption="cap", caption_entities=[], has_media_spoiler=True)
    )
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_video"
    assert kwargs["video"] == "v1"
    assert kwargs["caption"] == "cap"
    assert kwargs["has_spoiler"] is True


def test_build_kwargs_animation_spoiler():
    payload = message_payload.extract_message(
        _msg(animation=_Media("g1"), caption=None, caption_entities=[], has_media_spoiler=True)
    )
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_animation"
    assert kwargs["animation"] == "g1"
    assert kwargs["has_spoiler"] is True


def test_build_kwargs_photo_caption_entities():
    payload = message_payload.extract_message(
        _msg(photo=_Media("p1"), caption="x", caption_entities=[_entity(MessageEntityType.BOLD, 0, 1)])
    )
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_photo"
    assert kwargs["photo"] == "p1"
    assert kwargs["caption"] == "x"
    assert kwargs["caption_entities"][0].type == MessageEntityType.BOLD


def test_build_kwargs_sticker_has_no_caption():
    payload = message_payload.extract_message(_msg(sticker=_Media("s1")))
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_sticker"
    assert kwargs["sticker"] == "s1"
    assert "caption" not in kwargs


def test_build_kwargs_document_caption():
    payload = message_payload.extract_message(
        _msg(document=_Media("d1"), caption="doc", caption_entities=[])
    )
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    assert method == "send_document"
    assert kwargs["document"] == "d1"
    assert kwargs["caption"] == "doc"


def test_build_kwargs_buttons_markup():
    payload = message_payload.extract_message(
        _msg(
            text="go",
            entities=[],
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Visit", url="https://example.com")]]
            ),
        )
    )
    method, kwargs = message_payload.build_send_kwargs(payload, 100)
    row = kwargs["reply_markup"].inline_keyboard[0]
    assert row[0].text == "Visit"
    assert row[0].url == "https://example.com"


def test_build_kwargs_rejects_unknown_type():
    with pytest.raises(ValueError):
        message_payload.build_send_kwargs({"type": "nope"}, 100)


# ---------------------------------------------------------------------------
# send_payload — HTML fallback never loses the message / crashes
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self):
        self.calls = []

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))
        return None

    async def send_video(self, **kwargs):
        self.calls.append(("send_video", kwargs))
        return None

    async def copy_message(self, **kwargs):
        self.calls.append(("copy_message", kwargs))
        return None


def test_send_payload_plain_text():
    client = _FakeClient()
    payload = message_payload.extract_message(_msg(text="hi", entities=[]))
    assert asyncio.run(message_payload.send_payload(client, 100, payload)) is True
    assert client.calls[0][0] == "send_message"
    assert client.calls[0][1]["text"] == "hi"


def test_send_payload_html_rejected_then_plain_retry():
    client = _FakeClient()

    async def flaky_send_message(**kwargs):
        client.calls.append(("send_message", dict(kwargs)))
        if kwargs.get("parse_mode") == ParseMode.HTML:
            raise ValueError("Can't parse entities: unknown entity")
        return None

    client.send_message = flaky_send_message
    payload = message_payload.extract_message(_msg(text="<b>Bold</b>", entities=[]))
    assert asyncio.run(message_payload.send_payload(client, 100, payload)) is True
    # First attempt HTML, second attempt plain — text never lost.
    assert client.calls[0][1]["parse_mode"] == ParseMode.HTML
    assert client.calls[1][1]["text"] == "<b>Bold</b>"
    assert "parse_mode" not in client.calls[1][1]


def test_send_payload_html_rejected_plain_also_fails_raises():
    client = _FakeClient()
    attempts = []

    async def failing_send_message(**kwargs):
        attempts.append(kwargs)
        raise ValueError("rejected")

    client.send_message = failing_send_message
    payload = message_payload.extract_message(_msg(text="<b>x</b>", entities=[]))
    with pytest.raises(ValueError):
        asyncio.run(message_payload.send_payload(client, 100, payload))
    assert len(attempts) == 2  # tried HTML, then plain; still raised


def test_send_payload_video_spoiler():
    client = _FakeClient()
    payload = message_payload.extract_message(
        _msg(video=_Media("v1"), caption=None, caption_entities=[], has_media_spoiler=True)
    )
    assert asyncio.run(message_payload.send_payload(client, 100, payload)) is True
    assert client.calls[0][0] == "send_video"
    assert client.calls[0][1]["has_spoiler"] is True
    assert client.calls[0][1]["video"] == "v1"


# ---------------------------------------------------------------------------
# Entity round-trip
# ---------------------------------------------------------------------------

def test_entity_round_trip():
    user = User(id=9, first_name="F", username="f")
    original = [
        _entity(MessageEntityType.TEXT_LINK, 0, 4, url="https://x.com"),
        _entity(MessageEntityType.TEXT_MENTION, 5, 3, user=user),
        _entity(MessageEntityType.PRE, 9, 2, language="py"),
    ]
    rebuilt = message_payload._entities_from_list(message_payload._entities_to_list(original))
    assert rebuilt[0].type == MessageEntityType.TEXT_LINK and rebuilt[0].url == "https://x.com"
    assert rebuilt[1].type == MessageEntityType.TEXT_MENTION and rebuilt[1].user.id == 9
    assert rebuilt[2].type == MessageEntityType.PRE and rebuilt[2].language == "py"


# ---------------------------------------------------------------------------
# Broadcast service routing: payload path vs copy_message fallback
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

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
        self.calls = []

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

    async def find_one(self, *args, **kwargs):
        query = args[0] if args else {}
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return dict(doc)
        return None

    async def insert_one(self, doc):
        self._docs.append(doc)

    async def update_one(self, filt, update, upsert=False):
        self.calls.append((filt, update, upsert))
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in filt.items()):
                doc.update(update.get("$set", {}))
                break

    async def create_index(self, *args, **kwargs):
        pass

    def docs_status(self, broadcast_id):
        for doc in self._docs:
            if doc.get("broadcast_id") == broadcast_id:
                return doc.get("status")
        return None


class _FakeDb:
    def __init__(self, collections):
        self._collections = {
            name: _FakeCollection(docs) for name, docs in collections.items()
        }

    def __getitem__(self, name):
        return self._collections[name]


def _install_db(monkeypatch, collections):
    fake = _FakeDb(collections)
    monkeypatch.setattr(mongo, "db", fake)
    return fake


def test_run_broadcast_uses_payload_path(monkeypatch):
    payload = message_payload.extract_message(_msg(text="<b>Hi</b>", entities=[]))
    fake = _install_db(
        monkeypatch,
        {
            "broadcast_logs": [
                {
                    "broadcast_id": "BC-1",
                    "type": "dm",
                    "sender_id": 999,
                    "source_chat_id": 10,
                    "source_message_id": 20,
                    "status": "pending",
                    "payload": payload,
                }
            ],
            "users": [
                {"user_id": 1, "bot_started": True},
                {"user_id": 2, "bot_started": True},
            ],
        },
    )
    client = _FakeClient()
    stats = asyncio.run(
        broadcast.run_broadcast(client, "dm", 10, 20, 999, "BC-1")
    )
    assert stats["sent"] == 2
    methods = [c[0] for c in client.calls]
    assert methods == ["send_message", "send_message"]
    assert "copy_message" not in methods
    assert all(c[1]["parse_mode"] == ParseMode.HTML for c in client.calls)
    assert fake["broadcast_logs"].docs_status("BC-1") == "completed"


def test_run_broadcast_falls_back_to_copy_without_payload(monkeypatch):
    fake = _install_db(
        monkeypatch,
        {
            "broadcast_logs": [
                {
                    "broadcast_id": "BC-2",
                    "type": "group",
                    "sender_id": 999,
                    "source_chat_id": 10,
                    "source_message_id": 20,
                    "status": "pending",
                }
            ],
            "group_config": [{"chat_id": -1001}, {"chat_id": -1002}],
        },
    )
    client = _FakeClient()
    stats = asyncio.run(
        broadcast.run_broadcast(client, "group", 10, 20, 999, "BC-2")
    )
    assert stats["sent"] == 2
    methods = [c[0] for c in client.calls]
    assert methods == ["copy_message", "copy_message"]
    assert fake["broadcast_logs"].docs_status("BC-2") == "completed"