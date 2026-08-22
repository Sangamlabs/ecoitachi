"""Global Battle user handlers: /missions, /gbal, /gbconvert, etc."""

from __future__ import annotations

import logging

from config import config
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.common import ensure_user, safe_handler
from services.global_battle import (
    missions as missions_service,
    currency as currency_service,
    inventory as inventory_service,
    store as store_service,
    items as items_service,
)
from database import global_battle as gb_db, users as users_db
from utils import messages as msgs
from utils.money import format_money
from utils.permissions import is_sudo
from utils.sender import answer_callback, edit_html, reply_html

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot
GB_PREFIX = "gbattle:"
INV_PREFIX = "gbinv:"
STORE_PREFIX = "gbstore:"


async def _get_or_create_gb_profile(user_id: int) -> dict[str, Any]:
    """Get or create global battle profile with UID."""
    user = await users_db.get_user(user_id)
    if not user:
        user = await users_db.get_or_create_user(user_id)
    uid = user.get("unique_user_id")
    return await gb_db.get_or_create_profile(user_id, uid)


@safe_handler(feature="economy")
async def cmd_missions(client: Client, message: Message) -> None:
    """Show mission progress and global event unlock status."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    data = await missions_service.get_missions_ui(user_id)
    text = await missions_service.format_missions_message(data)

    is_admin = user_id == config.OWNER_ID or await is_sudo(user_id)
    
    # Show unlock button only for non-admins who have completed enough missions
    if not data["unlocked"] and data["completed_count"] >= data["required_for_unlock"] and not is_admin:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌍 UNLOCK GLOBAL EVENT", callback_data=f"{GB_PREFIX}unlock")]]
        )
        await reply_html(client, message, text, reply_markup=markup)
    else:
        await reply_html(client, message, text)


@safe_handler(feature="economy")
async def cmd_gbal(client: Client, message: Message) -> None:
    """Show Global Battle profile."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    profile = await _get_or_create_gb_profile(user_id)
    user = await users_db.get_user(user_id)

    # Calculate stats
    base_hp = 100
    hp_per_stat = 5
    max_hp = base_hp + profile.get("health_stat", 0) * hp_per_stat

    win_rate = 0.0
    matches = profile.get("matches", 0)
    wins = profile.get("wins", 0)
    if matches > 0:
        win_rate = (wins / matches) * 100

    # Get equipped items
    weapon_name = "None"
    armor_name = "None"
    special_name = "None"

    if profile.get("equipped_weapon_id"):
        weapon = await gb_db.get_item(profile["equipped_weapon_id"])
        if weapon:
            weapon_name = weapon.get("name", "Unknown")

    if profile.get("equipped_armor_id"):
        armor = await gb_db.get_item(profile["equipped_armor_id"])
        if armor:
            armor_name = armor.get("name", "Unknown")

    if profile.get("equipped_special_id"):
        special = await gb_db.get_special_weapon(profile["equipped_special_id"])
        if special:
            special_name = special.get("name", "Unknown")

    # Get inventory summary
    inventory = await gb_db.get_inventory(user_id)
    weapons = [i for i in inventory if i["item_id"].startswith("WPN-")]
    armor_items = [i for i in inventory if i["item_id"].startswith("ARM-")]
    specials = [i for i in inventory if i["item_id"].startswith("SP-")]
    utility = [i for i in inventory if i["item_id"].startswith("UTL-")]

    is_admin = user_id == config.OWNER_ID or await is_sudo(user_id)
    unlocked = profile.get("mission_unlocked", False) or is_admin
    unlock_status = "🔓 UNLOCKED (Admin Pre-Unlock)" if is_admin else ("🔓 UNLOCKED" if unlocked else "🔒 LOCKED")

    text = (
        f"🌍 <b>GLOBAL BATTLE PROFILE</b>\n\n"
        f"UID: <code>{user.get('unique_user_id', '—')}</code>\n\n"
        f"Level: <b>{profile.get('level', 1)}</b>\n"
        f"XP: <b>{profile.get('xp', 0):,} / {profile.get('xp_to_next', 1000):,}</b>\n\n"
        f"GB Coins: <b>{profile.get('gb_coins', 0):,}</b>\n"
        f"Global Event: <b>{unlock_status}</b>\n\n"
        f"Matches: <b>{matches}</b>\n"
        f"Wins: <b>{wins}</b>\n"
        f"Losses: <b>{profile.get('losses', 0)}</b>\n"
        f"Win Rate: <b>{win_rate:.1f}%</b>\n\n"
        f"❤️ Health: <b>{max_hp}</b> (Base {base_hp} + {profile.get('health_stat', 0)} × {hp_per_stat})\n"
        f"👊 Melee: <b>{profile.get('melee_stat', 0)}</b>\n"
        f"✨ Ability: <b>{profile.get('ability_stat', 0)}</b>\n"
        f"🛡 Durability: <b>{profile.get('durability_stat', 0)}</b>\n\n"
        f"🔫 Equipped Weapon: <b>{weapon_name}</b>\n"
        f"🛡 Equipped Armor: <b>{armor_name}</b>\n"
        f"⭐ Special Weapon: <b>{special_name}</b>\n\n"
        f"📦 Inventory: {len(weapons)} Weapons, {len(armor_items)} Armor, "
        f"{len(specials)} Special, {len(utility)} Utility"
    )

    await reply_html(client, message, text)


@safe_handler(feature="economy")
async def cmd_gbconvert(client: Client, message: Message) -> None:
    """Convert RS to GB Coins."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    args = message.command[1:]
    if not args:
        rate = await currency_service.get_conversion_rate()
        await reply_html(
            client, message,
            f"💱 <b>GLOBAL EXCHANGE</b>\n\n"
            f"Rate: 1 GB = {rate} RS\n\n"
            f"Usage: <code>/gbconvert <RS amount></code>\n"
            f"Example: <code>/gbconvert 10000</code> → 100 GB",
        )
        return

    rs_amount, err = currency_service.parse_amount_or_error(args[0])
    if err:
        await reply_html(client, message, msgs.error(f"Usage: <code>/gbconvert amount</code>. {err}"))
        return

    rate = await currency_service.get_conversion_rate()
    if rs_amount % rate != 0:
        await reply_html(
            client, message,
            msgs.error(f"Amount must be a multiple of {rate} RS (1 GB = {rate} RS)."),
        )
        return

    gb_amount = rs_amount // rate
    user = await users_db.get_user(user_id)
    rs_before = user.get("wallet", 0) if user else 0
    gb_before = await gb_db.get_profile(user_id)
    gb_before = gb_before.get("gb_coins", 0) if gb_before else 0

    gb_received = rs_amount // await currency_service.get_conversion_rate()

    text = (
        f"💱 <b>GLOBAL EXCHANGE</b>\n\n"
        f"RS Spent:\n"
        f"<b>{format_money(rs_amount)}</b>\n\n"
        f"GB Received:\n"
        f"<b>{gb_received} GB</b>\n\n"
        f"Rate: 1 GB = {await currency_service.get_conversion_rate()} RS\n\n"
        f"Current RS Wallet: {format_money(rs_before)}\n"
        f"Current GB Balance: {gb_before:,}\n\n"
        f"After conversion:\n"
        f"RS Wallet: {format_money(rs_before - rs_amount)}\n"
        f"GB Balance: {gb_before + gb_received:,}"
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"gbconvert:confirm:{rs_amount}"),
                InlineKeyboardButton("❌ Cancel", callback_data="gbconvert:cancel"),
            ]
        ]
    )

    await reply_html(client, message, text, reply_markup=markup)


async def cb_gbconvert_confirm(client: Client, callback: CallbackQuery) -> None:
    """Handle GB conversion confirmation."""
    user_id = callback.from_user.id
    data = callback.data.split(":")
    if len(data) != 3:
        await answer_callback(client, callback, "Invalid data.", show_alert=True)
        return

    try:
        rs_amount = int(data[2])
    except ValueError:
        await answer_callback(client, callback, "Invalid amount.", show_alert=True)
        return

    try:
        result = await currency_service.convert_rs_to_gb(user_id, rs_amount)
    except Exception as exc:
        await answer_callback(client, callback, str(exc), show_alert=True)
        return

    text = (
        f"✅ <b>CONVERSION COMPLETE</b>\n\n"
        f"RS Spent: {format_money(result['rs_spent'])}\n"
        f"GB Received: {result['gb_received']:,} GB\n"
        f"Rate: 1 GB = {result['rate']} RS\n\n"
        f"New RS Wallet: {format_money(result['new_rs_wallet'])}\n"
        f"New GB Balance: {result['new_gb_balance']:,}\n\n"
        f"🧾 Transaction: <code>#{result['tx_id']}</code>"
    )

    await edit_html(client, callback.message, text, reply_markup=None)
    await answer_callback(client, callback, "Conversion complete!", show_alert=True)


async def cb_gbconvert_cancel(client: Client, callback: CallbackQuery) -> None:
    """Handle conversion cancellation."""
    await edit_html(client, callback.message, "🚫 Conversion cancelled.", reply_markup=None)
    await answer_callback(client, callback, "Cancelled.")


async def cb_unlock_global(client: Client, callback: CallbackQuery) -> None:
    """Handle UNLOCK GLOBAL EVENT button."""
    user_id = callback.from_user.id
    data = await missions_service.get_missions_ui(user_id)
    is_admin = user_id == config.OWNER_ID or await is_sudo(user_id)

    if not data["unlocked"] and data["completed_count"] >= data["required_for_unlock"] and not is_admin:
        await gb_db.unlock_global_event(user_id)
        await edit_html(
            client,
            callback.message,
            msgs.success("🎉 <b>GLOBAL EVENT UNLOCKED!</b>\n\nYou have completed 3/10 economy missions.\nGlobal Battle is now available!"),
            reply_markup=None,
        )
        await answer_callback(client, callback, "Global Event unlocked!", show_alert=True)
    elif is_admin and data.get("pre_unlocked"):
        await answer_callback(client, callback, "You already have Global Event access (Admin Pre-Unlock).", show_alert=True)
    else:
        await answer_callback(client, callback, "Requirements not met.", show_alert=True)


async def cb_missions_refresh(client: Client, callback: CallbackQuery) -> None:
    """Refresh missions display."""
    user_id = callback.from_user.id
    data = await missions_service.get_missions_ui(user_id)
    text = await missions_service.format_missions_message(data)
    is_admin = user_id == config.OWNER_ID or await is_sudo(user_id)

    if not data["unlocked"] and data["completed_count"] >= data["required_for_unlock"] and not is_admin:
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🌍 UNLOCK GLOBAL EVENT", callback_data=f"{GB_PREFIX}unlock")]]
        )
    else:
        markup = None

    await edit_html(client, callback.message, text, reply_markup=markup)
    await answer_callback(client, callback)


def register(app: Client) -> None:
    app.on_message(filters.command("missions") & NOT_CHANNEL)(cmd_missions)
    app.on_message(filters.command("gbal") & NOT_CHANNEL)(cmd_gbal)
    app.on_message(filters.command("gbconvert") & NOT_CHANNEL)(cmd_gbconvert)
    app.on_message(filters.command("gbinventory") & NOT_CHANNEL)(cmd_gbinventory)
    app.on_message(filters.command("ginventory") & NOT_CHANNEL)(cmd_gbinventory)
    app.on_message(filters.command("pick") & NOT_CHANNEL)(cmd_pick)
    app.on_message(filters.command("euips") & NOT_CHANNEL)(cmd_euips)
    app.on_message(filters.command("spick") & NOT_CHANNEL)(cmd_spick)
    app.on_message(filters.command("speciallist") & NOT_CHANNEL)(cmd_speciallist)
    app.on_message(filters.command("store") & NOT_CHANNEL)(cmd_store)
    app.on_message(filters.command("buy") & NOT_CHANNEL)(cmd_buy)

    app.on_callback_query(filters.regex(r"^gbconvert:confirm:"))(cb_gbconvert_confirm)
    app.on_callback_query(filters.regex(r"^gbconvert:cancel$"))(cb_gbconvert_cancel)
    app.on_callback_query(filters.regex(rf"^{GB_PREFIX}unlock$"))(cb_unlock_global)
    app.on_callback_query(filters.regex(rf"^{GB_PREFIX}refresh$"))(cb_missions_refresh)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}pick:"))(cb_inventory_pick)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}equip:"))(cb_inventory_equip)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}spick:"))(cb_inventory_spick)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}back$"))(cb_inventory_back)
    app.on_callback_query(filters.regex(rf"^{STORE_PREFIX}buy:"))(cb_store_buy)
    app.on_callback_query(filters.regex(rf"^{STORE_PREFIX}cancel$"))(cb_store_cancel)


# ---------------------------------------------------------------------------
# Inventory Commands
# ---------------------------------------------------------------------------

@safe_handler(feature="economy")
async def cmd_gbinventory(client: Client, message: Message) -> None:
    """Show global battle inventory with pagination."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    inventory = await inventory_service.get_user_inventory(user_id)
    if not inventory:
        await reply_html(client, message, msgs.info("🎒 <b>INVENTORY</b>\n\nYour inventory is empty."))
        return

    # Group by category
    weapons = [i for i in inventory if i["category"] == "weapon"]
    armor = [i for i in inventory if i["category"] == "armor"]
    special = [i for i in inventory if i["category"] == "special"]
    utility = [i for i in inventory if i["category"] == "utility"]

    lines = ["🎒 <b>INVENTORY</b>", ""]
    
    if weapons:
        lines.append("<b>🔫 Weapons:</b>")
        for w in weapons:
            dur = f" ({w['durability_current']}/{w['max_durability']})" if w['max_durability'] > 0 else ""
            lines.append(f"  • {w['name']} x{w['quantity']}{dur}")
    
    if armor:
        lines.append("<b>🛡 Armor:</b>")
        for a in armor:
            dur = f" ({a['durability_current']}/{a['max_durability']})" if a['max_durability'] > 0 else ""
            lines.append(f"  • {a['name']} x{a['quantity']}{dur}")
    
    if special:
        lines.append("<b>⭐ Special:</b>")
        for s in special:
            lines.append(f"  • {s['name']} x{s['quantity']}")
    
    if utility:
        lines.append("<b>🔧 Utility:</b>")
        for u in utility:
            lines.append(f"  • {u['name']} x{u['quantity']}")

    total_items = sum(i['quantity'] for i in inventory)
    lines.append(f"\n<b>Total Items:</b> {total_items}")

    await reply_html(client, message, "\n".join(lines))


# ---------------------------------------------------------------------------
# Equipment Commands
# ---------------------------------------------------------------------------

@safe_handler(feature="economy")
async def cmd_pick(client: Client, message: Message) -> None:
    """Select a weapon to equip."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    # Get owned weapons
    inventory = await inventory_service.get_user_inventory(user_id)
    weapons = [i for i in inventory if i["category"] == "weapon" and i["quantity"] > 0]
    
    if not weapons:
        await reply_html(client, message, msgs.info("🔫 <b>SELECT WEAPON</b>\n\nYou don't own any weapons. Buy some from the store with <code>/store</code>."))
        return

    # Build keyboard
    rows = []
    for w in weapons:
        rows.append([InlineKeyboardButton(f"{w['name']} (x{w['quantity']})", callback_data=f"{INV_PREFIX}pick:{w['item_id']}")])
    
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"{INV_PREFIX}back")])
    
    await reply_html(
        client, message,
        "🔫 <b>SELECT WEAPON</b>\n\nChoose a weapon to equip:",
        reply_markup=InlineKeyboardMarkup(rows)
    )


@safe_handler(feature="economy")
async def cmd_euips(client: Client, message: Message) -> None:
    """Select armor to equip. Command is intentionally named /euips."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    inventory = await inventory_service.get_user_inventory(user_id)
    armor = [i for i in inventory if i["category"] == "armor" and i["quantity"] > 0]
    
    if not armor:
        await reply_html(client, message, msgs.info("🛡 <b>SELECT ARMOR</b>\n\nYou don't own any armor. Buy some from the store with <code>/store</code>."))
        return

    rows = []
    for a in armor:
        rows.append([InlineKeyboardButton(f"{a['name']} (x{a['quantity']})", callback_data=f"{INV_PREFIX}equip:{a['item_id']}")])
    
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"{INV_PREFIX}back")])
    
    await reply_html(
        client, message,
        "🛡 <b>SELECT ARMOR</b>\n\nChoose armor to equip:",
        reply_markup=InlineKeyboardMarkup(rows)
    )


@safe_handler(feature="economy")
async def cmd_spick(client: Client, message: Message) -> None:
    """Select a special weapon to equip."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    inventory = await inventory_service.get_user_inventory(user_id)
    specials = [i for i in inventory if i["category"] == "special" and i["quantity"] > 0]
    
    if not specials:
        await reply_html(client, message, msgs.info("⭐ <b>SELECT SPECIAL WEAPON</b>\n\nYou do not own any special weapon."))
        return

    rows = []
    for s in specials:
        rows.append([InlineKeyboardButton(f"{s['name']} (x{s['quantity']})", callback_data=f"{INV_PREFIX}spick:{s['item_id']}")])
    
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"{INV_PREFIX}back")])
    
    await reply_html(
        client, message,
        "⭐ <b>SELECT SPECIAL WEAPON</b>\n\nChoose a special weapon to equip:",
        reply_markup=InlineKeyboardMarkup(rows)
    )


@safe_handler(feature="economy")
async def cmd_speciallist(client: Client, message: Message) -> None:
    """View all available special weapon definitions."""
    await ensure_user(client, message)
    specials = await items_service.list_special_weapons(active_only=True)
    
    if not specials:
        await reply_html(client, message, msgs.info("⭐ <b>SPECIAL WEAPONS</b>\n\nNo special weapons available."))
        return

    lines = ["⭐ <b>SPECIAL WEAPONS</b>", ""]
    for s in specials:
        lines.append(f"• {s['special_id']} — {s['name']}: {s['description']}")
    
    await reply_html(client, message, "\n".join(lines))


# ---------------------------------------------------------------------------
# Store Commands
# ---------------------------------------------------------------------------

@safe_handler(feature="economy")
async def cmd_store(client: Client, message: Message) -> None:
    """Show the global battle store."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    preview = await store_service.get_store_preview(user_id)
    
    lines = [
        f"🏪 <b>GLOBAL STORE</b>",
        f"",
        f"💰 GB Balance: <b>{preview['gb_balance']:,} GB</b>",
        f"",
    ]

    if preview['weapons']:
        lines.append("<b>🔫 Weapons:</b>")
        for w in preview['weapons']:
            lines.append(f"  • {w['name']} — {w['price']:,} GB — {w['damage']} dmg (ID: <code>{w['item_id']}</code>)")
        lines.append("")

    if preview['armor']:
        lines.append("<b>🛡 Armor:</b>")
        for a in preview['armor']:
            lines.append(f"  • {a['name']} — {a['price']:,} GB — {a['defense']} def (ID: <code>{a['item_id']}</code>)")
        lines.append("")

    if preview['utility']:
        lines.append("<b>🔧 Utility:</b>")
        for u in preview['utility']:
            lines.append(f"  • {u['name']} — {u['price']:,} GB (ID: <code>{u['item_id']}</code>)")
        lines.append("")

    lines.append("<i>Buy with <code>/buy ITEM_ID [quantity]</code></i>")
    
    await reply_html(client, message, "\n".join(lines))


@safe_handler(feature="economy")
async def cmd_buy(client: Client, message: Message) -> None:
    """Purchase an item from the store."""
    await ensure_user(client, message)
    user_id = message.from_user.id

    args = message.command[1:]
    if not args:
        await reply_html(client, message, msgs.error("Usage: <code>/buy ITEM_ID [quantity]</code>"))
        return

    item_id = args[0].upper()
    quantity = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1

    if quantity <= 0:
        await reply_html(client, message, msgs.error("Quantity must be positive."))
        return

    try:
        item = await items_service.get_item(item_id)
        if not item:
            await reply_html(client, message, msgs.error("Item not found in store."))
            return
        
        if item.get("category") == "special":
            await reply_html(client, message, msgs.error("Special weapons cannot be purchased from the store."))
            return

        price = item.get("price", 0)
        total = price * quantity
        gb_balance = await currency_service.get_gb_balance(user_id)

        if gb_balance < total:
            await reply_html(client, message, msgs.error(f"Insufficient GB Coins. You have {gb_balance:,}, need {total:,}."))
            return

        text = (
            f"🛒 <b>CONFIRM PURCHASE</b>\n\n"
            f"Item: <b>{item['name']}</b> (ID: <code>{item_id}</code>)\n"
            f"Price: <b>{price:,} GB</b> each\n"
            f"Quantity: <b>{quantity}</b>\n"
            f"Total: <b>{total:,} GB</b>\n\n"
            f"Your GB: {gb_balance:,}\n"
            f"After: {gb_balance - total:,}"
        )

        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Buy", callback_data=f"{STORE_PREFIX}buy:{item_id}:{quantity}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"{STORE_PREFIX}cancel"),
            ]
        ])

        await reply_html(client, message, text, reply_markup=markup)

    except ValueError as exc:
        await reply_html(client, message, msgs.error(str(exc)))


# ---------------------------------------------------------------------------
# Inventory Callbacks
# ---------------------------------------------------------------------------

async def cb_inventory_pick(client: Client, callback: CallbackQuery) -> None:
    """Handle weapon pick callback."""
    user_id = callback.from_user.id
    item_id = callback.data.split(":")[2]

    try:
        await inventory_service.equip_weapon(user_id, item_id)
        item = await items_service.get_weapon(item_id)
        await edit_html(
            client, callback.message,
            msgs.success(f"🔫 Equipped <b>{item['name']}</b>!"),
            reply_markup=None
        )
    except ValueError as exc:
        await answer_callback(client, callback, str(exc), show_alert=True)


async def cb_inventory_equip(client: Client, callback: CallbackQuery) -> None:
    """Handle armor equip callback."""
    user_id = callback.from_user.id
    item_id = callback.data.split(":")[2]

    try:
        await inventory_service.equip_armor(user_id, item_id)
        item = await items_service.get_armor(item_id)
        await edit_html(
            client, callback.message,
            msgs.success(f"🛡 Equipped <b>{item['name']}</b>!"),
            reply_markup=None
        )
    except ValueError as exc:
        await answer_callback(client, callback, str(exc), show_alert=True)


async def cb_inventory_spick(client: Client, callback: CallbackQuery) -> None:
    """Handle special weapon equip callback."""
    user_id = callback.from_user.id
    item_id = callback.data.split(":")[2]

    try:
        await inventory_service.equip_special(user_id, item_id)
        special = await gb_db.get_special_weapon(item_id)
        await edit_html(
            client, callback.message,
            msgs.success(f"⭐ Equipped <b>{special['name']}</b>!"),
            reply_markup=None
        )
    except ValueError as exc:
        await answer_callback(client, callback, str(exc), show_alert=True)


async def cb_inventory_back(client: Client, callback: CallbackQuery) -> None:
    """Handle back button in inventory menus."""
    await edit_html(
        client, callback.message,
        "🎒 <b>INVENTORY</b>\n\nUse <code>/gbinventory</code> to view your items.",
        reply_markup=None
    )


# ---------------------------------------------------------------------------
# Store Callbacks
# ---------------------------------------------------------------------------

async def cb_store_buy(client: Client, callback: CallbackQuery) -> None:
    """Handle store purchase confirmation."""
    user_id = callback.from_user.id
    _, item_id, quantity = callback.data.split(":")
    quantity = int(quantity)

    try:
        result = await store_service.purchase_item(user_id, item_id, quantity)
        await edit_html(
            client, callback.message,
            msgs.success(
                f"✅ <b>PURCHASE COMPLETE</b>\n\n"
                f"Item: <b>{result['name']}</b>\n"
                f"Quantity: <b>{result['quantity']}</b>\n"
                f"Total: <b>{result['total_price']:,} GB</b>\n\n"
                f"New GB Balance: <b>{result['new_gb_balance']:,}</b>\n\n"
                f"🧾 <code>#{result['tx_id']}</code>"
            ),
            reply_markup=None
        )
    except Exception as exc:
        await answer_callback(client, callback, str(exc), show_alert=True)


async def cb_store_cancel(client: Client, callback: CallbackQuery) -> None:
    """Handle store cancel."""
    await edit_html(client, callback.message, "🚫 Purchase cancelled.", reply_markup=None)
    await answer_callback(client, callback, "Cancelled.")


def register(app: Client) -> None:
    app.on_message(filters.command("missions") & NOT_CHANNEL)(cmd_missions)
    app.on_message(filters.command("gbal") & NOT_CHANNEL)(cmd_gbal)
    app.on_message(filters.command("gbconvert") & NOT_CHANNEL)(cmd_gbconvert)
    app.on_message(filters.command("gbinventory") & NOT_CHANNEL)(cmd_gbinventory)
    app.on_message(filters.command("ginventory") & NOT_CHANNEL)(cmd_gbinventory)
    app.on_message(filters.command("pick") & NOT_CHANNEL)(cmd_pick)
    app.on_message(filters.command("euips") & NOT_CHANNEL)(cmd_euips)
    app.on_message(filters.command("spick") & NOT_CHANNEL)(cmd_spick)
    app.on_message(filters.command("speciallist") & NOT_CHANNEL)(cmd_speciallist)
    app.on_message(filters.command("store") & NOT_CHANNEL)(cmd_store)
    app.on_message(filters.command("buy") & NOT_CHANNEL)(cmd_buy)

    app.on_callback_query(filters.regex(r"^gbconvert:confirm:"))(cb_gbconvert_confirm)
    app.on_callback_query(filters.regex(r"^gbconvert:cancel$"))(cb_gbconvert_cancel)
    app.on_callback_query(filters.regex(rf"^{GB_PREFIX}unlock$"))(cb_unlock_global)
    app.on_callback_query(filters.regex(rf"^{GB_PREFIX}refresh$"))(cb_missions_refresh)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}pick:"))(cb_inventory_pick)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}equip:"))(cb_inventory_equip)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}spick:"))(cb_inventory_spick)
    app.on_callback_query(filters.regex(rf"^{INV_PREFIX}back$"))(cb_inventory_back)
    app.on_callback_query(filters.regex(rf"^{STORE_PREFIX}buy:"))(cb_store_buy)
    app.on_callback_query(filters.regex(rf"^{STORE_PREFIX}cancel$"))(cb_store_cancel)