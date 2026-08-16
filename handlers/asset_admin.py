"""Assets Market admin handlers.

All commands are SUDO (and implicitly OWNER) only.  Every mutation is
validated in the service layer and written to the admin audit log.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import asset_listings as listings_service
from services import assets as asset_service
from utils import messages as msgs
from utils.money import format_money
from utils.permissions import sudo_only
from utils.sender import reply_html

NOT_CHANNEL = ~filters.channel & ~filters.bot


def _qty(args: list[str]) -> str | None:
    return args[1] if len(args) > 1 else None


def _page(args: list[str]) -> int:
    if args and args[-1].isdigit():
        return int(args[-1])
    return 1


def register(app: Client) -> None:
    @app.on_message(filters.command("addasset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_addasset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 5:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/addasset SYMBOL name category base_price volatility</code>\n"
                    f"Categories: {', '.join(asset_service_category_list())}\n"
                    "Example: <code>/addasset VILLA Luxury-Villa REAL_ESTATE 20000000 0.015</code>"
                ),
            )
            return
        symbol = args[0].upper()
        base_price = int(float(args[-2]))
        volatility = float(args[-1])
        category = args[-3].upper()
        name = " ".join(args[1:-3])
        try:
            asset = await asset_service.create_asset(
                message.from_user.id,
                symbol=symbol,
                name=name,
                category=category,
                base_price=base_price,
                volatility=volatility,
            )
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Listed asset <code>{asset['symbol']}</code> — {asset.get('name', '')} "
                f"({asset['asset_id']}).\n"
                f"💵 Base price: {format_money(base_price)} · 📊 Volatility: {volatility}"
            ),
        )

    @app.on_message(filters.command("editasset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_editasset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 3:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/editasset SYMBOL field value</code>\n"
                    f"Editable fields: {', '.join(sorted(asset_service.EDITABLE_FIELDS))}"
                ),
            )
            return
        symbol = args[0].upper()
        field = args[1].lower()
        value = " ".join(args[2:])
        try:
            asset = await asset_service.update_asset_fields(
                message.from_user.id, symbol, {field: value}
            )
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Updated <code>{asset['symbol']}</code> → <code>{field}</code> = "
                f"<b>{str(value)}</b>"
            ),
        )

    @app.on_message(filters.command("rmasset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_rmasset(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = message.command[1].upper() if len(message.command) > 1 else None
        if not symbol:
            await reply_html(client, message, msgs.error("Usage: <code>/rmasset SYMBOL</code>"))
            return
        try:
            asset = await asset_service.deactivate_asset(message.from_user.id, symbol)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Delisted <code>{asset['symbol']}</code> ({asset.get('name', '')}).\n"
                f"Existing holders keep their assets but can no longer trade them."
            ),
        )

    @app.on_message(filters.command("restoreasset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_restoreasset(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = message.command[1].upper() if len(message.command) > 1 else None
        if not symbol:
            await reply_html(client, message, msgs.error("Usage: <code>/restoreasset SYMBOL</code>"))
            return
        try:
            asset = await asset_service.restore_asset(message.from_user.id, symbol)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(f"Restored <code>{asset['symbol']}</code> to the market."),
        )

    @app.on_message(filters.command("assetprice") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetprice(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(client, message, msgs.error("Usage: <code>/assetprice SYMBOL price</code>"))
            return
        symbol = args[0].upper()
        try:
            price = int(float(args[1]))
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid price."))
            return
        try:
            asset = await asset_service.set_price(message.from_user.id, symbol, price)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(f"Set <code>{asset['symbol']}</code> price to {format_money(price)}."),
        )

    @app.on_message(filters.command("assetvolatility") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetvolatility(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/assetvolatility SYMBOL 0.05</code>"),
            )
            return
        try:
            volatility = float(args[1])
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid volatility."))
            return
        try:
            asset = await asset_service.set_volatility(message.from_user.id, args[0].upper(), volatility)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(f"Set <code>{asset['symbol']}</code> volatility to {volatility}."),
        )

    @app.on_message(filters.command("assetinfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetinfo(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = message.command[1].upper() if len(message.command) > 1 else None
        if not symbol:
            await reply_html(client, message, msgs.error("Usage: <code>/assetinfo SYMBOL</code>"))
            return
        try:
            asset = await asset_service.get_asset(symbol)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        extra = ""
        if not asset.get("is_active", True):
            extra += "\n⛔ <b>DELISTED</b>"
        await reply_html(client, message, msgs.asset_detail(asset) + extra)

    @app.on_message(filters.command("assetlist") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetlist(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        page = _page(args)
        active_only = "all" not in [a.lower() for a in args]
        try:
            result = await asset_service.list_paged(active_only=active_only, page=page, per_page=20)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        if not result["assets"]:
            await reply_html(client, message, msgs.error("No assets found."))
            return
        lines = ["<b>🏠 ASSETS (admin)</b>", ""]
        for a in result["assets"]:
            status = "⛔" if not a.get("is_active", True) else "🟢"
            lines.append(
                f"{status} {a.get('emoji', '📦')} <code>{a['symbol']}</code> "
                f"{format_money(a.get('price', 0))} · {a.get('category', 'OTHER')}"
            )
        lines.append(f"\n<i>Page {result['page']}/{result['pages']} · <code>/assetlist {result['page'] + 1}</code></i>")
        await reply_html(client, message, "\n".join(lines))

    @app.on_message(filters.command("assetsearch") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetsearch(client: Client, message: Message):
        await ensure_user(client, message)
        query = " ".join(message.command[1:])
        if not query:
            await reply_html(client, message, msgs.error("Usage: <code>/assetsearch query</code>"))
            return
        result = await asset_service.list_paged(search=query, page=1, per_page=20)
        if not result["assets"]:
            await reply_html(client, message, msgs.error(f"No assets match <code>{query}</code>."))
            return
        lines = [f"<b>🔎 SEARCH: {query}</b>", ""]
        for a in result["assets"]:
            lines.append(
                f"{a.get('emoji', '📦')} <code>{a['symbol']}</code> "
                f"{a.get('name', '')} · {format_money(a.get('price', 0))}"
            )
        await reply_html(client, message, "\n".join(lines))

    @app.on_message(filters.command("assetset") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 3:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/assetset SYMBOL field value</code>\n"
                    f"Editable fields: {', '.join(sorted(asset_service.EDITABLE_FIELDS))}\n"
                    "Config keys are NOT editable here."
                ),
            )
            return
        symbol = args[0].upper()
        field = args[1].lower()
        value = " ".join(args[2:])
        try:
            asset = await asset_service.update_asset_fields(
                message.from_user.id, symbol, {field: value}, action="ASSET_CONFIG_CHANGE"
            )
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(f"Asset config updated: <code>{asset['symbol']}</code> → <code>{field}</code> = <b>{value}</b>"),
        )

    @app.on_message(filters.command("assetadminstats") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetadminstats(client: Client, message: Message):
        await ensure_user(client, message)
        s = await asset_service.admin_stats()
        top = " · ".join(f"{t['emoji']}{t['symbol']}:{format_money(t['value'])}" for t in s["top"]) or "—"
        most = " · ".join(f"{m['symbol']}:{m['quantity']:g}" for m in s["most_held"]) or "—"
        pnl = s["total_pnl"]
        sign = "+" if pnl >= 0 else ""
        text = (
            f"<b>📊 ASSET ADMIN STATS</b>\n"
            f"<blockquote>"
            f"📈 Assets: <b>{s['active']}</b> active / {s['total']} total ({(s['total'] - s['active'])} delisted)\n"
            f"👥 Holders: {s['holders']} · 📦 Holdings: {s['holdings']}\n"
            f"💹 Total Value: {format_money(s['total_value'])}\n"
            f"💸 Total Invested: {format_money(s['total_invested'])}\n"
            f"📉 Total P/L: {sign}{format_money(pnl)}\n"
            f"🧾 Volume: {format_money(s['total_volume'])}\n"
            f"🏆 Top: {top}\n"
            f"💎 Most held: {most}"
            f"</blockquote>"
        )
        await reply_html(client, message, text)

    @app.on_message(filters.command("assetowners") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_assetowners(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if not args:
            await reply_html(client, message, msgs.error("Usage: <code>/assetowners SYMBOL [page]</code>"))
            return
        symbol = args[0].upper()
        page = _page(args[1:])
        try:
            result = await asset_service.asset_owners(symbol, page=page)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        if not result["owners"]:
            await reply_html(client, message, msgs.error(f"No one owns <code>{symbol}</code>."))
            return
        lines = [f"<b>👥 OWNERS — {symbol}</b>", ""]
        for o in result["owners"]:
            who = f"@{o['username']}" if o["username"] else f"<code>{o['user_id']}</code>"
            lines.append(f"{who} · <b>{o['quantity']:g}</b> · {format_money(o['value'])}")
        lines.append(f"\n<i>Page {result['page']}/{result['pages']} · <code>/assetowners {symbol} {result['page'] + 1}</code></i>")
        await reply_html(client, message, "\n".join(lines))

    @app.on_message(filters.command("listinginfo") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_listinginfo(client: Client, message: Message):
        await ensure_user(client, message)
        listing_id = message.command[1].upper() if len(message.command) > 1 else None
        if not listing_id:
            await reply_html(client, message, msgs.error("Usage: <code>/listinginfo LISTING_ID</code>"))
            return
        listing = await listings_service.listing_info(listing_id)
        if listing is None:
            await reply_html(client, message, msgs.error(f"Unknown listing: <code>{listing_id}</code>."))
            return
        seller = await listings_service.username_lookup(listing["seller_user_id"])
        buyer = (
            await listings_service.username_lookup(listing["buyer_user_id"])
            if listing.get("buyer_user_id")
            else "—"
        )
        text = (
            f"<b>🛒 LISTING INFO</b>\n"
            f"<blockquote>"
            f"{listings_service.listing_text(listing)}\n"
            f"👤 Seller: {seller}\n"
            f"👤 Buyer: {buyer}"
            f"</blockquote>"
        )
        await reply_html(client, message, text)

    @app.on_message(filters.command("forcelisting") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_forcelisting(client: Client, message: Message):
        await ensure_user(client, message)
        listing_id = message.command[1].upper() if len(message.command) > 1 else None
        if not listing_id:
            await reply_html(client, message, msgs.error("Usage: <code>/forcelisting LISTING_ID</code>"))
            return
        try:
            listing = await listings_service.admin_cancel_listing(message.from_user.id, listing_id)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Cancelled listing <code>{listing['listing_id']}</code> "
                f"({listing['symbol']} × {listing['quantity']:g})."
            ),
        )


def asset_service_category_list() -> list[str]:
    from database import assets as assets_db

    return list(assets_db.CATEGORIES)
