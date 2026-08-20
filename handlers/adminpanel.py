"""Interactive inline-button admin panel (OWNER/SUDO only).

The panel groups every admin command by category.  Every callback handler
re-verifies the Telegram user id with ``is_sudo`` — callback data is never
trusted to carry permission.  Callback ids are namespaced with an
``adminpanel:`` prefix so they cannot collide with other handlers.
"""

from __future__ import annotations

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import safe_handler
from utils import messages as msgs
from utils.permissions import is_sudo, sudo_only
from utils.sender import answer_callback, edit_html, reply_html

PREFIX = "adminpanel:"
CAT_PREFIX = f"{PREFIX}cat:"
HELP_CALLBACK = f"{PREFIX}help"
CLOSE_CALLBACK = f"{PREFIX}close"

# category key -> (button label, list of command lines)
CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "economy": (
        "💰 Economy",
        [
            "<code>/give @user amount</code> — give money",
            "<code>/remove @user amount</code> — take money",
            "<code>/getcoin amount</code> — credit yourself coins",
            "<code>/data USER</code> — full activity report",
            "<code>/track TX_ID</code> — transaction detail",
            "<code>/clearlb AMOUNT USER_COUNT</code> — deduct from top users",
            "<code>/econstats</code> — economy stats",
        ],
    ),
    "bank": (
        "🏦 Bank & Taxes",
        [
            "<code>/setinterest rate</code> — interest % per 24h",
            "<code>/setincome bank|asset|stock rate</code> — daily income %",
            "<code>/setreward daily|weekly|monthly amount</code> — rewards",
            "<code>/settax rate</code> — withdrawal tax %",
            "<code>/addtax system rate</code> — per-system tax %",
            "<code>/dtax</code> — distribute the tax pool now",
            "<code>/taxinfo</code> — tax rates + pool",
            "<code>/banksettings</code> — bank settings",
        ],
    ),
    "loans": (
        "💰 Loans",
        [
            "<code>/setloan field value</code> — loan config",
            "<code>/loanstats</code> — loan statistics",
            "<code>/loanuser USER</code> — a user's loans",
        ],
    ),
    "markets": (
        "📈 Stock & Asset Markets",
        [
            "<code>/addstock SYMBOL name price volatility</code> — list a stock",
            "<code>/rmstock SYMBOL</code> — delist a stock",
            "<code>/addasset SYMBOL name CATEGORY price volatility</code> — list an asset",
            "<code>/editasset SYMBOL field value</code> — edit asset",
            "<code>/assetset SYMBOL field value</code> — asset config",
            "<code>/assetprice SYMBOL price</code> — manual price",
            "<code>/assetvolatility SYMBOL v</code> — volatility",
            "<code>/rmasset SYMBOL</code> / <code>/restoreasset SYMBOL</code>",
            "<code>/assetinfo /assetlist /assetsearch</code> — market views",
            "<code>/assetowners SYMBOL</code> — top holders",
            "<code>/assetadminstats</code> — admin stats",
            "<code>/listinginfo ID</code> / <code>/forcelisting ID</code>",
        ],
    ),
    "promos": (
        "🎁 Promo Codes",
        [
            "<code>/addpromo CODE EXPIRY LIMIT REWARD...</code> — create",
            "<code>/rmpromo CODE</code> — disable (history kept)",
            "<code>/editpromo CODE FIELD VALUE...</code> — edit",
            "<code>/promoinfo CODE</code> / <code>/promolist [status] [page]</code>",
            "<code>/promostats CODE</code> — redemption statistics",
        ],
    ),
    "games": (
        "🎮 Games",
        [
            "<code>/flyset low|medium|high field value</code>",
            "<code>/flytrap difficulty 8 values</code>",
            "<code>/betset win_prob multiplier min_bet max_bet [cooldown]</code>",
            "<code>/minestrap ...</code> — mines tuning",
            "<code>/colourset field value</code> — colour tuning",
            "<code>/aviatorset field value</code> — aviator tuning (incl. crash_value)",
            "<code>/robset field value</code> — rob tuning",
            "<code>/emojiset GAME field value</code>",
            "<code>/emojitrap GAME key=value ...</code>",
            "<code>/emojigameinfo GAME</code> / <code>/emojigames</code>",
            "<code>/bjset field value</code> / <code>/bjinfo</code>",
        ],
    ),
    "users": (
        "👥 Users",
        [
            "<code>/freeze @user</code> / <code>/unfreeze @user</code>",
            "<code>/ban @user</code> / <code>/unban @user</code>",
            "<code>/leaderban @user</code> / <code>/leaderunban @user</code>",
            "<code>/gban @user</code> / <code>/ungban @user</code> — global ban",
            "<code>/userinfo @user</code> — user details",
            "<code>/setchat [chat_id] [setting] [on|off]</code> — group config",
        ],
    ),
    "broadcast": (
        "📢 Broadcast",
        [
            "<code>/bgc</code> — broadcast the replied-to message to all groups",
            "<code>/bdm</code> — broadcast to all users who started the bot",
            "<i>Reply to the message you want to broadcast, then send the command.</i>",
        ],
    ),
    "system": (
        "🖥 System & Recovery",
        [
            "<code>/dumps</code> / <code>/dumpinfo</code> — security dumps",
            "<code>/clear USER</code> — backup + reset economy (owner)",
            "<code>/restore DUMP-ID</code> / <code>/recover DUMP-ID</code> (owner)",
            "<code>/restorecase CASE-ID</code> (owner)",
            "<code>/securityset</code> — security config (owner)",
            "<code>/addsudo @user</code> / <code>/rsudo @user</code> — sudo (owner)",
            "<code>/admincmds</code> — OWNER-only command permission panel",
            "<code>/restart</code> — restart the bot",
        ],
    ),
}


def _main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"{CAT_PREFIX}{key}")]
        for key, (label, _) in CATEGORIES.items()
    ]
    rows.append(
        [
            InlineKeyboardButton("📖 Full admin help", callback_data=HELP_CALLBACK),
            InlineKeyboardButton("❌ Close", callback_data=CLOSE_CALLBACK),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _category_menu(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("◀️ Back", callback_data=f"{PREFIX}main"),
                InlineKeyboardButton("❌ Close", callback_data=CLOSE_CALLBACK),
            ]
        ]
    )


def _back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("◀️ Back", callback_data=f"{PREFIX}main"),
                InlineKeyboardButton("❌ Close", callback_data=CLOSE_CALLBACK),
            ]
        ]
    )


def register(app: Client) -> None:
    @app.on_message(filters.command("adminpanel") & ~filters.channel & ~filters.bot)
    @sudo_only
    @safe_handler(feature="admin")
    async def cmd_adminpanel(client: Client, message):
        """/adminpanel — interactive admin command overview (OWNER/SUDO only)."""
        await reply_html(
            client, message,
            msgs.info("🖥 <b>ADMIN PANEL</b>\nPick a category to view its commands."),
            reply_markup=_main_menu(),
        )

    @app.on_callback_query(filters.regex(rf"^{PREFIX}"))
    async def cb_adminpanel(client: Client, callback: CallbackQuery):
        if not callback.from_user or not callback.message:
            return
        if not await is_sudo(callback.from_user.id):
            await answer_callback(client, callback, "Only the owner/sudo can use the admin panel.", show_alert=True)
            return

        data = callback.data
        if data == CLOSE_CALLBACK:
            try:
                await callback.message.delete()
            except Exception:
                await edit_html(client, callback.message, "Panel closed.", reply_markup=None)
            await answer_callback(client, callback, "Panel closed.")
            return

        if data == f"{PREFIX}main":
            await edit_html(
                client, callback.message,
                msgs.info("🖥 <b>ADMIN PANEL</b>\nPick a category to view its commands."),
                reply_markup=_main_menu(),
            )
            await answer_callback(client, callback)
            return

        if data == HELP_CALLBACK:
            await edit_html(client, callback.message, msgs.admin_help(), reply_markup=_back_menu())
            await answer_callback(client, callback)
            return

        if data.startswith(CAT_PREFIX):
            key = data[len(CAT_PREFIX):]
            entry = CATEGORIES.get(key)
            if entry is None:
                await answer_callback(client, callback, "Unknown category.", show_alert=True)
                return
            label, lines = entry
            await edit_html(
                client, callback.message,
                f"<b>🖥 {label}</b>\n" + "\n".join(lines),
                reply_markup=_category_menu(key),
            )
            await answer_callback(client, callback)
            return

        await answer_callback(client, callback, "Unknown action.", show_alert=True)