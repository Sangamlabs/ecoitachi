"""Assets Market user handlers.

Covers the primary market (/assets /asset /buyasset /sellasset /myassets
/assetstats) and the Section-62 user resale market (/listasset /mylistings
/listings /buylisting /cancellisting).  Buy confirmation uses an inline
keyboard whose expected price is re-verified server-side before executing.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.common import ensure_user, safe_handler
from services import asset_listings as listings_service
from services import assets as asset_service
from services.economy import EconomyError
from utils import messages as msgs
from utils.money import format_money
from utils.sender import answer_callback, edit_html, reply_html

NOT_CHANNEL = ~filters.channel & ~filters.bot

ASSET_BUY_PREFIX = "assetbuy:"
LISTING_NAV_PREFIX = "assetnav:"


def register(app: Client) -> None:
    @app.on_message(filters.command("assets") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_assets(client: Client, message: Message):
        await ensure_user(client, message)
        page = int(message.command[1]) if len(message.command) > 1 else 1
        try:
            result = await asset_service.list_paged(page=page)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        if not result["assets"]:
            await reply_html(client, message, msgs.error("The Assets Market has no listings."))
            return
        await reply_html(
            client, message,
            msgs.asset_list(result["assets"])
            + f"\n<i>Page {result['page']}/{result['pages']} · <code>/assets {result['page'] + 1}</code></i>",
        )

    @app.on_message(filters.command("asset") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_asset(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = message.command[1].upper() if len(message.command) > 1 else None
        if not symbol:
            await reply_html(client, message, msgs.error("Usage: <code>/asset SYMBOL</code>"))
            return
        try:
            asset = await asset_service.get_active_asset(symbol)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(client, message, msgs.asset_detail(asset))

    @app.on_message(filters.command("assetsinfo") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_assetsinfo(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = message.command[1].upper() if len(message.command) > 1 else None
        if not symbol:
            stats = await asset_service.market_stats()
            result = await asset_service.list_paged(page=1)
            if not result["assets"]:
                await reply_html(client, message, msgs.error("The Assets Market has no listings."))
                return
            await reply_html(
                client, message,
                msgs.asset_market_stats(stats) + "\n" + msgs.asset_list(result["assets"]),
            )
            return
        try:
            info = await asset_service.asset_buy_info(symbol)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(client, message, msgs.asset_buy_info(info))

    @app.on_message(filters.command("buyasset") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_buyasset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/buyasset SYMBOL quantity</code>"),
            )
            return
        symbol = args[0].upper()
        qty_raw = args[1]
        try:
            asset = await asset_service.get_active_asset(symbol)
            qty = asset_service.parse_quantity(qty_raw, asset)
        except asset_service.AssetError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        cfg = await asset_service.market_config()
        price = asset_service.price_for(asset, cfg, "buy")
        total = int(asset_service.multiply_price(price, qty))
        await reply_html(
            client, message,
            msgs.asset_confirm_buy(asset["symbol"], asset.get("name", ""), asset.get("emoji", "📦"), qty, price, total),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Confirm",
                            callback_data=f"{ASSET_BUY_PREFIX}{asset['symbol']}:{qty}:{price}",
                        ),
                        InlineKeyboardButton("❌ Cancel", callback_data=f"{ASSET_BUY_PREFIX}cancel"),
                    ]
                ]
            ),
        )

    @app.on_message(filters.command("sellasset") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_sellasset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 2:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/sellasset SYMBOL quantity</code>"),
            )
            return
        try:
            result = await asset_service.sell(message.from_user.id, args[0].upper(), args[1])
        except (asset_service.AssetError, EconomyError) as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(client, message, msgs.asset_trade("sell", result))

    @app.on_message(filters.command("myassets") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_myassets(client: Client, message: Message):
        await ensure_user(client, message)
        pf = await asset_service.portfolio(message.from_user.id)
        if not pf["rows"]:
            await reply_html(
                client, message,
                msgs.error("You own no assets. Buy some with <code>/buyasset SYMBOL qty</code>."),
            )
            return
        rows = []
        for r in pf["rows"]:
            arrow = "▲" if r["pnl_percent"] >= 0 else "▼"
            pnl_emoji = "✅" if r["pnl"] >= 0 else "⚠️"
            rows.append(
                f"{r['emoji']} <code>{r['symbol']}</code> · <b>{r['quantity']:g}</b>"
                f" @ <b>{format_money(r['price'])}</b>\n"
                f"💵 Value: {format_money(r['value'])} · "
                f"{pnl_emoji} P/L: {arrow} {format_money(abs(r['pnl']))} ({abs(r['pnl_percent']):.2f}%)"
            )
        await reply_html(
            client, message,
            msgs.asset_portfolio(rows, pf["total_value"], pf["total_invested"]),
        )

    @app.on_message(filters.command("assetstats") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_assetstats(client: Client, message: Message):
        await ensure_user(client, message)
        stats = await asset_service.market_stats()
        await reply_html(client, message, msgs.asset_market_stats(stats))

    @app.on_message(filters.command("listasset") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_listasset(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 3:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/listasset SYMBOL quantity price</code>"),
            )
            return
        try:
            listing = await listings_service.create_listing(
                message.from_user.id, args[0].upper(), args[1], args[2]
            )
        except (asset_service.AssetError, EconomyError) as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        text = (
            f"<b>🛒 LISTING CREATED</b>\n"
            f"<blockquote>{listings_service.listing_text(listing)}</blockquote>\n"
            f"<i>Buyers can grab it with <code>/buylisting {listing['listing_id']}</code>.</i>"
        )
        await reply_html(client, message, text)

    @app.on_message(filters.command("mylistings") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_mylistings(client: Client, message: Message):
        await ensure_user(client, message)
        listings = await listings_service.my_listings(message.from_user.id)
        await reply_html(client, message, msgs.my_listings(listings))

    @app.on_message(filters.command("listings") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_listings(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        symbol = None
        page = 1
        for arg in args:
            if arg.isdigit():
                page = int(arg)
            else:
                symbol = arg.upper()
        result = await listings_service.browse(symbol=symbol, page=page)
        if not result["listings"]:
            await reply_html(
                client, message,
                msgs.error(
                    "No listings yet." + (f" for <code>{symbol}</code>" if symbol else "")
                    + " Create one with <code>/listasset SYMBOL qty price</code>."
                ),
            )
            return
        nav = [
            InlineKeyboardButton("◀️", callback_data=f"{LISTING_NAV_PREFIX}{symbol or ''}:{result['page'] - 1}"),
            InlineKeyboardButton(f"{result['page']}/{result['pages']}", callback_data=f"{LISTING_NAV_PREFIX}none:{result['page']}"),
            InlineKeyboardButton("▶️", callback_data=f"{LISTING_NAV_PREFIX}{symbol or ''}:{result['page'] + 1}"),
        ]
        await reply_html(
            client, message,
            msgs.listings_list(result["listings"], symbol, result["page"], result["pages"]),
            reply_markup=InlineKeyboardMarkup([nav]),
        )

    @app.on_message(filters.command("buylisting") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_buylisting(client: Client, message: Message):
        await ensure_user(client, message)
        listing_id = message.command[1].upper() if len(message.command) > 1 else None
        if not listing_id:
            await reply_html(client, message, msgs.error("Usage: <code>/buylisting LISTING_ID</code>"))
            return
        try:
            listing = await listings_service.buy_listing(message.from_user.id, listing_id)
        except (asset_service.AssetError, EconomyError) as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"🎉 Bought <code>{listing['symbol']}</code> × <b>{listing['quantity']:g}</b> "
                f"for <b>{format_money(listing['total_price'])}</b>!\n"
                f"<i>Listing {listing['listing_id']} is now closed.</i>"
            ),
        )

    @app.on_message(filters.command("cancellisting") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_cancellisting(client: Client, message: Message):
        await ensure_user(client, message)
        listing_id = message.command[1].upper() if len(message.command) > 1 else None
        if not listing_id:
            await reply_html(client, message, msgs.error("Usage: <code>/cancellisting LISTING_ID</code>"))
            return
        try:
            await listings_service.cancel_listing(message.from_user.id, listing_id)
        except (asset_service.AssetError, EconomyError) as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(f"Listing <code>{listing_id}</code> cancelled. Your asset is back in your holdings."),
        )

    @app.on_message(filters.command("rmlisting") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_user_rmlisting(client: Client, message: Message):
        await ensure_user(client, message)
        listing_id = message.command[1].upper() if len(message.command) > 1 else None
        if not listing_id:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/rmlisting LISTING_ID</code> — remove your own listing"),
            )
            return
        try:
            await listings_service.cancel_listing(message.from_user.id, listing_id)
        except (asset_service.AssetError, EconomyError) as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Removed your listing <code>{listing_id}</code>. "
                f"The quantity is back in your holdings."
            ),
        )

    @app.on_callback_query(filters.regex(rf"^{ASSET_BUY_PREFIX}"))
    async def cb_asset_buy(client: Client, callback: CallbackQuery):
        if callback.from_user is None:
            return
        data = callback.data[len(ASSET_BUY_PREFIX):]
        if data == "cancel":
            await edit_html(client, callback.message, "🚫 Purchase cancelled.", reply_markup=None)
            await answer_callback(client, callback, "Cancelled.")
            return
        parts = data.split(":")
        if len(parts) != 3:
            await answer_callback(client, callback, "Invalid purchase data.", show_alert=True)
            return
        symbol, qty_raw, expected_price = parts[0], parts[1], int(parts[2])
        try:
            asset = await asset_service.get_active_asset(symbol)
            asset_service.parse_quantity(qty_raw, asset)
        except asset_service.AssetError as exc:
            await edit_html(client, callback.message, msgs.error(str(exc)), reply_markup=None)
            await answer_callback(client, callback, str(exc), show_alert=True)
            return
        cfg = await asset_service.market_config()
        current_price = asset_service.price_for(asset, cfg, "buy")
        if current_price != expected_price:
            await edit_html(
                client, callback.message,
                msgs.error(
                    f"⚠️ Price changed since you asked ({format_money(expected_price)} → "
                    f"{format_money(current_price)}). Send <code>/buyasset {symbol} {qty_raw}</code> again."
                ),
                reply_markup=None,
            )
            await answer_callback(client, callback, "Price changed, please retry.", show_alert=True)
            return
        try:
            result = await asset_service.buy(callback.from_user.id, symbol, qty_raw)
        except (asset_service.AssetError, EconomyError) as exc:
            await edit_html(client, callback.message, msgs.error(str(exc)), reply_markup=None)
            await answer_callback(client, callback, str(exc), show_alert=True)
            return
        await edit_html(client, callback.message, msgs.asset_trade("buy", result), reply_markup=None)
        await answer_callback(client, callback, "Purchased.")

    @app.on_callback_query(filters.regex(rf"^{LISTING_NAV_PREFIX}"))
    async def cb_listing_nav(client: Client, callback: CallbackQuery):
        if callback.from_user is None:
            return
        data = callback.data[len(LISTING_NAV_PREFIX):]
        symbol, page_raw = data.rsplit(":", 1)
        try:
            page = max(1, int(page_raw))
        except ValueError:
            page = 1
        symbol = symbol or None
        result = await listings_service.browse(symbol=symbol, page=page)
        if not result["listings"]:
            await answer_callback(client, callback, "No more listings.", show_alert=True)
            return
        nav = [
            InlineKeyboardButton("◀️", callback_data=f"{LISTING_NAV_PREFIX}{symbol or ''}:{result['page'] - 1}"),
            InlineKeyboardButton(f"{result['page']}/{result['pages']}", callback_data=f"{LISTING_NAV_PREFIX}none:{result['page']}"),
            InlineKeyboardButton("▶️", callback_data=f"{LISTING_NAV_PREFIX}{symbol or ''}:{result['page'] + 1}"),
        ]
        await edit_html(
            client, callback.message,
            msgs.listings_list(result["listings"], symbol, result["page"], result["pages"]),
            reply_markup=InlineKeyboardMarkup([nav]),
        )
        await answer_callback(client, callback, "Loaded.")
