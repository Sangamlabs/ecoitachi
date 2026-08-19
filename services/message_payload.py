"""Central broadcast message extractor/serializer.

Ports the proven extraction + formatting approach of the legacy MessageMaker
module into ECOITACHI's Pyrogram architecture.  ONE serializer feeds both
``/bgc`` and ``/bdm``: the admin's replied message is inspected once into a
JSON-serializable payload, and sends go through the correct Pyrogram method
with entities preserved.

Rules:
* When the original message already carries Telegram ``entities`` /
  ``caption_entities`` they are preserved and sent directly — they are never
  rewritten to HTML or Markdown.
* When the text contains literal HTML tags (``<b>..</b>``, ``<tg-spoiler>..``
  etc.) and has no entities, it is sent with ``parse_mode=HTML``.  If Telegram
  rejects the markup the original text is re-sent as plain text — the message
  is never lost and the broadcast worker never crashes.
* ``has_spoiler`` is preserved for photo/video/animation.
* Only URL inline buttons are carried over from the source message; callback
  buttons are stripped so arbitrary callback data can never be replayed
  against other bots.
"""

from __future__ import annotations

import logging
from typing import Any

from pyrogram import Client
from pyrogram.enums import MessageEntityType, ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, User

logger = logging.getLogger("broadcast")

TYPE_TEXT = "text"
TYPE_PHOTO = "photo"
TYPE_VIDEO = "video"
TYPE_ANIMATION = "animation"
TYPE_DOCUMENT = "document"
TYPE_STICKER = "sticker"
TYPE_VOICE = "voice"
TYPE_VIDEO_NOTE = "video_note"
TYPE_AUDIO = "audio"

SUPPORTED_TYPES = frozenset(
    {
        TYPE_TEXT,
        TYPE_PHOTO,
        TYPE_VIDEO,
        TYPE_ANIMATION,
        TYPE_DOCUMENT,
        TYPE_STICKER,
        TYPE_VOICE,
        TYPE_VIDEO_NOTE,
        TYPE_AUDIO,
    }
)

# media type -> (pyrogram send method, file_id argument name, supports spoiler)
_MEDIA_METHODS: dict[str, tuple[str, str, bool]] = {
    TYPE_PHOTO: ("send_photo", "photo", True),
    TYPE_VIDEO: ("send_video", "video", True),
    TYPE_ANIMATION: ("send_animation", "animation", True),
    TYPE_DOCUMENT: ("send_document", "document", False),
    TYPE_STICKER: ("send_sticker", "sticker", False),
    TYPE_VOICE: ("send_voice", "voice", False),
    TYPE_VIDEO_NOTE: ("send_video_note", "video_note", False),
    TYPE_AUDIO: ("send_audio", "audio", False),
}

# Types whose Telegram API supports a caption.
_CAPTION_TYPES = frozenset(
    {TYPE_PHOTO, TYPE_VIDEO, TYPE_ANIMATION, TYPE_DOCUMENT, TYPE_VOICE, TYPE_AUDIO}
)

# Message attributes probed in priority order for media classification.
_MEDIA_ATTRS = (
    (TYPE_PHOTO, "photo"),
    (TYPE_VIDEO, "video"),
    (TYPE_ANIMATION, "animation"),
    (TYPE_DOCUMENT, "document"),
    (TYPE_STICKER, "sticker"),
    (TYPE_VOICE, "voice"),
    (TYPE_VIDEO_NOTE, "video_note"),
    (TYPE_AUDIO, "audio"),
)


# ─── Entity serialization ───────────────────────────────────────────────────

def _entity_to_dict(entity: MessageEntity) -> dict[str, Any]:
    """Serialize a Pyrogram MessageEntity to a JSON-safe dict."""
    data: dict[str, Any] = {
        "type": entity.type.name,
        "offset": entity.offset,
        "length": entity.length,
    }
    if entity.url:
        data["url"] = entity.url
    if entity.language:
        data["language"] = entity.language
    if entity.custom_emoji_id:
        data["custom_emoji_id"] = int(entity.custom_emoji_id)
    if entity.user:
        user = entity.user
        data["user"] = {
            "id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
        }
    return data


def _entity_from_dict(data: dict[str, Any]) -> MessageEntity:
    """Rebuild a Pyrogram MessageEntity from a serialized dict."""
    kwargs: dict[str, Any] = {
        "type": MessageEntityType[data["type"]],
        "offset": int(data["offset"]),
        "length": int(data["length"]),
    }
    if data.get("url"):
        kwargs["url"] = data["url"]
    if data.get("language"):
        kwargs["language"] = data["language"]
    if data.get("custom_emoji_id"):
        kwargs["custom_emoji_id"] = int(data["custom_emoji_id"])
    user = data.get("user")
    if user:
        kwargs["user"] = User(
            id=int(user["id"]),
            first_name=user.get("first_name", "") or "",
            last_name=user.get("last_name") or "",
            username=user.get("username") or "",
        )
    return MessageEntity(**kwargs)


def _entities_to_list(entities: list[MessageEntity] | None) -> list[dict[str, Any]]:
    return [_entity_to_dict(e) for e in (entities or [])]


def _entities_from_list(data: list[dict[str, Any]] | None) -> list[MessageEntity] | None:
    if not data:
        return None
    return [_entity_from_dict(e) for e in data]


# ─── Button serialization ───────────────────────────────────────────────────

def _buttons_to_list(reply_markup: InlineKeyboardMarkup | None) -> list[list[dict[str, str]]]:
    """Extract only URL buttons; callback buttons are never carried over."""
    if reply_markup is None:
        return []
    keyboard = getattr(reply_markup, "inline_keyboard", None)
    if not keyboard:
        return []
    rows: list[list[dict[str, str]]] = []
    for row in keyboard:
        url_row: list[dict[str, str]] = []
        for button in row:
            if getattr(button, "url", None):
                url_row.append({"text": button.text, "url": button.url})
        if url_row:
            rows.append(url_row)
    return rows


def _markup_from_buttons(buttons: list[list[dict[str, str]]] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    keyboard = [
        [InlineKeyboardButton(button["text"], url=button["url"]) for button in row]
        for row in buttons
    ]
    return InlineKeyboardMarkup(keyboard)


# ─── Extraction ─────────────────────────────────────────────────────────────

def extract_message(message: Message) -> dict[str, Any] | None:
    """Inspect a Pyrogram Message into a serializable broadcast payload.

    Returns ``None`` for unsupported message types (contact, location, poll,
    dice, game, invoice, …).  Extraction is defensive: any unexpected
    attribute shape is logged and treated as unsupported rather than crashing.
    """
    try:
        if getattr(message, "text", None) is not None:
            payload: dict[str, Any] = {
                "type": TYPE_TEXT,
                "text": message.text,
                "entities": _entities_to_list(getattr(message, "entities", None)),
            }
        else:
            media = None
            media_type = None
            for mtype, attr in _MEDIA_ATTRS:
                obj = getattr(message, attr, None)
                if obj is not None:
                    media = obj
                    media_type = mtype
                    break
            if media is None:
                return None
            file_id = getattr(media, "file_id", None)
            if not file_id:
                return None
            payload = {
                "type": media_type,
                "file_id": file_id,
                "caption": getattr(message, "caption", None),
                "caption_entities": _entities_to_list(
                    getattr(message, "caption_entities", None)
                ),
            }
            if getattr(message, "has_media_spoiler", False):
                payload["has_spoiler"] = True

        payload["buttons"] = _buttons_to_list(getattr(message, "reply_markup", None))

        # HTML fallback: literal HTML tags authored by the admin, no entities.
        text = payload.get("text") or payload.get("caption")
        has_entities = bool(payload.get("entities") or payload.get("caption_entities"))
        if text and not has_entities and "<" in text:
            payload["use_html"] = True

        return payload
    except Exception as exc:  # noqa: BLE001 - extraction must never crash a broadcast
        logger.warning("message extraction failed: %s", exc)
        return None


# ─── Sending ────────────────────────────────────────────────────────────────

def build_send_kwargs(payload: dict[str, Any], chat_id: int) -> tuple[str, dict[str, Any]]:
    """Return ``(pyrogram_send_method, kwargs)`` for a payload to one chat.

    Entities are passed directly when present; ``parse_mode=HTML`` is used only
    as a fallback for literal-HTML text without entities.  ``has_spoiler`` and
    URL buttons are forwarded where the API supports them.
    """
    media_type = payload.get("type")
    if media_type not in SUPPORTED_TYPES:
        raise ValueError(f"Unsupported broadcast payload type: {media_type}")

    reply_markup = _markup_from_buttons(payload.get("buttons") or None)
    kwargs: dict[str, Any] = {"chat_id": chat_id, "reply_markup": reply_markup}

    if media_type == TYPE_TEXT:
        kwargs["text"] = payload.get("text", "")
        entities = _entities_from_list(payload.get("entities"))
        if entities:
            kwargs["entities"] = entities
        elif payload.get("use_html"):
            kwargs["parse_mode"] = ParseMode.HTML
        return "send_message", kwargs

    method, media_arg, supports_spoiler = _MEDIA_METHODS[media_type]
    kwargs[media_arg] = payload.get("file_id")

    if media_type in _CAPTION_TYPES and payload.get("caption"):
        kwargs["caption"] = payload["caption"]
        caption_entities = _entities_from_list(payload.get("caption_entities"))
        if caption_entities:
            kwargs["caption_entities"] = caption_entities
        elif payload.get("use_html"):
            kwargs["parse_mode"] = ParseMode.HTML

    if supports_spoiler and payload.get("has_spoiler"):
        kwargs["has_spoiler"] = True

    return method, kwargs


async def send_payload(client: Client, chat_id: int, payload: dict[str, Any]) -> bool:
    """Send a serialized payload to one chat; returns success.

    When the payload is flagged ``use_html`` and Telegram rejects the markup,
    the original text/caption is re-sent as plain text — the message is never
    lost and the broadcast worker never crashes.  Other errors propagate to
    the caller (FloodWait handling / classification lives in the broadcast
    service).
    """
    method, kwargs = build_send_kwargs(payload, chat_id)
    send = getattr(client, method)
    try:
        await send(**kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 - see module docstring
        if payload.get("use_html"):
            logger.info(
                "HTML parse rejected for chat %s (%s); retrying plain", chat_id, exc
            )
            try:
                kwargs.pop("parse_mode", None)
                kwargs.pop("entities", None)
                kwargs.pop("caption_entities", None)
                await send(**kwargs)
                return True
            except Exception:  # noqa: BLE001
                logger.warning("plain retry also failed for chat %s", chat_id)
        raise