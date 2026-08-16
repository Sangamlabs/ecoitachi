"""Centralized HTML message builders.

Every user-facing Telegram message is built here as valid Telegram HTML and
sent through :mod:`utils.sender` with ``parse_mode=HTML``.  Dynamic content is
HTML-escaped before insertion.

Message content is kept separate from the send logic so future features
(e.g. wrapping cards in ``<blockquote>``) can be applied globally.
"""

from __future__ import annotations

from html import escape
from typing import Any

from utils.formatting import tg_link
from utils.money import format_money

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
        f"<code>/leader</code> — leaderboard\n\n"
        f"<b>🏦 Bank</b>\n"
        f"<code>/deposit amount</code> — wallet → bank\n"
        f"<code>/withdraw amount</code> — bank → wallet (tax applies)\n"
        f"<code>/bank</code> — bank info & interest\n"
        f"<code>/transactions</code> — recent transactions\n\n"
        f"<b>📈 Market</b>\n"
        f"<code>/stocklist</code> — market overview\n"
        f"<code>/stock SYMBOL</code> — asset details\n"
        f"<code>/buystock SYMBOL qty</code> — buy\n"
        f"<code>/sellstock SYMBOL qty</code> — sell\n"
        f"<code>/portfolio</code> — your holdings\n\n"
        f"<b>🎮 Games</b>\n"
        f"<code>/fly low|medium|high amount</code>\n"
        f"<code>/mines amount</code> — 6x6 mines board\n"
        f"<code>/bet amount</code> — coin bet\n"
        f"<code>/rob @user</code> — steal from a user's bank\n\n"
        f"<b>🎁 Free Rewards</b>\n"
        f"<code>/daily</code> — free currency every 24h\n"
        f"<code>/weekly</code> — free currency every 7 days\n"
        f"<code>/monthly</code> — free currency every 30 days\n\n"
        f"<i>Every game and /rob has a 60s cooldown. Bet within your wallet balance.</i>"
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
    net = user.get("wallet", 0) + user.get("bank", 0) + stocks_value
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
        f"💸 <b>Total Earned:</b> {format_money(user.get('total_earned', 0))}\n"
        f"💳 <b>Total Spent:</b> {format_money(user.get('total_spent', 0))}\n"
        f"🏆 <b>Leaderboard Rank:</b> {rank}"
        f"</blockquote>"
    )


def payment(sender: dict[str, Any], receiver: dict[str, Any], amount: int, tx_id: str) -> str:
    return (
        f"<b>✅ PAYMENT SENT</b>\n"
        f"<blockquote>"
        f"👤 To: {_link(receiver['user_id'], _user_name(receiver))}\n"
        f"💵 Amount: <b>{format_money(amount)}</b>\n"
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


def transaction_row(tx: dict[str, Any]) -> str:
    sign = {"PAY": "→", "GAME_LOSS": "−", "ADMIN_REMOVE": "−", "STOCK_BUY": "−",
            "WITHDRAW": "−", "TAX": "−", "ROBBED": "−"}.get(tx.get("type", ""), "＋")
    amount = tx.get("amount", 0)
    return (
        f"<code>{escape(tx.get('type', 'UNKNOWN'))}</code> {sign} "
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


def game_cooldown(game: str, remaining: int) -> str:
    return f"<b>⏳ {escape(game.title())} is on cooldown.</b>\n<i>Try again in {remaining}s.</i>"


def _cooldown_label(seconds: int) -> str:
    days, rem = divmod(int(seconds), 86_400)
    hours = rem // 3600
    if days:
        return f"{days}d"
    if hours:
        return f"{hours}h"
    return f"{int(seconds) // 60}m"


def reward_claimed(kind: str, amount: int, cooldown: int) -> str:
    return (
        f"<b>🎁 {escape(kind.title())} REWARD</b>\n"
        f"<blockquote>You claimed <b>{format_money(amount)}</b>.\n"
        f"Next claim in {_cooldown_label(cooldown)}.</blockquote>"
    )


def rob_result(result: dict[str, Any], robber: dict[str, Any], victim: dict[str, Any]) -> str:
    victim_name = _user_name(victim)
    if result["success"]:
        return (
            f"<b>🦹 ROBBERY SUCCESS</b>\n"
            f"<blockquote>You stole <b>{format_money(result['stolen'])}</b> from {victim_name}.\n"
            f"They had {format_money(result['target_bank_before'])} banked.</blockquote>\n"
            f"<i>Next robbery in {result['cooldown']}s.</i>"
        )
    return (
        f"<b>🚔 ROBBERY FAILED</b>\n"
        f"<blockquote>The police caught you robbing {victim_name}.\n"
        f"You got nothing.</blockquote>\n"
        f"<i>Next robbery in {result['cooldown']}s.</i>"
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
        f"<b>🛡 Permissions</b>\n"
        f"<code>/addsudo @user</code> — add sudo (owner only)\n"
        f"<code>/rsudo @user</code> — remove sudo (owner only)\n\n"
        f"<b>💰 Economy</b>\n"
        f"<code>/give @user amount</code> — give money\n"
        f"<code>/remove @user amount</code> — take money\n\n"
        f"<b>🏦 Bank</b>\n"
        f"<code>/setinterest rate</code> — interest % per 24h\n"
        f"<code>/settax rate</code> — withdrawal tax %\n"
        f"<code>/banksettings</code> — view bank settings\n\n"
        f"<b>🎮 Games</b>\n"
        f"<code>/flyset low|medium|high field value</code>\n"
        f"<code>/flytrap difficulty 8 values</code>\n"
        f"<code>/betset win_prob multiplier min_bet max_bet [cooldown]</code>\n"
        f"<code>/minestrap ...</code> — mines tuning\n"
        f"<code>/robset field value</code> — rob tuning\n\n"
        f"<b>🎁 Rewards</b>\n"
        f"<code>/setreward daily|weekly|monthly amount</code>\n\n"
        f"<b>👥 Users</b>\n"
        f"<code>/freeze @user</code> / <code>/unfreeze @user</code>\n"
        f"<code>/ban @user</code> / <code>/unban @user</code>\n"
        f"<code>/userinfo @user</code> — user details\n\n"
        f"<b>⚙️ Group config</b>\n"
        f"<code>/setchat [chat_id] [setting] [on|off]</code>\n\n"
        f"<b>📊 Stats</b>\n"
        f"<code>/econstats</code> — economy stats"
    )


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


def userinfo(user: dict[str, Any], stats: dict[str, Any]) -> str:
    name = _user_name(user)
    net = user.get("wallet", 0) + user.get("bank", 0) + user.get("stocks_value", 0)
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
        f"💎 Net Worth: {format_money(net)}\n"
        f"💸 Total Earned: {format_money(user.get('total_earned', 0))}\n"
        f"💳 Total Spent: {format_money(user.get('total_spent', 0))}\n"
        f"🏆 Monthly Rank: {user.get('monthly_rank') or '—'}\n"
        f"🧾 Transactions: {stats['transactions']}"
        f"</blockquote>"
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
