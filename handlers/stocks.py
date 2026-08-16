"""Stock market handlers: /stocklist, /stock, /buystock, /sellstock, /portfolio."""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import Message

from handlers.common import ensure_user, safe_handler
from services import stocks as stocks_service
from utils import messages as msgs
from utils.money import format_money
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
