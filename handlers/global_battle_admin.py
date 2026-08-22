"""Global Battle Admin Handlers.

Admin commands for managing weapons, armor, special weapons, store items, and rewards.
"""

from __future__ import annotations

import logging

from config import config
from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services.global_battle import items as items_service, inventory as inventory_service, store as store_service
from database import global_battle as gb_db
from utils import messages as msgs
from utils.money import format_money
from utils.permissions import sudo_only
from utils.sender import reply_html
from utils.validators import parse_amount_or_error

logger = logging.getLogger(__name__)

NOT_CHANNEL = ~filters.channel & ~filters.bot


# ---------------------------------------------------------------------------
# Weapon Admin Commands
# ---------------------------------------------------------------------------

@sudo_only
@safe_handler(feature="admin")
async def cmd_addweapon(client: Client, message: Message) -> None:
    """Add a new weapon to the store."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 8:
        await reply_html(
            client, message,
            msgs.error(
                "Usage: <code>/addweapon ITEM_ID name rarity damage accuracy fire_rate ammo durability price [description]</code>\n"
                "Example: <code>/addweapon WPN-UZI-001 UZI epic 25 0.85 10 30 100 5000 \"Fast-firing SMG\"</code>"
            ),
        )
        return

    try:
        item_id = args[0].upper()
        name = args[1]
        rarity = args[2].lower()
        damage = int(args[3])
        accuracy = float(args[4])
        fire_rate = int(args[5])
        ammo = int(args[6])
        durability = int(args[7])
        price = int(args[8])
        description = " ".join(args[9:]) if len(args) > 9 else ""
    except ValueError:
        await reply_html(client, message, msgs.error("Invalid numeric value."))
        return

    try:
        await items_service.create_weapon(
            item_id=item_id,
            name=name,
            rarity=rarity,
            damage=damage,
            accuracy=accuracy,
            fire_rate=fire_rate,
            ammo=ammo,
            durability=durability,
            price=price,
            description=description,
        )
    except Exception as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return

    await reply_html(
        client, message,
        msgs.success(
            f"✅ <b>WEAPON ADDED</b>\n\n"
            f"ID: <code>{item_id}</code>\n"
            f"Name: <b>{name}</b>\n"
            f"Rarity: <b>{rarity}</b>\n"
            f"Damage: <b>{damage}</b>\n"
            f"Accuracy: <b>{accuracy:.0%}</b>\n"
            f"Fire Rate: <b>{fire_rate}</b>\n"
            f"Ammo: <b>{ammo}</b>\n"
            f"Durability: <b>{durability}</b>\n"
            f"Price: <b>{format_money(price)} GB</b>"
        ),
    )


@sudo_only
@safe_handler(feature="admin")
async def cmd_editweapon(client: Client, message: Message) -> None:
    """Edit an existing weapon."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 3:
        await reply_html(
            client, message,
            msgs.error("Usage: <code>/editweapon ITEM_ID field value</code>\nFields: name, rarity, damage, accuracy, fire_rate, ammo, durability, price, description, active")
        )
        return

    item_id = args[0].upper()
    field = args[1].lower()
    value = " ".join(args[2:])

    valid_fields = {"name", "rarity", "damage", "accuracy", "fire_rate", "ammo", "durability", "price", "description", "active"}
    if field not in valid_fields:
        await reply_html(client, message, msgs.error(f"Invalid field. Valid: {', '.join(valid_fields)}"))
        return

    # Type conversion
    if field in {"damage", "fire_rate", "ammo", "durability", "price"}:
        try:
            value = int(value)
        except ValueError:
            await reply_html(client, message, msgs.error(f"{field} must be an integer."))
            return
    elif field == "accuracy":
        try:
            value = float(value)
        except ValueError:
            await reply_html(client, message, msgs.error("Accuracy must be a float (0.0-1.0)."))
            return
    elif field == "active":
        value = value.lower() in ("true", "1", "yes", "on")
    elif field == "rarity":
        value = value.lower()

    try:
        success = await items_service.update_weapon(item_id, **{field: value})
        if not success:
            await reply_html(client, message, msgs.error("Weapon not found or no changes made."))
            return
    except Exception as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return

    await reply_html(client, message, msgs.success(f"✅ Weapon <code>{item_id}</code> updated: {field} = {value}"))


@sudo_only
@safe_handler(feature="admin")
async def cmd_delweapon(client: Client, message: Message) -> None:
    """Delete a weapon."""
    await ensure_user(client, message)
    args = message.command[1:]
    if not args:
        await reply_html(client, message, msgs.error("Usage: <code>/delweapon ITEM_ID</code>"))
        return

    item_id = args[0].upper()
    success = await items_service.delete_weapon(item_id)
    if not success:
        await reply_html(client, message, msgs.error("Weapon not found."))
        return

    await reply_html(client, message, msgs.success(f"✅ Weapon <code>{item_id}</code> deleted."))


@sudo_only
@safe_handler(feature="admin")
async def cmd_weaponlist(client: Client, message: Message) -> None:
    """List all weapons."""
    await ensure_user(client, message)
    args = message.command[1:]
    active_only = True
    if args and args[0].lower() in ("all", "inactive"):
        active_only = False

    weapons = await items_service.list_weapons(active_only=active_only)
    if not weapons:
        await reply_html(client, message, msgs.info("No weapons found."))
        return

    lines = [f"🔫 <b>WEAPONS</b> ({'Active' if active_only else 'All'})", ""]
    for w in weapons:
        status = "🟢" if w.get("active", True) else "🔴"
        lines.append(f"{status} <code>{w['item_id']}</code> — <b>{w['name']}</b> ({w.get('rarity', 'common')}) — {w.get('damage', 0)} dmg — {format_money(w.get('price', 0))} GB")

    await reply_html(client, message, "\n".join(lines))


# ---------------------------------------------------------------------------
# Armor Admin Commands
# ---------------------------------------------------------------------------

@sudo_only
@safe_handler(feature="admin")
async def cmd_addarmor(client: Client, message: Message) -> None:
    """Add a new armor to the store."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 6:
        await reply_html(
            client, message,
            msgs.error(
                "Usage: <code>/addarmor ITEM_ID name rarity defense base_durability price [description]</code>\n"
                "Example: <code>/addarmor ARM-GOLD-001 \"Gold Armor\" legendary 50 100 10000 \"Shiny gold armor\"</code>"
            ),
        )
        return

    try:
        item_id = args[0].upper()
        name = args[1]
        rarity = args[2].lower()
        defense = int(args[3])
        base_durability = int(args[4])
        price = int(args[5])
        description = " ".join(args[6:]) if len(args) > 6 else ""
    except ValueError:
        await reply_html(client, message, msgs.error("Invalid numeric value."))
        return

    try:
        await items_service.create_armor(
            item_id=item_id,
            name=name,
            rarity=rarity,
            defense=defense,
            base_durability=base_durability,
            price=price,
            description=description,
        )
    except Exception as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return

    await reply_html(
        client, message,
        msgs.success(
            f"✅ <b>ARMOR ADDED</b>\n\n"
            f"ID: <code>{item_id}</code>\n"
            f"Name: <b>{name}</b>\n"
            f"Rarity: <b>{rarity}</b>\n"
            f"Defense: <b>{defense}</b>\n"
            f"Durability: <b>{base_durability}</b>\n"
            f"Price: <b>{format_money(price)} GB</b>"
        ),
    )


@sudo_only
@safe_handler(feature="admin")
async def cmd_editarmor(client: Client, message: Message) -> None:
    """Edit an existing armor."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 3:
        await reply_html(
            client, message,
            msgs.error("Usage: <code>/editarmor ITEM_ID field value</code>\nFields: name, rarity, defense, base_durability, price, description, active")
        )
        return

    item_id = args[0].upper()
    field = args[1].lower()
    value = " ".join(args[2:])

    valid_fields = {"name", "rarity", "defense", "base_durability", "price", "description", "active"}
    if field not in valid_fields:
        await reply_html(client, message, msgs.error(f"Invalid field. Valid: {', '.join(valid_fields)}"))
        return

    if field in {"defense", "base_durability", "price"}:
        try:
            value = int(value)
        except ValueError:
            await reply_html(client, message, msgs.error(f"{field} must be an integer."))
            return
    elif field == "active":
        value = value.lower() in ("true", "1", "yes", "on")
    elif field == "rarity":
        value = value.lower()

    try:
        success = await items_service.update_armor(item_id, **{field: value})
        if not success:
            await reply_html(client, message, msgs.error("Armor not found or no changes made."))
            return
    except Exception as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return

    await reply_html(client, message, msgs.success(f"✅ Armor <code>{item_id}</code> updated: {field} = {value}"))


@sudo_only
@safe_handler(feature="admin")
async def cmd_delarmor(client: Client, message: Message) -> None:
    """Delete an armor."""
    await ensure_user(client, message)
    args = message.command[1:]
    if not args:
        await reply_html(client, message, msgs.error("Usage: <code>/delarmor ITEM_ID</code>"))
        return

    item_id = args[0].upper()
    success = await items_service.delete_armor(item_id)
    if not success:
        await reply_html(client, message, msgs.error("Armor not found."))
        return

    await reply_html(client, message, msgs.success(f"✅ Armor <code>{item_id}</code> deleted."))


@sudo_only
@safe_handler(feature="admin")
async def cmd_armorlist(client: Client, message: Message) -> None:
    """List all armor."""
    await ensure_user(client, message)
    args = message.command[1:]
    active_only = True
    if args and args[0].lower() in ("all", "inactive"):
        active_only = False

    armor = await items_service.list_armor(active_only=active_only)
    if not armor:
        await reply_html(client, message, msgs.info("No armor found."))
        return

    lines = [f"🛡 <b>ARMOR</b> ({'Active' if active_only else 'All'})", ""]
    for a in armor:
        status = "🟢" if a.get("active", True) else "🔴"
        lines.append(f"{status} <code>{a['item_id']}</code> — <b>{a['name']}</b> ({a.get('rarity', 'common')}) — {a.get('defense', 0)} def — {format_money(a.get('price', 0))} GB")

    await reply_html(client, message, "\n".join(lines))


# ---------------------------------------------------------------------------
# Special Weapon Admin Commands
# ---------------------------------------------------------------------------

@sudo_only
@safe_handler(feature="admin")
async def cmd_addspecial(client: Client, message: Message) -> None:
    """Add a new special weapon."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 4:
        await reply_html(
            client, message,
            msgs.error(
                "Usage: <code>/addspecial SPECIAL_ID name damage description</code>\n"
                "Example: <code>/addspecial SP-001 \"Susanoo Blade\" 100 \"Unleashes a divine slash\"</code>"
            ),
        )
        return

    special_id = args[0].upper()
    name = args[1]
    try:
        damage = int(args[2])
    except ValueError:
        await reply_html(client, message, msgs.error("Damage must be an integer."))
        return
    description = " ".join(args[3:])

    try:
        await items_service.create_special_weapon(
            special_id=special_id,
            name=name,
            description=description,
            damage=damage,
            effect="special",
        )
    except Exception as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return

    await reply_html(
        client, message,
        msgs.success(
            f"✅ <b>SPECIAL WEAPON ADDED</b>\n\n"
            f"ID: <code>{special_id}</code>\n"
            f"Name: <b>{name}</b>\n"
            f"Damage: <b>{damage}</b>\n"
            f"Description: {description}"
        ),
    )


@sudo_only
@safe_handler(feature="admin")
async def cmd_editspecial(client: Client, message: Message) -> None:
    """Edit a special weapon."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 3:
        await reply_html(
            client, message,
            msgs.error("Usage: <code>/editspecial SPECIAL_ID field value</code>\nFields: name, damage, description, active")
        )
        return

    special_id = args[0].upper()
    field = args[1].lower()
    value = " ".join(args[2:])

    valid_fields = {"name", "damage", "description", "active"}
    if field not in valid_fields:
        await reply_html(client, message, msgs.error(f"Invalid field. Valid: {', '.join(valid_fields)}"))
        return

    if field == "damage":
        try:
            value = int(value)
        except ValueError:
            await reply_html(client, message, msgs.error("Damage must be an integer."))
            return
    elif field == "active":
        value = value.lower() in ("true", "1", "yes", "on")

    try:
        success = await items_service.update_special_weapon(special_id, **{field: value})
        if not success:
            await reply_html(client, message, msgs.error("Special weapon not found or no changes made."))
            return
    except Exception as exc:
        await reply_html(client, message, msgs.error(str(exc)))
        return

    await reply_html(client, message, msgs.success(f"✅ Special weapon <code>{special_id}</code> updated: {field} = {value}"))


@sudo_only
@safe_handler(feature="admin")
async def cmd_delspecial(client: Client, message: Message) -> None:
    """Delete a special weapon."""
    await ensure_user(client, message)
    args = message.command[1:]
    if not args:
        await reply_html(client, message, msgs.error("Usage: <code>/delspecial SPECIAL_ID</code>"))
        return

    special_id = args[0].upper()
    success = await items_service.delete_special_weapon(special_id)
    if not success:
        await reply_html(client, message, msgs.error("Special weapon not found."))
        return

    await reply_html(client, message, msgs.success(f"✅ Special weapon <code>{special_id}</code> deleted."))


@sudo_only
@safe_handler(feature="admin")
async def cmd_speciallist_admin(client: Client, message: Message) -> None:
    """List all special weapons (admin view)."""
    await ensure_user(client, message)
    args = message.command[1:]
    active_only = True
    if args and args[0].lower() in ("all", "inactive"):
        active_only = False

    specials = await items_service.list_special_weapons(active_only=active_only)
    if not specials:
        await reply_html(client, message, msgs.info("No special weapons found."))
        return

    lines = [f"⭐ <b>SPECIAL WEAPONS</b> ({'Active' if active_only else 'All'})", ""]
    for s in specials:
        status = "🟢" if s.get("active", True) else "🔴"
        lines.append(f"{status} <code>{s['special_id']}</code> — <b>{s['name']}</b> — {s.get('damage', 0)} dmg — {s.get('description', '')}")

    await reply_html(client, message, "\n".join(lines))


# ---------------------------------------------------------------------------
# Store/Admin Commands
# ---------------------------------------------------------------------------

@sudo_only
@safe_handler(feature="admin")
async def cmd_sgivespecial(client: Client, message: Message) -> None:
    """Grant a special weapon to a user."""
    await ensure_user(client, message)
    args = message.command[1:]
    
    if len(args) < 2:
        await reply_html(
            client, message,
            msgs.error("Usage: <code>/sgivespecial @user SPECIAL_ID</code> or reply + <code>/sgivespecial SPECIAL_ID</code>")
        )
        return

    target_arg = args[0]
    special_id = args[1].upper()

    # Resolve target user
    from services import identity as identity_service
    target_doc = await identity_service.resolve_user(client, message, target_arg, create=True)
    if not target_doc:
        await reply_html(client, message, msgs.error("Target user not found."))
        return

    target_id = target_doc["user_id"]
    
    special = await gb_db.get_special_weapon(special_id)
    if not special:
        await reply_html(client, message, msgs.error("Special weapon not found."))
        return

    success = await inventory_service.add_to_inventory(target_id, special_id, 1)
    if not success:
        await reply_html(client, message, msgs.error("Failed to grant special weapon."))
        return

    # Audit log
    from services import transaction as tx_service
    await tx_service.record(
        user_id=target_id,
        ttype="GB_SPECIAL_GRANT",
        amount=0,
        balance_before=0,
        balance_after=0,
        metadata={
            "special_id": special_id,
            "special_name": special["name"],
            "granted_by": message.from_user.id,
        },
    )

    await reply_html(
        client, message,
        msgs.success(f"✅ Granted <code>{special['name']}</code> (<code>{special_id}</code>) to user <code>{target_id}</code>.")
    )


# Register admin commands
def register_admin(app: Client) -> None:
    app.on_message(filters.command("addweapon") & NOT_CHANNEL)(cmd_addweapon)
    app.on_message(filters.command("editweapon") & NOT_CHANNEL)(cmd_editweapon)
    app.on_message(filters.command("delweapon") & NOT_CHANNEL)(cmd_delweapon)
    app.on_message(filters.command("weaponlist") & NOT_CHANNEL)(cmd_weaponlist)

    app.on_message(filters.command("addarmor") & NOT_CHANNEL)(cmd_addarmor)
    app.on_message(filters.command("editararmor") & NOT_CHANNEL)(cmd_editarmor)
    app.on_message(filters.command("delarmor") & NOT_CHANNEL)(cmd_delarmor)
    app.on_message(filters.command("armorlist") & NOT_CHANNEL)(cmd_armorlist)

    app.on_message(filters.command("addspecial") & NOT_CHANNEL)(cmd_addspecial)
    app.on_message(filters.command("editspecial") & NOT_CHANNEL)(cmd_editspecial)
    app.on_message(filters.command("delspecial") & NOT_CHANNEL)(cmd_delspecial)
    app.on_message(filters.command("speciallist") & NOT_CHANNEL)(cmd_speciallist_admin)

    app.on_message(filters.command("sgivespecial") & NOT_CHANNEL)(cmd_sgivespecial)