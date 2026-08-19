"""Centralized HTML message builders.

Every user-facing Telegram message is built here as valid Telegram HTML and
sent through :mod:`utils.sender` with ``parse_mode=HTML``.  Dynamic content is
HTML-escaped before insertion.

Message content is kept separate from the send logic so future features
(e.g. wrapping cards in ``<blockquote>``) can be applied globally.
"""

from __future__ import annotations

import math
import time
from html import escape
from typing import Any

from utils.formatting import tg_link
from utils.money import format_money
from services.settings import DEFAULT_SLOT_PAYOUTS

CMD = "💰 UNOITACHI"
OWNER_EMOJI = {1: "🥇", 2: "🥈", 3: "🥉"}


def _user_name(user: dict[str, Any]) -> str:
    if user.get("username"):
        return f"@{escape(user['username'])}"
    return escape(user.get("first_name") or "Unknown")


def _link(user_id: int, name: str) -> str:
    return tg_link(user_id, escape(name))


def success(text: str) -> str:
    return f"<b>✅ {text}</b>"


def error(text: str) -> str:
    return f"<b>❌ {text}</b>"


def warning(text: str) -> str:
    return f"<b>⚠️ {text}</b>"


def info(text: str) -> str:
    return f"<b>ℹ️ {text}</b>"


def start(user: dict[str, Any]) -> str:
    name = _user_name(user)
    return (
        f"<b>💰 {CMD}</b>\n\n"
        f"Welcome, <b>{name}</b>! You have joined the UNOITACHI economy.\n\n"
        f"<blockquote>"
        f"💵 Work the market, bank your earnings and grow your net worth.\n"
        f"🎁 Claim free currency with <code>/daily</code>, <code>/weekly</code> and "
        f"<code>/monthly</code>.\n"
        f"Use <code>/help</code> to see everything you can do."
        f"</blockquote>"
    )


def help_text() -> str:
    return (
        f"<b>📖 {CMD} — HELP</b>\n\n"
        f"<b>👤 Economy</b>\n"
        f"<code>/profile</code> — your profile\n"
        f"<code>/bal</code> — check balance\n"
        f"<code>/pay @user amount</code> — send money\n"
        f"<code>/leader</code> — leaderboard\n"
        f"<code>/topbank</code> — top bank deposits\n"
        f"<code>/transactions</code> — last 10 transfers (sent & received)\n\n"
        f"<b>🏦 Bank</b>\n"
        f"<code>/deposit amount</code> — wallet → bank\n"
        f"<code>/withdraw amount</code> — bank → wallet (tax applies)\n"
        f"<code>/bank</code> — bank info & interest\n\n"
        f"<b>💰 Loans</b>\n"
        f"<code>/loan DAYS AMOUNT</code> — borrow money\n"
        f"<code>/loanpay [amount]</code> — repay a loan\n"
        f"<code>/loaninfo</code> — loan rules & rates\n\n"
        f"<b>📈 Stock Market</b>\n"
        f"<code>/stocklist</code> — market overview\n"
        f"<code>/stock SYMBOL</code> — stock details\n"
        f"<code>/buystock SYMBOL qty</code> — buy\n"
        f"<code>/sellstock SYMBOL qty</code> — sell\n"
        f"<code>/portfolio</code> — your holdings\n\n"
        f"<b>🏠 Asset Market</b>\n"
        f"<code>/assets</code> — asset market overview\n"
        f"<code>/asset SYMBOL</code> — asset details\n"
        f"<code>/assetsinfo [SYMBOL]</code> — market stats / buy-decision info\n"
        f"<code>/assetstats</code> — market statistics\n"
        f"<code>/buyasset SYMBOL qty</code> — buy (with confirm)\n"
        f"<code>/sellasset SYMBOL qty</code> — sell\n"
        f"<code>/myassets</code> — your asset portfolio\n\n"
        f"<b>🛒 Resale Market</b>\n"
        f"<code>/listasset SYMBOL qty price</code> — list your asset for sale\n"
        f"<code>/listings</code> — browse user listings\n"
        f"<code>/buylisting ID</code> — buy a listing\n"
        f"<code>/mylistings</code> — your listings\n"
        f"<code>/cancellisting ID</code> / <code>/rmlisting ID</code> — remove your own listing\n\n"
        f"<b>🎁 Promo Codes</b>\n"
        f"Just type an active promo code as a normal message in DM or a group — "
        f"it is detected and redeemed automatically. "
        f"<code>/redeem CODE</code> — redeem a code explicitly.\n"
        f"<i>Only valid, active codes are processed; ordinary messages do nothing.</i>\n\n"
        f"<b>🎁 Free Rewards</b>\n"
        f"<code>/daily</code> / <code>/weekly</code> / <code>/monthly</code> — claim free money\n\n"
        f"<b>💰 Daily Income</b>\n"
        f"<code>/interestbank</code> — claim daily bank income\n"
        f"<code>/interestasset</code> — claim daily asset income\n"
        f"<code>/stockinterest</code> — claim daily stock income\n\n"
        f"<b>🎮 Games</b>\n"
        f"<code>/fly low|medium|high amount</code> — fly game\n"
        f"<code>/mines amount</code> — 6x6 mines board\n"
        f"<code>/bet amount</code> — coin bet\n"
        f"<code>/colour amount</code> — pick size, colour & number\n"
        f"<code>/blackjack amount</code> — play the dealer\n"
        f"<code>/rob @user</code> — steal from a user's bank\n\n"
        f"<b>🎳 Emoji Games</b>\n"
        f"<code>/sball /sarrow /sbasketball /sfootball /sslot /sdice</code> — solo rounds\n"
        f"<code>/ball /arrow /basketball /football /slot /dice</code> — duels (create a lobby)\n"
        f"<code>/join CODE</code> — join a duel with its 4-digit code\n\n"
        f"<i>Every game and /rob has a 60s cooldown. Bet within your wallet balance. "
        f"Use <code>/help</code> again anytime, and <code>/start</code> to see the welcome.</i>"
    )


def balance(user: dict[str, Any], target: dict[str, Any]) -> str:
    name = _link(target["user_id"], _user_name(target))
    net = user.get("wallet", 0) + user.get("bank", 0)
    return (
        f"<b>💰 BALANCE</b>\n"
        f"<blockquote>👤 User: {name}\n"
        f"💵 Wallet: {format_money(user.get('wallet', 0))}\n"
        f"🏦 Bank: {format_money(user.get('bank', 0))}\n"
        f"💎 Net Worth: {format_money(net)}</blockquote>"
    )


def profile(user: dict[str, Any]) -> str:
    rank = user.get("monthly_rank") or "—"
    stocks_value = user.get("stocks_value", 0)
    asset_value = user.get("asset_value", 0)
    net = user.get("wallet", 0) + user.get("bank", 0) + stocks_value + asset_value
    name = _user_name(user)
    if user.get("username"):
        ident_line = f"🆔 <b>Username:</b> @{escape(user['username'])}"
    else:
        ident_line = f"🆔 <b>User ID:</b> <code>{user['user_id']}</code>"
    return (
        f"<b>👤 PROFILE</b>\n"
        f"<blockquote>"
        f"👤 <b>Name:</b> {name}\n"
        f"{ident_line}\n"
        f"🆔 <b>User ID:</b> <code>{user['user_id']}</code>\n"
        f"💵 <b>Wallet:</b> {format_money(user.get('wallet', 0))}\n"
        f"🏦 <b>Bank:</b> {format_money(user.get('bank', 0))}\n"
        f"💎 <b>Net Worth:</b> {format_money(net)}\n"
        f"📈 <b>Stocks Value:</b> {format_money(stocks_value)}\n"
        f"🏠 <b>Asset Value:</b> {format_money(asset_value)}\n"
        f"💸 <b>Total Earned:</b> {format_money(user.get('total_earned', 0))}\n"
        f"💳 <b>Total Spent:</b> {format_money(user.get('total_spent', 0))}\n"
        f"🏆 <b>Leaderboard Rank:</b> {rank}"
        f"</blockquote>"
    )


def payment(sender: dict[str, Any], receiver: dict[str, Any], amount: int, tx_id: str) -> str:
    uid = receiver.get("unique_user_id")
    uid_line = f"🪪 Recipient UID: <code>{uid}</code>\n" if uid else ""
    return (
        f"<b>✅ PAYMENT SENT</b>\n"
        f"<blockquote>"
        f"👤 To: {_link(receiver['user_id'], _user_name(receiver))}\n"
        f"💵 Amount: <b>{format_money(amount)}</b>\n"
        f"{uid_line}"
        f"🧾 <code>#{tx_id}</code>"
        f"</blockquote>"
    )


def payment_received(sender: dict[str, Any], amount: int) -> str:
    return (
        f"<b>💸 PAYMENT RECEIVED</b>\n"
        f"{_link(sender['user_id'], _user_name(sender))} sent you "
        f"<b>{format_money(amount)}</b>."
    )


def leaderboard(entries: list[tuple[int, str, int]]) -> str:
    lines = ["<b>🏆 UNOITACHI LEADERBOARD</b>", ""]
    for idx, (user_id, name, net_worth) in enumerate(entries, start=1):
        medal = OWNER_EMOJI.get(idx, "")
        prefix = f"{medal} " if medal else f"<code>{idx}</code>. "
        lines.append(f"{prefix}{_link(user_id, escape(name))} — <b>{format_money(net_worth)}</b>")
    return "\n".join(lines)


def bank_leaderboard(entries: list[tuple[int, str, int]]) -> str:
    lines = ["<b>🏦 TOP BANK DEPOSITS</b>", ""]
    for idx, (user_id, name, bank_balance) in enumerate(entries, start=1):
        medal = OWNER_EMOJI.get(idx, "")
        prefix = f"{medal} " if medal else f"<code>{idx}</code>. "
        lines.append(f"{prefix}{_link(user_id, escape(name))} — <b>{format_money(bank_balance)}</b>")
    return "\n".join(lines)


def bank(user: dict[str, Any], settings: dict[str, Any], tax_pool: int) -> str:
    rate = settings.get("interest_rate", 2.0)
    interval = settings.get("interest_interval_hours", 24)
    tax = settings.get("withdrawal_tax_rate", 5.0)
    return (
        f"<b>🏦 BANK</b>\n"
        f"<blockquote>"
        f"💵 Bank Balance: {format_money(user.get('bank', 0))}\n"
        f"💰 Wallet: {format_money(user.get('wallet', 0))}\n"
        f"📈 Interest: <b>{rate}%</b> per {interval}h\n"
        f"🧾 Withdrawal Tax: <b>{tax}%</b>\n"
        f"🏛️ Tax Pool: {format_money(tax_pool)}"
        f"</blockquote>\n"
        f"<i>Use <code>/deposit</code> and <code>/withdraw</code> to move money.</i>"
    )


def income_claim(source: str, result: dict[str, Any]) -> str:
    """Reply for /interestbank, /interestasset and /stockinterest."""
    emoji = {
        "bank": "🏦",
        "asset": "🏠",
        "stock": "📈",
    }.get(source, "💰")
    labels = {
        "bank": "BANK INTEREST",
        "asset": "ASSET INCOME",
        "stock": "STOCK INTEREST",
    }
    label = labels.get(source, source.upper())
    amount = int(result.get("amount", 0))
    value = int(result.get("value", 0))
    rate = float(result.get("rate", 0.0))
    days = int(result.get("days", 0))

    if result.get("already_claimed"):
        return f"<b>{emoji} {label}</b>\n⚠️ You already claimed just now — try again in 24h."

    if result.get("started"):
        return (
            f"<b>{emoji} {label}</b>\n"
            f"<blockquote>"
            f"📈 Income tracking started.\n"
            f"💰 Base: {format_money(value)}\n"
            f"📊 Rate: <b>{rate}%</b> per 24h\n"
            f"⏳ Check back in 24h to claim."
            f"</blockquote>"
        )

    if amount <= 0:
        wait = int(result.get("next_in", 86_400))
        hours = max(1, wait // 3600)
        return (
            f"<b>{emoji} {label}</b>\n"
            f"<blockquote>"
            f"💰 Base: {format_money(value)}\n"
            f"📊 Rate: <b>{rate}%</b> per 24h\n"
            f"⏳ Nothing to claim yet — next income in ~{hours}h."
            f"</blockquote>"
        )

    return (
        f"<b>{emoji} {label}</b>\n"
        f"<blockquote>"
        f"💵 Claimed: <b>{format_money(amount)}</b>\n"
        f"📅 Unclaimed days: {days}\n"
        f"💰 Base: {format_money(value)}\n"
        f"📊 Rate: <b>{rate}%</b> per 24h"
        f"</blockquote>\n"
        f"<i>Paid to your wallet. Next income in 24h.</i>"
    )


def transaction_row(tx: dict[str, Any]) -> str:
    direction = tx.get("metadata", {}).get("direction")
    if tx.get("type") == "PAY" and direction == "in":
        sign, label = "←", "RECEIVED"
    elif tx.get("type") == "PAY":
        sign, label = "→", "SENT"
    else:
        sign = {"GAME_LOSS": "−", "ADMIN_REMOVE": "−", "STOCK_BUY": "−",
                "WITHDRAW": "−", "TAX": "−", "ROBBED": "−"}.get(tx.get("type", ""), "＋")
        label = tx.get("type", "UNKNOWN")
    amount = tx.get("amount", 0)
    return (
        f"<code>{escape(label)}</code> {sign} "
        f"<b>{format_money(amount)}</b> · <code>#{tx.get('transaction_id', '')[:10]}</code>"
    )


def transactions_list(rows: list[str], empty: bool) -> str:
    if empty:
        return "<b>🧾 TRANSACTIONS</b>\n<i>No transactions yet.</i>"
    return "<b>🧾 RECENT TRANSACTIONS</b>\n" + "\n".join(rows)


def stock_list(assets: list[dict[str, Any]]) -> str:
    lines = ["<b>📈 UNOITACHI MARKET</b>", ""]
    for a in assets:
        arrow = "▲" if a.get("change_percent", 0) >= 0 else "▼"
        lines.append(
            f"<code>{escape(a['symbol'])}</code> "
            f"{format_money(a.get('price', 0))} "
            f"<b>{arrow} {abs(a.get('change_percent', 0)):.2f}%</b>"
        )
    return "\n".join(lines)


def stock_detail(asset: dict[str, Any]) -> str:
    arrow = "▲" if asset.get("change_percent", 0) >= 0 else "▼"
    return (
        f"<b>📈 {escape(asset['symbol'])} — {escape(asset.get('name', ''))}</b>\n"
        f"<blockquote>"
        f"💵 Price: <b>{format_money(asset.get('price', 0))}</b> "
        f"{arrow} {asset.get('change_percent', 0):.2f}%\n"
        f"📈 24h High: {format_money(asset.get('high_price', 0))}\n"
        f"📉 24h Low: {format_money(asset.get('low_price', 0))}\n"
        f"📊 Volatility: {asset.get('volatility', 0):.1%}"
        f"</blockquote>"
    )


def stock_trade(action: str, symbol: str, qty: float, total: int, tx_id: str) -> str:
    verb = "Bought" if action == "buy" else "Sold"
    return (
        f"<b>✅ {verb} {escape(symbol)}</b>\n"
        f"<blockquote>"
        f"🔢 Quantity: {qty}\n"
        f"💵 Total: <b>{format_money(total)}</b>\n"
        f"🧾 <code>#{tx_id}</code>"
        f"</blockquote>"
    )


def portfolio(rows: list[str], total_value: int, total_cost: int) -> str:
    pnl = total_value - total_cost
    sign = "+" if pnl >= 0 else ""
    lines = ["<b>📊 PORTFOLIO</b>", ""]
    lines.extend(rows)
    lines += [
        "",
        f"<b>Total Stock Value:</b> {format_money(total_value)}",
        f"<b>Total P/L:</b> {sign}{format_money(pnl)}",
    ]
    return "\n".join(lines)


def fly_result(difficulty: str, bet: int, won: bool, multiplier: float, payout: int, tx_id: str) -> str:
    head = "✈️ FLY GAME" if won else "💥 FLY GAME"
    result = (
        f"<b>✅ YOU WON!</b>\nPayout: <b>{format_money(payout)}</b> "
        f"({multiplier:.2f}x on {format_money(bet)})"
        if won
        else f"<b>❌ YOU CRASHED!</b>\nLost: {format_money(bet)}"
    )
    return (
        f"<b>{head}</b> — <i>{escape(difficulty)}</i>\n"
        f"<blockquote>{result}\n🧾 <code>#{tx_id}</code></blockquote>"
    )


def bet_result(bet: int, won: bool, multiplier: float, payout: int, tx_id: str) -> str:
    result = (
        f"<b>✅ WIN!</b> {format_money(payout)} ({multiplier:.2f}x)"
        if won
        else f"<b>❌ LOSS</b> {format_money(bet)}"
    )
    return f"<b>🎲 BET GAME</b>\n<blockquote>{result}\n🧾 <code>#{tx_id}</code></blockquote>"


def format_duration(seconds: int) -> str:
    """Human-readable countdown timer, e.g. 90 -> '1m 30s', 3725 -> '1h 2m 5s'."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86_400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def game_cooldown(game: str, remaining: int) -> str:
    return (
        f"<b>⏳ {escape(game.title())} is on cooldown.</b>\n"
        f"<i>Try again in <b>{format_duration(remaining)}</b>.</i>"
    )


def reward_claimed(kind: str, amount: int, cooldown: int) -> str:
    return (
        f"<b>🎁 {escape(kind.title())} REWARD</b>\n"
        f"<blockquote>You claimed <b>{format_money(amount)}</b>.\n"
        f"Next claim in {format_duration(cooldown)}.</blockquote>"
    )


def rob_result(result: dict[str, Any], robber: dict[str, Any], victim: dict[str, Any]) -> str:
    victim_name = _user_name(victim)
    next_rob = format_duration(result.get("cooldown", 0))
    if result["success"]:
        return (
            f"<b>🦹 ROBBERY SUCCESS</b>\n"
            f"<blockquote>You stole <b>{format_money(result['stolen'])}</b> from {victim_name}.\n"
            f"They had {format_money(result['target_bank_before'])} banked.</blockquote>\n"
            f"<i>Next robbery in {next_rob}.</i>"
        )
    return (
        f"<b>🚔 ROBBERY FAILED</b>\n"
        f"<blockquote>The police caught you robbing {victim_name}.\n"
        f"You got nothing.</blockquote>\n"
        f"<i>Next robbery in {next_rob}.</i>"
    )


def robbery_notice(victim: dict[str, Any], robber: dict[str, Any], stolen: int) -> str:
    return (
        f"<b>🦹 YOU WERE ROBBED</b>\n"
        f"{_link(robber['user_id'], _user_name(robber))} stole "
        f"<b>{format_money(stolen)}</b> from your bank!"
    )


def admin_help() -> str:
    return (
        f"<b>🛠 {CMD} — ADMIN HELP</b>\n"
        f"<i>Owner + sudo admins only.</i>\n\n"
        f"<b>🛡 Permissions (owner only)</b>\n"
        f"<code>/addsudo @user</code> — add sudo\n"
        f"<code>/rsudo @user</code> — remove sudo\n\n"
        f"<b>💰 Economy</b>\n"
        f"<code>/give @user amount</code> — give money\n"
        f"<code>/remove @user amount</code> — take money\n"
        f"<code>/getcoin amount</code> — credit yourself coins\n"
        f"<code>/data USER</code> — full activity report (ID / @username / UID / reply)\n"
        f"<code>/track TX_ID</code> — full transaction detail\n\n"
        f"<b>🏦 Bank</b>\n"
        f"<code>/setinterest rate</code> — interest % per 24h\n"
        f"<code>/setincome bank|asset|stock rate</code> — daily income % per 24h\n"
        f"<code>/setreward daily|weekly|monthly amount</code> — reward amounts\n"
        f"<code>/settax rate</code> — withdrawal tax %\n"
        f"<code>/addtax system rate</code> — tax % for a system's transactions\n"
        f"<code>/dtax</code> — distribute the tax pool now\n"
        f"<code>/taxinfo</code> — all tax rates + pool\n"
        f"<code>/banksettings</code> — view bank settings\n\n"
        f"<b>💰 Loans</b>\n"
        f"<code>/setloan field value</code> — loan config\n"
        f"<code>/loanstats</code> — loan statistics\n"
        f"<code>/loanuser USER</code> — a user's loans\n\n"
        f"<b>📈 Stock Market</b>\n"
        f"<code>/addstock SYMBOL name price volatility</code> — list a stock\n"
        f"<code>/rmstock SYMBOL</code> — delist a stock\n\n"
        f"<b>🏠 Asset Market</b>\n"
        f"<code>/addasset SYMBOL name CATEGORY price volatility</code> — list an asset\n"
        f"<code>/editasset SYMBOL field value</code> — edit asset fields\n"
        f"<code>/assetset SYMBOL field value</code> — asset config\n"
        f"<code>/assetprice SYMBOL price</code> — manual price set\n"
        f"<code>/assetvolatility SYMBOL v</code> — volatility set\n"
        f"<code>/rmasset SYMBOL</code> — delist\n"
        f"<code>/restoreasset SYMBOL</code> — relist\n"
        f"<code>/assetinfo SYMBOL</code> / <code>/assetlist [page]</code>\n"
        f"<code>/assetsearch query</code> — search assets\n"
        f"<code>/assetowners SYMBOL [page]</code> — top holders\n"
        f"<code>/assetadminstats</code> — market admin stats\n"
        f"<code>/listinginfo LISTING_ID</code> — listing details\n"
        f"<code>/forcelisting LISTING_ID</code> — force-cancel any listing\n\n"
        f"<b>🎁 Promo Codes</b>\n"
        f"<code>/addpromo CODE EXPIRY LIMIT REWARD [REWARD...]</code> — create\n"
        f"<code>/rmpromo CODE</code> — disable (history kept)\n"
        f"<code>/editpromo CODE FIELD VALUE [VALUE...]</code> — expiry|limit|active|reward\n"
        f"<code>/promoinfo CODE</code> / <code>/promolist [status] [page]</code>\n"
        f"<code>/promostats CODE</code> — redemption statistics\n\n"
        f"<b>🎮 Games</b>\n"
        f"<code>/flyset low|medium|high field value</code>\n"
        f"<code>/flytrap difficulty 8 values</code>\n"
        f"<code>/betset win_prob multiplier min_bet max_bet [cooldown]</code>\n"
        f"<code>/minestrap ...</code> — mines tuning\n"
        f"<code>/colourset field value</code> — colour tuning\n"
        f"<code>/robset field value</code> — rob tuning\n\n"
        f"<b>🎳 Emoji Games</b>\n"
        f"<code>/emojiset GAME field value</code> — one field at a time\n"
        f"<code>/emojitrap GAME key=value ...</code> — bulk set\n"
        f"<code>/emojigameinfo GAME</code> — current config\n"
        f"<code>/emojigames</code> — overview of all emoji games\n"
        f"<code>/bjset field value</code> — blackjack config\n"
        f"<code>/bjinfo</code> — blackjack config\n"
        f"<i>Fields: cooldown, min_bet, max_bet, multiplier, rule, target, "
        f"single, duel, enabled, expiry.</i>\n\n"
        f"<b>👥 Users</b>\n"
        f"<code>/freeze @user</code> / <code>/unfreeze @user</code>\n"
        f"<code>/ban @user</code> / <code>/unban @user</code>\n"
        f"<code>/leaderban @user</code> / <code>/leaderunban @user</code> — hide/show a user on leaderboards\n"
        f"<code>/clearlb AMOUNT USER_COUNT</code> — set the wallet of the top USER_COUNT users to exactly AMOUNT\n"
        f"<code>/gban @user</code> / <code>/ungban @user</code> — global ban (owner + sudo)\n"
        f"<code>/userinfo @user</code> — user details\n"
        f"<code>/setchat [chat_id] [setting] [on|off]</code> — group config\n\n"
        f"<b>📢 Broadcast (reply to a message)</b>\n"
        f"<code>/bgc</code> — broadcast to all registered groups (auto-registered on join)\n"
        f"<code>/bdm</code> — broadcast to all users who started the bot via DM\n\n"
        f"<b>🖥 Admin Panel</b>\n"
        f"<code>/adminpanel</code> — interactive inline-button admin menu\n\n"
        f"<b>📊 Stats & Recovery</b>\n"
        f"<code>/econstats</code> — economy stats\n"
        f"<code>/dumps</code> / <code>/dumpinfo</code> — security dumps (owner + sudo)\n"
        f"<code>/clear USER</code> — backup + reset economy, returns recovery ID (owner)\n"
        f"<code>/restore DUMP-ID</code> / <code>/recover DUMP-ID</code> — restore from a dump (owner)\n"
        f"<code>/restorecase CASE-ID</code> — restore from a recoverable case (owner)\n"
        f"<code>/securityset</code> — view security config (owner)\n"
        f"<code>/restart</code> — restart the bot process\n"
        f"<code>/adminhelp</code> — this help"
    )


def clearlb_result(amount: int, done: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> str:
    """Report for the /clearlb command (wallet set to ``amount`` per user)."""
    lines = [
        f"<b>🧹 CLEAR LEADERBOARD</b>",
        f"<blockquote>💵 Set the wallet of {len(done)} user(s) to exactly "
        f"<b>{format_money(amount)}</b>.",
    ]
    for entry in done:
        lines.append(
            f"  • User <code>{entry['user_id']}</code>: "
            f"{format_money(entry['before'])} → {format_money(entry['after'])} "
            f"— 🧾 <code>#{entry['tx_id']}</code>"
        )
    lines.append("</blockquote>")
    if skipped:
        lines.append("<b>Skipped:</b>")
        for entry in skipped:
            lines.append(f"  • User <code>{entry['user_id']}</code> — {entry['reason']}")
    return "\n".join(lines)


def admin_stats(stats: dict[str, Any]) -> str:
    return (
        f"<b>📊 ECONOMY STATS</b>\n"
        f"<blockquote>"
        f"👥 Users: {stats['users']}\n"
        f"💰 Total In Circulation: {format_money(stats['total_wallet'])}\n"
        f"🏦 Total Banked: {format_money(stats['total_bank'])}\n"
        f"🏛️ Tax Pool: {format_money(stats['tax_pool'])}\n"
        f"🧾 Transactions: {stats['transactions']}\n"
        f"📈 Active Stocks: {stats['stocks']}"
        f"</blockquote>"
    )


def tx_track_detail(tx: dict[str, Any]) -> str:
    """Full audit detail for one transaction (used by /track)."""
    meta = tx.get("metadata") or {}
    meta_lines = [
        f"<code>{escape(str(k))}</code>: <b>{escape(str(v))}</b>"
        for k, v in meta.items()
    ]
    return (
        f"<b>🧾 TRANSACTION TRACKER</b>\n"
        f"<blockquote>"
        f"🆔 ID: <code>#{tx.get('transaction_id', '')}</code>\n"
        f"👤 User: <code>{tx.get('user_id', '')}</code>\n"
        f"📦 Type: <b>{escape(str(tx.get('type', '')))}</b>\n"
        f"💰 Amount: {format_money(int(tx.get('amount', 0)))}\n"
        f"💵 Balance Before: {format_money(int(tx.get('balance_before', 0)))}\n"
        f"💵 Balance After: {format_money(int(tx.get('balance_after', 0)))}"
        f"</blockquote>"
        + ("\n📎 Metadata:\n" + "\n".join(meta_lines) if meta_lines else "")
        + f"\n🕒 At: <code>{tx.get('created_at', '')}</code>"
    )


def taxinfo(taxes: dict[str, Any], pool: int, bank_settings: dict[str, Any]) -> str:
    """Admin view of every per-system tax rate + tax pool size."""
    rows = [f"<code>{k}</code>: <b>{v}%</b>" for k, v in taxes.items()]
    bank_rate = bank_settings.get("withdrawal_tax_rate", 5.0)
    return (
        f"<b>🏛️ TAX INFO</b>\n"
        f"<blockquote>"
        f"💰 Pool: {format_money(pool)}\n"
        f"🏦 Bank (withdrawal): <b>{bank_rate}%</b>"
        f"</blockquote>\n"
        f"📊 System taxes:\n" + "\n".join(rows)
    )


def tax_distribution(result: dict[str, Any]) -> str:
    """Report for a manual /dtax (or monthly) tax pool distribution."""
    rows = [
        f"<code>#{r['rank']}</code> · User <code>{r['user_id']}</code> — <b>{format_money(r['amount'])}</b>"
        for r in result.get("results", [])
    ]
    return (
        f"<b>🏛️ TAX DISTRIBUTION</b>\n"
        f"<blockquote>"
        f"💰 Pool: {format_money(result['pool'])}\n"
        f"💸 Distributed: <b>{format_money(result['distributed'])}</b>\n"
        f"👥 Recipients: {len(result.get('results', []))}"
        f"</blockquote>\n"
        f"{chr(10).join(rows)}"
    )


def userinfo(user: dict[str, Any], stats: dict[str, Any]) -> str:
    name = _user_name(user)
    net = (
        user.get("wallet", 0)
        + user.get("bank", 0)
        + user.get("stocks_value", 0)
        + user.get("asset_value", 0)
    )
    badges = []
    if user.get("is_banned"):
        badges.append("<s>BANNED</s>")
    if user.get("is_frozen"):
        badges.append("<s>FROZEN</s>")
    badge_text = " " + " ".join(badges) if badges else ""
    return (
        f"<b>👤 USER INFO</b>{badge_text}\n"
        f"<blockquote>"
        f"👤 {_link(user['user_id'], name)}\n"
        f"🆔 <code>{user['user_id']}</code>\n"
        f"💵 Wallet: {format_money(user.get('wallet', 0))}\n"
        f"🏦 Bank: {format_money(user.get('bank', 0))}\n"
        f"📈 Stocks: {format_money(user.get('stocks_value', 0))}\n"
        f"🏠 Assets: {format_money(user.get('asset_value', 0))}\n"
        f"💎 Net Worth: {format_money(net)}\n"
        f"💸 Total Earned: {format_money(user.get('total_earned', 0))}\n"
        f"💳 Total Spent: {format_money(user.get('total_spent', 0))}\n"
        f"🏆 Monthly Rank: {user.get('monthly_rank') or '—'}\n"
        f"🧾 Transactions: {stats['transactions']}"
        f"</blockquote>"
    )


GAME_TX_TYPES = {
    "GAME_BET", "GAME_WIN", "GAME_LOSS", "EMOJI_GAME_WIN", "EMOJI_GAME_LOSS",
    "EMOJI_GAME_REFUND", "EMOJI_DUEL_WIN", "EMOJI_DUEL_LOSS", "EMOJI_DUEL_DRAW",
    "EMOJI_DUEL_REFUND", "BLACKJACK_WIN", "BLACKJACK_LOSS", "BLACKJACK_DRAW",
}


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return "—"
    try:
        return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(ts)))
    except (ValueError, TypeError, OverflowError):
        return "—"


def user_data_report(
    user: dict[str, Any],
    *,
    stock_holdings: list[dict[str, Any]],
    asset_holdings: list[dict[str, Any]],
    cases: list[dict[str, Any]],
    dumps: list[dict[str, Any]],
    recovery: dict[str, Any] | None,
    quarantine: dict[str, Any] | None,
    transactions: list[dict[str, Any]],
    transaction_count: int,
) -> str:
    """Admin-only /data report.  Shows only real stored fields — never secrets."""
    uid = user.get("unique_user_id") or "—"
    name = escape(user.get("first_name") or "Unknown")
    username = user.get("username")
    username_line = f"@{escape(username)}" if username else "—"
    stocks_value = int(user.get("stocks_value", 0))
    asset_value = int(user.get("asset_value", 0))
    net = (
        int(user.get("wallet", 0))
        + int(user.get("bank", 0))
        + stocks_value
        + asset_value
    )

    badges = []
    if user.get("is_banned"):
        badges.append("<s>BANNED</s>")
    if user.get("is_frozen"):
        badges.append("<s>FROZEN</s>")
    if quarantine and quarantine.get("is_quarantined"):
        badges.append("<s>QUARANTINED</s>")
    badge_text = " " + " ".join(badges) if badges else ""

    games_played = sum(1 for tx in transactions if tx.get("type") in GAME_TX_TYPES)
    active_cases = sum(1 for c in cases if c.get("status") == "open")

    recovery_line = "—"
    if recovery:
        recovery_line = (
            f"Default <code>{format_money(int(recovery.get('recovery_balance', 0)))}</code>"
            f" · last dump <code>{recovery.get('last_dump_id') or '—'}</code>"
        )
    elif dumps:
        recovery_line = f"{len(dumps)} dump(s) available"

    tx_lines = []
    for tx in transactions[:8]:
        tx_lines.append(transaction_row(tx))

    return (
        f"<b>👤 USER DATA</b>{badge_text}\n"
        f"<blockquote>"
        f"🆔 <b>UNOITACHI UID:</b> <code>{uid}</code>\n"
        f"📱 <b>Telegram ID:</b> <code>{user['user_id']}</code>\n"
        f"👤 <b>Name:</b> {name}\n"
        f"💬 <b>Username:</b> {username_line}\n"
        f"📅 <b>Registered:</b> {_fmt_ts(user.get('created_at'))}\n"
        f"🕒 <b>Last Seen:</b> {_fmt_ts(user.get('last_seen_at') or user.get('last_active_at'))}\n\n"
        f"💵 <b>Wallet:</b> {format_money(int(user.get('wallet', 0)))}\n"
        f"🏦 <b>Bank:</b> {format_money(int(user.get('bank', 0)))}\n"
        f"📈 <b>Stocks:</b> {format_money(stocks_value)} ({len(stock_holdings)} holdings)\n"
        f"🏠 <b>Assets:</b> {format_money(asset_value)} ({len(asset_holdings)} holdings)\n"
        f"💎 <b>Net Worth:</b> {format_money(net)}\n\n"
        f"🏦 <b>Active Loan:</b> N/A (no loan system)\n"
        f"⚠️ <b>Security Cases:</b> {len(cases)} ({active_cases} open)\n"
        f"🛡 <b>Recovery:</b> {recovery_line}\n"
        f"💸 <b>Total Earned:</b> {format_money(int(user.get('total_earned', 0)))}\n"
        f"💳 <b>Total Spent:</b> {format_money(int(user.get('total_spent', 0)))}\n"
        f"🧾 <b>Transactions:</b> {transaction_count}\n"
        f"🎮 <b>Games Played:</b> {games_played}\n"
        f"</blockquote>"
        + (f"\n<b>🧾 RECENT TRANSACTIONS</b>\n" + "\n".join(tx_lines) if tx_lines else "")
    )


def banksettings(settings: dict[str, Any], tax_pool: int) -> str:
    return (
        f"<b>🏦 BANK SETTINGS</b>\n"
        f"<blockquote>"
        f"📈 Interest Rate: <b>{settings.get('interest_rate', 2.0)}%</b> / {settings.get('interest_interval_hours', 24)}h\n"
        f"🧾 Withdrawal Tax: <b>{settings.get('withdrawal_tax_rate', 5.0)}%</b>\n"
        f"🏛️ Tax Pool: {format_money(tax_pool)}"
        f"</blockquote>"
    )


def tax_reward_notice(rank: int, amount: int) -> str:
    return (
        f"<b>🏆 MONTHLY TAX REWARD</b>\n"
        f"You placed <b>#{rank}</b> this month and received "
        f"<b>{format_money(amount)}</b> from the tax pool!"
    )


def interest_notice(amount: int) -> str:
    return (
        f"<b>🏦 INTEREST CREDITED</b>\n"
        f"Your bank deposit earned <b>{format_money(amount)}</b> in 24h interest."
    )


def group_config_status(chat_id: int, cfg: dict[str, Any]) -> str:
    def mark(value: Any) -> str:
        return "✅ ON" if value else "⛔ OFF"

    return (
        f"<b>⚙️ GROUP CONFIG</b>\n"
        f"<blockquote>"
        f"🆔 Chat: <code>{chat_id}</code>\n"
        f"🤖 Bot: {mark(cfg.get('group_enabled', True))}\n"
        f"💰 Economy: {mark(cfg.get('economy_enabled', True))}\n"
        f"🎮 Games: {mark(cfg.get('games_enabled', True))}\n"
        f"🏆 Leaderboard: {mark(cfg.get('leaderboard_enabled', True))}\n"
        f"🛠 Admin Commands: {mark(cfg.get('admin_commands_enabled', True))}"
        f"</blockquote>\n"
        f"<i>Change with <code>/setchat setting on|off</code>.</i>"
    )


def asset_list(assets: list[dict[str, Any]], title: str = "🏠 ASSET MARKET") -> str:
    lines = [f"<b>{title}</b>", ""]
    for a in assets:
        arrow = "▲" if a.get("change_percent", 0) >= 0 else "▼"
        emoji = a.get("emoji", "📦")
        lines.append(
            f"{emoji} <code>{escape(a['symbol'])}</code> "
            f"{format_money(a.get('price', 0))} "
            f"<b>{arrow} {abs(a.get('change_percent', 0)):.2f}%</b>"
        )
    return "\n".join(lines)


def asset_detail(asset: dict[str, Any]) -> str:
    arrow = "▲" if asset.get("change_percent", 0) >= 0 else "▼"
    emoji = asset.get("emoji", "📦")
    frac = "Fractional" if asset.get("allow_fractional") else "Whole units"
    return (
        f"<b>{emoji} {escape(asset['symbol'])} — {escape(asset.get('name', ''))}</b>\n"
        f"<blockquote>"
        f"📋 Category: {escape(str(asset.get('category', 'OTHER')))}\n"
        f"💵 Price: <b>{format_money(asset.get('price', 0))}</b> "
        f"{arrow} {asset.get('change_percent', 0):.2f}%\n"
        f"📈 24h High: {format_money(asset.get('high_price', 0))}\n"
        f"📉 24h Low: {format_money(asset.get('low_price', 0))}\n"
        f"📊 Volatility: {asset.get('volatility', 0):.1%}\n"
        f"🔢 {frac} · Min {asset.get('min_quantity', 1):g}"
        + (f" · Max {asset.get('max_quantity', 0):g}" if asset.get("max_quantity") else "")
        + "\n"
        f"📝 {escape(asset.get('description', 'No description'))}"
        f"</blockquote>"
    )


def asset_trade(action: str, result: dict[str, Any]) -> str:
    verb = "Bought" if action == "buy" else "Sold"
    return (
        f"<b>✅ {verb} {escape(result['symbol'])}</b>\n"
        f"<blockquote>"
        f"🔢 Quantity: <b>{result['quantity']:g}</b>\n"
        f"💵 Total: <b>{format_money(result['total'] if action == 'buy' else result['received'])}</b>\n"
        f"🧾 <code>#{result['tx_id']}</code>"
        f"</blockquote>"
    )


def asset_confirm_buy(symbol: str, name: str, emoji: str, qty: float, price: int, total: int) -> str:
    return (
        f"<b>🛒 CONFIRM PURCHASE</b>\n"
        f"<blockquote>"
        f"{emoji} <code>{escape(symbol)}</code> — {escape(name)}\n"
        f"🔢 Quantity: <b>{qty:g}</b>\n"
        f"💵 Unit Price: <b>{format_money(price)}</b>\n"
        f"🧮 Total: <b>{format_money(total)}</b>"
        f"</blockquote>\n"
        f"<i>Price will be re-checked before the purchase completes.</i>"
    )


def asset_portfolio(rows: list[str], total_value: int, total_invested: int) -> str:
    pnl = total_value - total_invested
    sign = "+" if pnl >= 0 else ""
    lines = ["<b>🏠 ASSET PORTFOLIO</b>", ""]
    lines.extend(rows)
    lines += [
        "",
        f"<b>Total Asset Value:</b> {format_money(total_value)}",
        f"<b>Total Invested:</b> {format_money(total_invested)}",
        f"<b>Total P/L:</b> {sign}{format_money(pnl)}",
    ]
    return "\n".join(lines)


def asset_buy_info(info: dict[str, Any]) -> str:
    asset = info["asset"]
    arrow = "▲" if asset.get("change_percent", 0) >= 0 else "▼"
    emoji = asset.get("emoji", "📦")
    frac = "Fractional" if asset.get("allow_fractional") else "Whole units"
    fee = f" · Buy fee {info['fee_buy']:g}%" if info["fee_buy"] else ""
    return (
        f"<b>{emoji} {escape(asset['symbol'])} — {escape(asset.get('name', ''))}</b>\n"
        f"<blockquote>"
        f"💵 Current Price: <b>{format_money(asset.get('price', 0))}</b> "
        f"{arrow} {asset.get('change_percent', 0):.2f}% (24h)\n"
        f"📈 24h High: {format_money(asset.get('high_price', 0))} · "
        f"📉 24h Low: {format_money(asset.get('low_price', 0))}\n"
        f"📊 Volatility: {asset.get('volatility', 0):.1%}\n"
        f"🔢 {frac} · Min {asset.get('min_quantity', 1):g}"
        + (f" · Max {asset.get('max_quantity', 0):g}" if asset.get("max_quantity") else "")
        + f"{fee}\n"
        f"🏛️ Market Cap: {format_money(info['market_cap'])} · "
        f"👥 Holders: {info['holders']} · 📦 Held: {info['total_held']:g}\n"
        f"🧾 Trades: {info['trades']} · 📋 Category: {escape(str(asset.get('category', 'OTHER')))}\n"
        f"📝 {escape(asset.get('description', 'No description'))}"
        f"</blockquote>\n"
        f"<i>Buy with <code>/buyasset {asset['symbol']} qty</code> or grab a user resale via "
        f"<code>/listings {asset['symbol']}</code>.</i>"
    )


def asset_market_stats(stats: dict[str, Any]) -> str:
    return (
        f"<b>📊 ASSET MARKET STATS</b>\n"
        f"<blockquote>"
        f"📈 Total Assets: <b>{stats['active']}</b> / {stats['total']}\n"
        f"💹 Market Value: {format_money(stats['total_market_value'])}\n"
        f"🧾 Trading Volume: {format_money(stats['total_volume'])}\n"
        f"🟢 Gainers: {stats['gainers']} · 🔴 Losers: {stats['losers']} · ⚪ Flat: {stats['unchanged']}"
        f"</blockquote>"
    )


def listings_list(listings: list[dict[str, Any]], symbol: str | None, page: int, pages: int) -> str:
    header = "🛒 RESALE MARKET" + (f" — {escape(symbol)}" if symbol else "") + f" (pg {page}/{pages})"
    lines = [f"<b>{header}</b>", ""]
    for listing in listings:
        lines.append(
            f"{listing.get('emoji', '📦')} <code>{listing['listing_id']}</code> "
            f"{escape(listing['symbol'])} × <b>{listing['quantity']:g}</b> "
            f"→ <b>{format_money(listing['total_price'])}</b>"
        )
    lines += ["", "<i>Buy with <code>/buylisting ID</code>. List with <code>/listasset</code>.</i>"]
    return "\n".join(lines)


def my_listings(listings: list[dict[str, Any]]) -> str:
    if not listings:
        return "<b>🛒 MY LISTINGS</b>\n<i>You have no listings.</i>"
    lines = ["<b>🛒 MY LISTINGS</b>", ""]
    for listing in listings:
        status = {
            "active": "🟢",
            "pending": "🕐",
            "sold": "✅",
            "cancelled": "❌",
        }.get(listing["status"], "•")
        lines.append(
            f"{status} <code>{listing['listing_id']}</code> "
            f"{escape(listing['symbol'])} × <b>{listing['quantity']:g}</b> "
            f"→ <b>{format_money(listing['total_price'])}</b>"
        )
    return "\n".join(lines)


def emoji_lobby(game_label: str, emoji: str, bet: int, game_id: str, expiry: int) -> str:
    return (
        f"<b>⚔️ {emoji} {game_label} DUEL LOBBY</b>\n"
        f"<blockquote>🎰 Game ID: <code>{game_id}</code>\n"
        f"💰 Bet: <b>{format_money(bet)}</b>\n"
        f"⏳ Expires in <b>{format_duration(expiry)}</b></blockquote>\n"
        f"<i>Another player can join with <code>/join {game_id}</code>. "
        f"If nobody joins in time, your bet is refunded.</i>"
    )


def emoji_single_result(
    game_label: str,
    emoji: str,
    display_result: str,
    outcome: str,
    bet: int,
    payout: int,
    tx_id: str,
) -> str:
    head = f"{emoji} {game_label}"
    if outcome == "win":
        body = (
            f"<b>✅ YOU WON!</b>\n"
            f"{display_result} · Payout: <b>{format_money(payout)}</b> "
            f"(profit {format_money(payout - bet)})"
        )
    else:
        body = (
            f"<b>❌ YOU LOST</b>\n"
            f"{display_result} · Lost: {format_money(bet)}"
        )
    return f"<b>{head}</b>\n<blockquote>{body}\n🧾 <code>#{tx_id}</code></blockquote>"


def emoji_duel_result(
    game_label: str,
    emoji: str,
    player1_name: str,
    player2_name: str,
    display_result: str,
    winner_name: str | None,
    bet: int,
    payout: int,
    tx_id: str | None,
) -> str:
    lines = [
        f"<b>⚔️ {emoji} {game_label} DUEL</b>",
        "",
        f"🎲 {display_result}",
        "",
    ]
    if winner_name is None:
        lines += [
            "<b>🤝 DRAW!</b>",
            f"Both bets ({format_money(bet)}) returned.",
        ]
    else:
        lines += [
            f"<b>🏆 {escape(winner_name)} WINS!</b>",
            f"Pot: <b>{format_money(payout)}</b> "
            f"(bet {format_money(bet)} + {format_money(bet)})",
        ]
    if tx_id:
        lines += ["", f"🧾 <code>#{tx_id}</code>"]
    return "\n".join(lines)


def emoji_game_failed(game_label: str, emoji: str) -> str:
    return (
        f"<b>⚠️ {emoji} {game_label} — COULD NOT COMPLETE</b>\n"
        f"<blockquote>No dice value was received. "
        f"Your bet has been refunded.</blockquote>"
    )


def blackjack_result(
    user_cards: list[str],
    bot_cards: list[str],
    user_total: int,
    bot_total: int,
    outcome: str,
    bet: int,
    payout: int,
    tx_id: str,
) -> str:
    if outcome == "win":
        verdict = (
            f"<b>✅ YOU BEAT THE BOT!</b>\n"
            f"Payout: <b>{format_money(payout)}</b> "
            f"(profit {format_money(payout - bet)})"
        )
    elif outcome == "loss":
        verdict = f"<b>❌ BOT WINS</b>\nLost: {format_money(bet)}"
    else:
        verdict = f"<b>🤝 DRAW — bet returned</b> ({format_money(bet)})"
    return (
        f"<b>🃏 BLACKJACK</b>\n"
        f"<blockquote>🫵 You: {' '.join(user_cards)} → <b>{user_total}</b>\n"
        f"🤖 Bot: {' '.join(bot_cards)} → <b>{bot_total}</b>\n"
        f"{verdict}</blockquote>\n"
        f"🧾 <code>#{tx_id}</code>"
    )


def emoji_game_info(game_type: str, emoji: str, label: str, config: dict[str, Any]) -> str:
    lines = [
        f"<b>🎮 {emoji} {label} ({escape(game_type)})</b>",
        "",
        f"🟢 Enabled: <b>{'yes' if config.get('enabled', True) else 'no'}</b>",
        f"🔴 Single-player: <b>{'yes' if config.get('single_enabled', True) else 'no'}</b>",
        f"⚔️ Duels: <b>{'yes' if config.get('duel_enabled', True) else 'no'}</b>",
        f"⏳ Cooldown: <b>{config.get('cooldown', 60)}s</b>",
        f"💰 Bet range: <b>{format_money(config.get('minimum_bet', 0))} – {format_money(config.get('maximum_bet', 0))}</b>",
    ]
    if game_type == "slot":
        paytable = config.get("slot_payouts") or DEFAULT_SLOT_PAYOUTS
        lines.append("🎰 <b>Slot Paytable</b> (multiplier on bet):")
        for sym, vals in paytable.items():
            if "triple" in vals:
                lines.append(f"   {sym}×3: {vals['triple']}x")
            if "pair" in vals:
                lines.append(f"   {sym}×2: {vals['pair']}x")
    else:
        lines.append(
            f"🎯 Win rule: <b>{escape(config.get('win_rule', 'gte'))}</b> on "
            f"<b>{config.get('win_target', '-')}</b>"
        )
        lines.append(f"💥 Multiplier: <b>{config.get('multiplier', 1.0):.2f}x</b>")
    lines.append(f"⏲️ Lobby expiry: <b>{config.get('lobby_expiry', 300)}s</b>")
    return "\n".join(lines)


def emoji_games_list(configs: dict[str, Any], defs: dict[str, Any]) -> str:
    lines = ["<b>🎲 EMOJI GAMES</b>", ""]
    for game_type, game_def in defs.items():
        cfg = configs.get(game_type, {})
        status = "🟢" if cfg.get("enabled", True) else "🔴"
        lines.append(
            f"{status} {game_def.emoji} <code>{game_def.label}</code> "
            f"· single {format_money(cfg.get('minimum_bet', 0))}–{format_money(cfg.get('maximum_bet', 0))}"
            f" · duel bet {format_money(cfg.get('minimum_bet', 0))}–{format_money(cfg.get('maximum_bet', 0))}"
            f" · cooldown {cfg.get('cooldown', 60)}s"
        )
    lines.append(
        "",
        "<i>Play solo with <code>/sball /sarrow /sbasketball /sfootball /sslot /sdice</code> "
        "or duel with <code>/ball /arrow /basketball /football /slot /dice</code> "
        "+ <code>/join CODE</code>.</i>",
    )
    return "\n".join(lines)


def blackjack_info(config: dict[str, Any]) -> str:
    return (
        f"<b>🃏 BLACKJACK</b>\n"
        f"<blockquote>🟢 Enabled: <b>{'yes' if config.get('enabled', True) else 'no'}</b>\n"
        f"⏳ Cooldown: <b>{config.get('cooldown', 60)}s</b>\n"
        f"💰 Bet range: <b>{format_money(config.get('minimum_bet', 0))} – {format_money(config.get('maximum_bet', 0))}</b>\n"
        f"💥 Payout multiplier: <b>{config.get('multiplier', 1.0):.2f}x</b></blockquote>\n"
        f"<i>2 cards each, A=11/1, J/Q/K=10, highest total wins, ties refund the bet.</i>"
    )


# --------------------------------------------------------------------------- #
# Promo system
# --------------------------------------------------------------------------- #


def _fmt_qty(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _reward_line(reward: dict[str, Any]) -> str:
    kind = reward.get("type")
    if kind == "currency":
        return f"💰 {format_money(int(reward.get('amount', 0)))}"
    if kind == "stock":
        return f"📈 {escape(str(reward.get('symbol', '')))} × {_fmt_qty(float(reward.get('quantity', 0)))}"
    if kind == "asset":
        return f"🏠 {escape(str(reward.get('asset_id', '')))} × {_fmt_qty(float(reward.get('quantity', 0)))}"
    return f"🎁 {escape(str(reward))}"


def _promo_status(doc: dict[str, Any], now: int) -> str:
    if not doc.get("is_active"):
        return "Inactive"
    expires_at = doc.get("expires_at")
    if expires_at is not None and now >= int(expires_at):
        return "Expired"
    return "Active"


def _expiry_text(doc: dict[str, Any]) -> str:
    label = doc.get("expiry_label") or "Lifetime"
    expires_at = doc.get("expires_at")
    if expires_at is not None:
        remaining = int(expires_at) - int(time.time())
        if remaining > 0:
            label += f" ({format_duration(remaining)} left)"
        else:
            label += " (expired)"
    return label


def _uses_text(doc: dict[str, Any]) -> str:
    used = int(doc.get("redeemed_count", 0))
    mx = doc.get("max_redemptions")
    return f"{used} / {mx}" if mx is not None else f"{used} / ∞"


def promo_created(doc: dict[str, Any]) -> str:
    lines = [
        f"🎟 Code: <code>{escape(doc['code'])}</code>",
        f"⏰ Expiry: {_expiry_text(doc)}",
        f"👥 Limit: {_uses_text(doc)}",
        "🎁 Rewards:",
    ]
    lines.extend(f"  {_reward_line(r)}" for r in doc.get("rewards", []))
    return f"<b>✅ PROMO CREATED</b>\n<blockquote>{chr(10).join(lines)}</blockquote>"


def promo_redeemed(result: dict[str, Any]) -> str:
    lines = [
        f"🎟 Code: <code>{escape(result['promo']['code'])}</code>",
        "🎁 Rewards:",
    ]
    lines.extend(f"  {g['description']}" for g in result.get("granted", []))
    return (
        f"<b>🎁 PROMO REDEEMED</b>\n"
        f"<blockquote>{chr(10).join(lines)}</blockquote>\n"
        f"<b>✅ Rewards added successfully.</b>"
    )


def promo_already_used() -> str:
    return (
        "<b>⚠️ PROMO ALREADY USED</b>\n"
        "<blockquote>You have already redeemed this promo code. Each user can redeem once.</blockquote>"
    )


def promo_expired() -> str:
    return "<b>⌛ PROMO EXPIRED</b>\n<blockquote>This promo code has expired.</blockquote>"


def promo_inactive() -> str:
    return "<b>🚫 PROMO INACTIVE</b>\n<blockquote>This promo code is no longer active.</blockquote>"


def promo_limit_reached() -> str:
    return (
        "<b>❌ PROMO LIMIT REACHED</b>\n"
        "<blockquote>This promo code has reached its maximum redemption limit.</blockquote>"
    )


def promo_info(doc: dict[str, Any]) -> str:
    now = int(time.time())
    lines = [
        f"🎟 Code: <code>{escape(doc['code'])}</code>",
        f"📊 Status: <b>{_promo_status(doc, now)}</b>",
        f"⏰ Expiry: {_expiry_text(doc)}",
        f"👥 Uses: {_uses_text(doc)}",
        "👤 Per user: 1",
        "🎁 Rewards:",
    ]
    lines.extend(f"  {_reward_line(r)}" for r in doc.get("rewards", []))
    return f"<b>🎁 PROMO INFO</b>\n<blockquote>{chr(10).join(lines)}</blockquote>"


def promo_list(docs: list[dict[str, Any]], total: int, page: int, per_page: int) -> str:
    if not docs:
        return "<b>🎁 PROMO LIST</b>\n<blockquote>No promos found.</blockquote>"
    now = int(time.time())
    lines = [
        f"{idx}. <code>{escape(doc['code'])}</code> — <b>{_promo_status(doc, now)}</b> · "
        f"{_expiry_text(doc)} · {_uses_text(doc)}"
        for idx, doc in enumerate(docs, start=1)
    ]
    pages = max(1, math.ceil(total / per_page))
    return (
        f"<b>🎁 PROMO LIST</b>\n<blockquote>{chr(10).join(lines)}</blockquote>\n"
        f"<i>Page {page} of {pages} · Total {total}</i>"
    )


def promo_stats(stats: dict[str, Any]) -> str:
    promo = stats["promo"]
    lines = [
        f"🎟 Code: <code>{escape(promo['code'])}</code>",
        f"✅ Redemptions: {stats['total_redemptions']}",
        f"👥 Unique users: {stats['unique_users']}",
    ]
    remaining = stats["remaining"]
    lines.append(f"♻️ Remaining: <b>{'∞' if remaining is None else remaining}</b>")
    if stats["currency_total"]:
        lines.append(f"💰 Currency given: {format_money(int(stats['currency_total']))}")
    for symbol, qty in stats["stock_rows"]:
        lines.append(f"📈 Stock given: {_fmt_qty(qty)} × {escape(symbol)}")
    for asset_id, qty in stats["asset_rows"]:
        lines.append(f"🏠 Asset given: {_fmt_qty(qty)} × {escape(asset_id)}")
    if stats.get("last_redeemed_at"):
        ago = int(time.time()) - int(stats["last_redeemed_at"])
        lines.append(f"🕒 Last redemption: {format_duration(ago)} ago")
    return f"<b>📊 PROMO STATS</b>\n<blockquote>{chr(10).join(lines)}</blockquote>"
