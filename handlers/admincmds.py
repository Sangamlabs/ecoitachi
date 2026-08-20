"""OWNER-only dynamic admin command control panel.

The command list is discovered at runtime from the ACTUAL registered Pyrogram
dispatcher: every ``MessageHandler`` whose wrapped callback carries the
``_admin_perm`` marker (stamped by the permission decorators in
:mod:`utils.permissions`) is an admin command.  No manual command list is
maintained.

The panel re-verifies the callback user is the real OWNER (``config.OWNER_ID``)
on every interaction, namespaces callbacks under the ``admincmds:`` prefix, and
writes/reads permission overrides through the existing central settings system
so changes apply immediately (no restart, no cache flush beyond the one entry).
"""

from __future__ import annotations

import math
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.adminpanel import CATEGORIES as PANEL_CATEGORIES
from handlers.common import safe_handler
from services import settings as settings_service
from services import transaction as tx_service
from utils.permissions import (
    NON_DELEGATABLE_ADMIN_COMMANDS,
    is_owner,
    invalidate_command_permission,
    normalize_command_name,
    owner_only,
)
from utils.sender import answer_callback, edit_html, reply_html

PREFIX = "admincmds:"
LIST_CALLBACK = f"{PREFIX}list:"
CMD_CALLBACK = f"{PREFIX}cmd:"
SET_CALLBACK = f"{PREFIX}set:"
BACK_CALLBACK = f"{PREFIX}back"
CLOSE_CALLBACK = f"{PREFIX}close"

PAGE_SIZE = 6

# Discovered admin command inventory (built lazily once per process).
_DISCOVERY_CACHE: list[dict] | None = None


def reset_discovery() -> None:
    """Forget the discovered inventory (used by tests)."""
    global _DISCOVERY_CACHE
    _DISCOVERY_CACHE = None


def _collect_commands(flt) -> set[str]:
    """Recursively collect command names from a (possibly combined) filter."""
    names = set()
    seen = set()

    def walk(node):
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        cmds = getattr(node, "commands", None)
        if cmds:
            names.update(str(c).lower() for c in cmds)
        walk(getattr(node, "base", None))
        walk(getattr(node, "other", None))

    walk(flt)
    return names


def _category_map() -> dict[str, str]:
    """command -> category key, derived from the existing adminpanel CATEGORIES."""
    mapping = {}
    for key, (_, lines) in PANEL_CATEGORIES.items():
        for line in lines:
            for m in re.finditer(r"/\b([a-z0-9_]+)\b", line.lower()):
                mapping.setdefault(m.group(1), key)
    return mapping


def discover_admin_commands(client: Client) -> list[dict]:
    """Return the ACTUAL registered admin commands (command, perm, category).

    ``perm`` is each command's CURRENT default permission (the decorator's
    marker); stored overrides are layered on top at check time.
    """
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE is not None:
        return _DISCOVERY_CACHE
    seen = set()
    commands = []
    cat_map = _category_map()
    for group in getattr(client.dispatcher, "groups", []):
        for handler in group:
            perm = getattr(getattr(handler, "callback", None), "_admin_perm", None)
            if perm is None:
                continue
            for name in _collect_commands(getattr(handler, "filters", None)):
                name = normalize_command_name(name)
                if not name or name in seen:
                    continue
                seen.add(name)
                commands.append(
                    {
                        "command": name,
                        "perm": perm,
                        "category": cat_map.get(name, "other"),
                    }
                )
    commands.sort(key=lambda c: (c["category"], c["command"]))
    _DISCOVERY_CACHE = commands
    return commands


def _category_label(key: str) -> str:
    entry = PANEL_CATEGORIES.get(key)
    return entry[0] if entry else "🗂 Other"


def _mode_label(perm: str, non_delegatable: bool) -> str:
    if non_delegatable:
        return "🔒 OWNER ONLY"
    return "🟢 OWNER + SUDO" if perm == "admin" else "🔴 OWNER ONLY"


def _list_text(commands: list[dict], page: int, total_pages: int) -> str:
    start = (page - 1) * PAGE_SIZE
    chunk = commands[start:start + PAGE_SIZE]
    lines = [f"🛡 <b>ECOITACHI ADMIN COMMANDS</b>\nPage {page}/{total_pages}\n"]
    last_cat = None
    for c in chunk:
        if c["category"] != last_cat:
            lines.append(f"<b>{_category_label(c['category'])}</b>")
            last_cat = c["category"]
        nd = c["command"] in NON_DELEGATABLE_ADMIN_COMMANDS
        lines.append(f"/{c['command']}  {_mode_label(c['perm'], nd)}")
    return "\n".join(lines)


def _list_markup(page: int, total_pages: int, chunk: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for c in chunk:
        row.append(InlineKeyboardButton(f"/{c['command']}", callback_data=f"{CMD_CALLBACK}{c['command']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("◀ Previous", callback_data=f"{LIST_CALLBACK}{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"{LIST_CALLBACK}{page}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"{LIST_CALLBACK}{page + 1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton("❌ Close", callback_data=CLOSE_CALLBACK)])
    return InlineKeyboardMarkup(rows)


def _cmd_text(command: str, perm: str, non_delegatable: bool) -> str:
    current = "🔒 OWNER ONLY" if non_delegatable else _mode_label(perm, False)
    note = "\n\n<i>🔒 This command cannot be delegated to SUDO.</i>" if non_delegatable else ""
    return (
        f"🛡 <b>COMMAND PERMISSION</b>\n"
        f"Command: <code>/{command}</code>\n"
        f"Current: {current}{note}\n\n"
        f"<i>Choose a permission:</i>"
    )


def _cmd_markup(command: str, non_delegatable: bool) -> InlineKeyboardMarkup:
    rows = []
    if not non_delegatable:
        rows.append([InlineKeyboardButton("🔴 OWNER ONLY", callback_data=f"{SET_CALLBACK}{command}:owner")])
        rows.append([InlineKeyboardButton("🟢 OWNER + SUDO", callback_data=f"{SET_CALLBACK}{command}:admin")])
    rows.append([InlineKeyboardButton("🔙 Back to Admin Commands", callback_data=BACK_CALLBACK)])
    rows.append([InlineKeyboardButton("❌ Close", callback_data=CLOSE_CALLBACK)])
    return InlineKeyboardMarkup(rows)


async def _render_list(client: Client, message, commands: list[dict], page: int, *, reply: bool = False) -> None:
    total_pages = max(1, math.ceil(len(commands) / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    start = (page - 1) * PAGE_SIZE
    text = _list_text(commands, page, total_pages)
    markup = _list_markup(page, total_pages, commands[start:start + PAGE_SIZE])
    if reply:  # initial /admincmds reply
        await reply_html(client, message, text, reply_markup=markup)
    else:  # callback edit
        await edit_html(client, message, text, reply_markup=markup)


def register(app: Client) -> None:
    @app.on_message(filters.command("admincmds") & ~filters.channel & ~filters.bot)
    @owner_only
    @safe_handler(feature="admin")
    async def cmd_admincmds(client: Client, message):
        """`/admincmds` — OWNER-only control panel for all registered admin commands."""
        commands = discover_admin_commands(client)
        await _render_list(client, message, commands, 1, reply=True)

    @app.on_callback_query(filters.regex(rf"^{PREFIX}"))
    async def cb_admincmds(client: Client, callback: CallbackQuery):
        if not callback.from_user or not callback.message:
            return
        if not await is_owner(callback.from_user.id):
            await answer_callback(
                client, callback, "Only the bot owner can use this panel.", show_alert=True
            )
            return

        data = callback.data
        commands = discover_admin_commands(client)
        by_name = {c["command"]: c for c in commands}

        if data == CLOSE_CALLBACK:
            try:
                await callback.message.delete()
            except Exception:
                await edit_html(client, callback.message, "Panel closed.", reply_markup=None)
            await answer_callback(client, callback, "Panel closed.")
            return

        if data == BACK_CALLBACK:
            await _render_list(client, callback.message, commands, 1)
            await answer_callback(client, callback)
            return

        if data.startswith(LIST_CALLBACK):
            raw = data[len(LIST_CALLBACK):]
            page = int(raw) if raw.isdigit() else 1
            await _render_list(client, callback.message, commands, page)
            await answer_callback(client, callback)
            return

        if data.startswith(CMD_CALLBACK):
            name = normalize_command_name(data[len(CMD_CALLBACK):])
            entry = by_name.get(name)
            if entry is None:
                await answer_callback(client, callback, "Unknown command.", show_alert=True)
                return
            nd = name in NON_DELEGATABLE_ADMIN_COMMANDS
            await edit_html(
                client, callback.message,
                _cmd_text(name, entry["perm"], nd),
                reply_markup=_cmd_markup(name, nd),
            )
            await answer_callback(client, callback)
            return

        if data.startswith(SET_CALLBACK):
            rest = data[len(SET_CALLBACK):]
            name, _, mode = rest.rpartition(":")
            name = normalize_command_name(name)
            entry = by_name.get(name)
            if entry is None:
                await answer_callback(client, callback, "Unknown command.", show_alert=True)
                return
            if mode not in ("owner", "admin"):
                await answer_callback(client, callback, "Invalid permission.", show_alert=True)
                return
            if name in NON_DELEGATABLE_ADMIN_COMMANDS:
                await answer_callback(
                    client, callback,
                    "🔒 This command cannot be delegated to SUDO.", show_alert=True
                )
                return

            stored = await settings_service.get_command_permission(name)
            old = stored if stored in ("owner", "admin") else entry["perm"]
            if old == mode:
                await edit_html(
                    client, callback.message,
                    _cmd_text(name, mode, False),
                    reply_markup=_cmd_markup(name, False),
                )
                await answer_callback(client, callback, f"/{name} is already {mode.upper()}.")
                return

            await settings_service.set_command_permission(name, mode, callback.from_user.id)
            await tx_service.record(
                user_id=callback.from_user.id,
                ttype=tx_service.ADMIN_COMMAND_PERMISSION_CHANGED,
                amount=0,
                balance_before=0,
                balance_after=0,
                metadata={
                    "command": name,
                    "old": old,
                    "new": mode,
                    "changed_by": callback.from_user.id,
                },
            )
            invalidate_command_permission(name)
            entry["perm"] = mode  # list rendering reflects the new state immediately

            await edit_html(
                client, callback.message,
                f"✅ <b>Permission updated</b>\n"
                f"<code>/{name}</code>\n"
                f"Mode: {'🔴 OWNER ONLY' if mode == 'owner' else '🟢 OWNER + SUDO'}",
                reply_markup=_cmd_markup(name, False),
            )
            await answer_callback(client, callback, f"/{name} -> {mode.upper()}")
            return

        await answer_callback(client, callback, "Unknown action.", show_alert=True)