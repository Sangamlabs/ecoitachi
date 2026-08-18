"""Broadcast handlers — /bgc (groups) and /bdm (DM), OWNER/SUDO only.

The admin replies to an existing message (text/photo/video/GIF/document/
audio with formatting) and issues /bgc or /bdm.  The original message is
copied with Pyrogram's ``copy_message`` so entities and media are preserved.
A large broadcast requires inline confirmation so nothing is sent twice.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.mongo import mongo
from handlers.common import safe_handler
from services import broadcast as broadcast_service
from utils import messages as msgs
from utils.permissions import is_sudo, sudo_only
from utils.sender import answer_callback, edit_html, reply_html

logger = logging.getLogger("broadcast")

NOT_CHANNEL = ~filters.channel & ~filters.bot

CONFIRM_PREFIX = "bg_confirm:"
CANCEL_PREFIX = "bg_cancel:"


def _confirmation_keyboard(broadcast_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm & Send", callback_data=f"{CONFIRM_PREFIX}{broadcast_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"{CANCEL_PREFIX}{broadcast_id}"),
            ]
        ]
    )


async def _prepare_broadcast(
    client: Client,
    message: Message,
    broadcast_type: str,
    type_label: str,
) -> None:
    if not message.from_user:
        return
    if message.reply_to_message is None:
        await reply_html(
            client, message,
            msgs.error(
                f"Please <b>reply to a message</b> to broadcast.\n"
                f"Usage: <code>/{'bgc' if broadcast_type == 'group' else 'bdm'}</code> (reply to a message)"
            ),
        )
        return

    reply = message.reply_to_message
    source_chat_id = reply.chat.id
    source_message_id = reply.message_id

    if broadcast_type == broadcast_service.TYPE_DM:
        targets = await broadcast_service.get_target_users()
    else:
        targets = await broadcast_service.get_target_chats()
    targets = [t for t in targets if t != message.from_user.id]

    if not targets:
        await reply_html(client, message, msgs.error("No targets found to broadcast to."))
        return

    pending = await broadcast_service.create_pending(
        broadcast_type=broadcast_type,
        sender_id=message.from_user.id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        total_targets=len(targets),
    )

    await reply_html(
        client, message,
        msgs.info(
            f"📢 <b>BROADCAST CONFIRMATION</b>\n"
            "<blockquote>"
            f"<b>Type:</b> {type_label}\n"
            f"<b>Targets:</b> {len(targets)}\n"
            f"<b>Broadcast ID:</b> <code>{pending['broadcast_id']}</code>\n"
            f"<b>Message:</b> #{source_message_id}\n"
            "</blockquote>\n"
            "Press <b>Confirm &amp; Send</b> to start, or <b>Cancel</b> to abort."
        ),
        reply_markup=_confirmation_keyboard(pending["broadcast_id"]),
    )


def register(app: Client) -> None:
    @app.on_message(filters.command("bgc") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_bgc(client: Client, message: Message):
        """/bgc — broadcast the replied-to message to all registered groups."""
        await _prepare_broadcast(client, message, broadcast_service.TYPE_GROUP, "GROUPS")

    @app.on_message(filters.command("bdm") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_bdm(client: Client, message: Message):
        """/bdm — broadcast the replied-to message to all registered users via DM."""
        await _prepare_broadcast(client, message, broadcast_service.TYPE_DM, "DM")

    @app.on_callback_query(filters.regex(rf"^{CONFIRM_PREFIX}"))
    async def cb_confirm(client: Client, callback: CallbackQuery):
        if not callback.from_user or not callback.message:
            return
        broadcast_id = callback.data[len(CONFIRM_PREFIX):]
        doc = await mongo.db[broadcast_service.COLLECTION].find_one(
            {"broadcast_id": broadcast_id}
        )
        if doc is None:
            await answer_callback(client, callback, "Broadcast record not found.", show_alert=True)
            return
        if not await is_sudo(callback.from_user.id):
            await answer_callback(client, callback, "Only the owner/sudo can confirm.", show_alert=True)
            return
        if doc.get("sender_id") != callback.from_user.id:
            await answer_callback(client, callback, "Only the requester can confirm.", show_alert=True)
            return
        if doc.get("status") == broadcast_service.STATUS_COMPLETED:
            await answer_callback(client, callback, "This broadcast was already sent.", show_alert=True)
            return

        await edit_html(client, callback.message, "📤 Sending broadcast... please wait.", reply_markup=None)
        await answer_callback(client, callback, "Broadcast started.")

        try:
            stats = await broadcast_service.run_broadcast(
                client,
                broadcast_type=doc["type"],
                source_chat_id=doc["source_chat_id"],
                source_message_id=doc["source_message_id"],
                sender_id=doc["sender_id"],
                broadcast_id=broadcast_id,
            )
        except Exception as exc:
            logger.exception("broadcast %s failed", broadcast_id)
            await edit_html(
                client, callback.message,
                msgs.error(f"Broadcast failed: {exc}"),
                reply_markup=None,
            )
            return

        await edit_html(
            client, callback.message,
            msgs.success(
                "📢 <b>BROADCAST COMPLETE</b>\n"
                "<blockquote>"
                f"<b>Broadcast ID:</b> <code>{stats['broadcast_id']}</code>\n"
                f"<b>Type:</b> {'GROUPS' if stats['type'] == 'group' else 'DM'}\n"
                f"<b>Targets:</b> {stats['total_targets']}\n"
                f"<b>Sent:</b> ✅ {stats['sent']}\n"
                f"<b>Failed:</b> ❌ {stats['failed']}\n"
                f"<b>Blocked:</b> 🚫 {stats['blocked']}\n"
                f"<b>Duration:</b> {stats.get('duration', '—')}s"
                "</blockquote>"
            ),
            reply_markup=None,
        )

    @app.on_callback_query(filters.regex(rf"^{CANCEL_PREFIX}"))
    async def cb_cancel(client: Client, callback: CallbackQuery):
        if not callback.from_user or not callback.message:
            return
        broadcast_id = callback.data[len(CANCEL_PREFIX):]
        doc = await mongo.db[broadcast_service.COLLECTION].find_one(
            {"broadcast_id": broadcast_id}
        )
        if doc is not None and doc.get("sender_id") == callback.from_user.id:
            await broadcast_service.set_status(broadcast_id, broadcast_service.STATUS_CANCELLED)
        await edit_html(
            client, callback.message,
            msgs.info(f"Broadcast <code>{broadcast_id}</code> cancelled."),
            reply_markup=None,
        )
        await answer_callback(client, callback, "Cancelled.")