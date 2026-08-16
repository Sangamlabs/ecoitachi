"""Stock market handlers: /stocklist, /stock, /buystock, /sellstock, /portfolio,
plus admin commands /addstock and /rmstock."""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import stocks as stocks_service
from services.economy import EconomyError
from utils import messages as msgs
from utils.money import format_money
from utils.permissions import sudo_only
from utils.sender import reply_html

NOT_CHANNEL = ~filters.channel & ~filters.bot


def _symbol(args: list[str]) -> str | None:
    return args[0].upper() if args else None


def _qty(args: list[str]) -> str | None:
    return args[1] if len(args) > 1 else None


def register(app: Client) -> None:
    @app.on_message(filters.command("stocklist") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_stocklist(client: Client, message: Message):
        await ensure_user(client, message)
        assets = await stocks_service.list_market()
        await reply_html(client, message, msgs.stock_list(assets))

    @app.on_message(filters.command("stock") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_stock(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = _symbol(message.command[1:])
        if not symbol:
            await reply_html(client, message, msgs.error("Usage: <code>/stock SYMBOL</code>"))
            return
        asset = await stocks_service.get_asset(symbol)
        await reply_html(client, message, msgs.stock_detail(asset))

    @app.on_message(filters.command("buystock") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_buystock(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        symbol, qty = _symbol(args), _qty(args)
        if not symbol or not qty:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/buystock SYMBOL quantity</code>"),
            )
            return
        result = await stocks_service.buy_stock(message.from_user.id, symbol, qty)
        await reply_html(
            client, message,
            msgs.stock_trade("buy", result["symbol"], result["quantity"], result["cost"], result["tx_id"]),
        )

    @app.on_message(filters.command("sellstock") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_sellstock(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        symbol, qty = _symbol(args), _qty(args)
        if not symbol or not qty:
            await reply_html(
                client, message,
                msgs.error("Usage: <code>/sellstock SYMBOL quantity</code>"),
            )
            return
        result = await stocks_service.sell_stock(message.from_user.id, symbol, qty)
        await reply_html(
            client, message,
            msgs.stock_trade("sell", result["symbol"], result["quantity"], result["value"], result["tx_id"]),
        )

    @app.on_message(filters.command("portfolio") & NOT_CHANNEL)
    @safe_handler(feature="economy")
    async def cmd_portfolio(client: Client, message: Message):
        await ensure_user(client, message)
        pf = await stocks_service.portfolio(message.from_user.id)
        rows = []
        for r in pf["rows"]:
            arrow = "▲" if r["change_percent"] >= 0 else "▼"
            rows.append(
                f"<code>{r['symbol']}</code> · <b>{r['quantity']}</b>\n"
                f"💵 Value: {format_money(r['value'])} {arrow} {abs(r['change_percent']):.2f}%"
            )
        await reply_html(client, message, msgs.portfolio(rows, pf["total_value"], pf["total_cost"]))

    @app.on_message(filters.command("addstock") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_addstock(client: Client, message: Message):
        await ensure_user(client, message)
        args = message.command[1:]
        if len(args) < 4:
            await reply_html(
                client, message,
                msgs.error(
                    "Usage: <code>/addstock SYMBOL name base_price volatility</code>\n"
                    "Example: <code>/addstock ADA Cardano 45000 0.02</code>"
                ),
            )
            return
        symbol = args[0].upper()
        if not symbol.isalnum():
            await reply_html(client, message, msgs.error("Symbol must be letters/numbers only."))
            return
        try:
            base_price = int(float(args[-2]))
            volatility = float(args[-1])
        except ValueError:
            await reply_html(client, message, msgs.error("Invalid base_price or volatility."))
            return
        name = " ".join(args[1:-2])
        if base_price <= 0:
            await reply_html(client, message, msgs.error("base_price must be greater than 0."))
            return
        if not (0 < volatility <= 1):
            await reply_html(client, message, msgs.error("Volatility must be between 0 and 1."))
            return
        try:
            status = await stocks_service.add_asset(symbol, name, base_price, volatility)
        except EconomyError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Listed <code>{symbol}</code> — {name} ({status}).\n"
                f"Base price: {format_money(base_price)} · Volatility: {volatility}"
            ),
        )

    @app.on_message(filters.command("rmstock") & NOT_CHANNEL)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_rmstock(client: Client, message: Message):
        await ensure_user(client, message)
        symbol = message.command[1].upper() if len(message.command) > 1 else None
        if not symbol:
            await reply_html(client, message, msgs.error("Usage: <code>/rmstock SYMBOL</code>"))
            return
        try:
            asset = await stocks_service.deactivate_asset(symbol)
        except EconomyError as exc:
            await reply_html(client, message, msgs.error(str(exc)))
            return
        await reply_html(
            client, message,
            msgs.success(
                f"Removed <code>{asset['symbol']}</code> ({asset.get('name', '')}) from the market.\n"
                f"Existing holders can no longer trade it."
            ),
        )
