# UNOITACHI Bot — Phase 1

A production-ready, modular **Telegram Economy Bot** built with **Python + Pyrogram + MongoDB**.
Everything runs asynchronously, all money is stored as integer sub-units, every financial
operation is atomic + audited, and every system is separated into modules so future phases
can be added without rewriting the economy engine.

## Features

- **DM + Group / Supergroup support** — every command works in private chats and
  groups; the economy account is **global per Telegram user id** (same wallet/bank/
  stocks everywhere), while game sessions are chat-bound
- **Reply-based commands** — `/bal` and `/pay` work by replying to any user in any chat
  (target resolution: reply user id → explicit id → username)
- **Centralized group config** — per-chat enable/disable of economy, games,
  leaderboard and admin commands via `/setchat` (extensible without touching handlers)
- **User profiles** — auto-created on first interaction (`/start`, `/profile`)
- **Wallet & payments** — `/bal`, `/pay`, transaction engine with unique IDs
- **Leaderboard** — net-worth ranking (wallet + bank + live stock value), modular categories
- **Full bank system** — `/deposit`, `/withdraw`, configurable interest (24h) and withdrawal tax
- **Tax pool & monthly distribution** — collected taxes are pooled and distributed monthly to the
  Top-10 earners using admin-configurable rank percentages (idempotent)
- **Stock/crypto market** — `/stocklist`, `/stock`, `/buystock`, `/sellstock`, `/portfolio`
  with a volatility-driven price simulator and price history
- **Games** — `/fly` (configurable difficulties), `/mines` (6×6 inline board), `/bet`;
  central game engine enforces cooldowns, bet limits, one-active-game and double-settlement protection;
  mines callbacks verify user + chat + message ownership
- **Owner + Sudo admin system** — numeric-ID based permission service with decorators;
  Telegram group-admin status is separate from the bot's admin hierarchy
- **Admin configuration** — interest/tax rates, fly/mines/bet tuning, freeze/ban, give/remove,
  economy stats (`/econstats`), group config (`/setchat`)
- **Centralized HTML messaging** — every message is built by `utils/messages.py` and sent via
  `utils/sender.py` with Telegram HTML parse mode; dynamic content is always escaped
- **Catbox media abstraction** — `services/media.py` for future media commands (graceful when disabled)
- **Audit logging** — every money movement creates a transaction record; secrets are filtered from logs

## DM + Group support

The bot is **DM + Group/Supergroup**: the same user account and economy data follow the
**Telegram user id** everywhere (private chat, group, supergroup) — there is never a
per-chat wallet.  Reply-based commands identify the target by the replied-to message's
numeric user id.

Per-chat behavior is controlled centrally in MongoDB (`group_config` collection) by
owner/sudo admins with `/setchat`:

```
/setchat                 # show this group's config (group)
/setchat games off       # disable games in this group (group)
/setchat -1001234567890 economy on   # configure from DM
```

Settings: `group`, `economy`, `games`, `leaderboard`, `admin_commands` — all default to
`on` in Phase 1.  When a feature is disabled the bot stays silent on those commands.
Admin commands (owner/sudo only) are blocked by `admin` even in groups; the owner always
bypasses.  `/addsudo` and `/rsudo` remain private-chat only.

## Architecture

```
Command (handler)  →  Service (business logic)  →  Data layer (Motor/MongoDB)
                          ↑
Game modules (games/) → central game engine (services/game_engine.py)
                          → economy engine (services/economy.py)
                          → transaction engine (services/transaction.py)
```

```
unoitachi-bot/
├── bot.py                 # entry point + centralized command registration
├── config.py              # env-based configuration
├── handlers/              # thin command handlers
│   ├── start.py           # /start /help
│   ├── economy.py         # /profile /bal /pay /leader
│   ├── bank.py            # /deposit /withdraw /bank /transactions
│   ├── stocks.py          # market commands
│   ├── games.py           # /fly /mines /bet + mines callbacks
│   └── admin.py           # owner/sudo administration
├── database/              # Motor data-access layer + indexes
├── services/              # business logic (economy, transaction, bank, interest, tax,
│                          #   stocks, leaderboard, game_engine, settings, media)
├── games/                 # fly, mines, bet (game-specific logic only)
├── utils/                 # money, validators, messages, sender, cooldown, permissions
├── scheduler/             # APScheduler jobs (interest, market, tax, game cleanup)
├── tests/                 # unit + integration tests
└── logs/                  # rotating application logs
```

## Requirements

- Python 3.11+
- MongoDB (local or Atlas)

## Installation

```bash
git clone <repo> unoitachi-bot
cd unoitachi-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit
```

### MongoDB setup

Any MongoDB (>= 4.4) works; the bot creates its own database and indexes on startup.

```bash
# Local MongoDB (Ubuntu example)
sudo systemctl enable --now mongod

# Or export a connection string for Atlas
export MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net/"
```

## Environment variables

| Variable | Description |
| --- | --- |
| `API_ID` | Telegram API ID (my.telegram.org) |
| `API_HASH` | Telegram API hash |
| `BOT_TOKEN` | Bot token from @BotFather |
| `MONGO_URI` | MongoDB connection string |
| `MONGO_DB_NAME` | Database name |
| `OWNER_ID` | Numeric Telegram ID of the bot owner (gets full control) |
| `CATBOX_ENABLED` | `true`/`false` — media uploads |
| `CATBOX_API_URL` | Catbox API endpoint |

Never commit your `.env`.

## Running locally

```bash
python bot.py
```

## Deployment

Deployment files live in the repo root (`Dockerfile`, `Procfile`, `railway.json`,
`runtime.txt`) and under `deploy/` and `scripts/`.

### Railway

1. Push this repo to GitHub, then create a **New Project → Deploy from GitHub repo**.
2. Add a **MongoDB** plugin (or set `MONGO_URI` to Atlas).
3. Set the variables from `.env.example` in **Variables** (`API_ID`, `API_HASH`,
   `BOT_TOKEN`, `OWNER_ID`, …).
4. Deploy. The included `railway.json` + `Dockerfile` are picked up automatically
   and run `python bot.py`.

### Heroku

```bash
heroku create unoitachi-bot
heroku buildpacks:set heroku/python
heroku config:set API_ID=... API_HASH=... BOT_TOKEN=... OWNER_ID=... \
  MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/
git push heroku main
heroku ps:scale worker=1
```

The `Procfile` runs the bot as a **worker** dyno (`worker: python bot.py`) so no
web port is required; `runtime.txt` pins the Python version. MongoDB must be
external (e.g. Atlas free tier) — set `MONGO_URI` accordingly.

### Ubuntu VPS

Automatic setup (installs MongoDB, clones the repo, creates venv + systemd unit):

```bash
sudo bash scripts/deploy_vps.sh
# edit /opt/unoitachi-bot/.env  (API_ID, API_HASH, BOT_TOKEN, OWNER_ID)
sudo systemctl restart unoitachi
journalctl -u unoitachi -f     # watch logs
```

Manual setup — copy `deploy/unoitachi.service` to
`/etc/systemd/system/unoitachi.service`, fix the `User=` line, then:

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now unoitachi
```

### Windows

```bat
:: run from the repo root, PowerShell/cmd:
scripts\install_windows.bat   :: venv + deps + .env (edit it afterwards)
scripts\start_windows.bat     :: launch the bot
```

### Docker (any host)

```bash
docker build -t unoitachi-bot .
docker run -d --env-file .env --name unoitachi unoitachi-bot
```

## Commands

| Group | Commands |
| --- | --- |
| Economy | `/start` `/profile` `/bal [@user\|id]` `/pay @user\|id amount` `/leader` — reply-based `/bal` `/pay` work in every chat |
| Bank | `/deposit amount` `/withdraw amount` `/bank` `/transactions` |
| Market | `/stocklist` `/stock SYMBOL` `/buystock SYMBOL qty` `/sellstock SYMBOL qty` `/portfolio` |
| Games | `/fly low\|medium\|high amount` `/mines amount` `/bet amount` |
| Owner | `/addsudo @user` `/rsudo @user` (private chat only) |
| Admin (owner + sudo) | `/give @user amount` `/remove @user amount` `/setinterest rate` `/settax rate` `/banksettings` `/flyset low\|medium\|high field value` `/flytrap low\|medium\|high min_mult max_mult risk win_prob cooldown min_bet max_bet` `/betset win_prob multiplier min_bet max_bet [cooldown]` `/minestrap bombs min_reveals min_bet max_bet cooldown duration` `/minestrap multipliers auto\|m1,m2,...` `/freeze @user` `/unfreeze @user` `/ban @user` `/unban @user` `/userinfo @user` `/econstats` `/setchat [chat_id] [setting] [on\|off]` |

## Admin system

Permissions resolve from **numeric Telegram IDs** (never usernames).

```
OWNER  (OWNER_ID)
  └─ SUDO ADMINS   (/addsudo by owner)
       └─ USERS
```

Decorators: `@owner_only`, `@sudo_only` / `@admin_only` (see `utils/permissions.py`).

## Database architecture

| Collection | Purpose | Key indexes |
| --- | --- | --- |
| `users` | profiles, balances, stats, flags | `user_id` (unique), `username`, `monthly_earnings` |
| `transactions` | full financial audit trail | `transaction_id` (unique), `user_id`, `created_at` |
| `admins` | sudo admins | `user_id` (unique) |
| `stocks` / `stock_holdings` / `stock_history` | market assets, holdings, price history | `symbol`, `user_id+symbol` |
| `game_sessions` / `game_cooldowns` | active games + cooldowns (survive restarts) | `game_id`, `game_cooldowns` TTL |
| `settings` | admin-configurable economy/game values | `key` (unique) |
| `bank_settings` | interest & tax rates | `key` (unique) |
| `tax_pool` / `tax_distributions` | tax pool + monthly distribution records | — |

## Money handling

- Money is an integer count of the smallest unit (₹1 = 100 units). **No floats.**
- All balance changes go through the central economy service with atomic guarded updates —
  concurrent commands cannot double-spend.
- Interest and monthly tax distribution are idempotent; game settlement can never pay twice.

## Testing

```bash
pip install -r requirements-dev.txt
pytest                    # unit tests run without MongoDB
pytest -v                 # integration tests auto-skip if MongoDB is unreachable
```

Coverage includes: money parsing/formatting, tax & interest math, mines multipliers,
HTML safety of every message builder, permission gates, config validation, double-spend
protection, and end-to-end economy flows.

## Development guidelines

1. Keep handlers thin — business logic belongs in `services/`.
2. Never modify wallet/bank balances outside the economy service.
3. All configurable values live in MongoDB settings, not in handlers.
4. Every financial change produces a transaction record.
5. All messages go through `utils/messages.py` + `utils/sender.py` (HTML only).
6. Add new commands to `handlers/` and register the module in `bot.py`'s `COMMAND_REGISTRY`.
7. New games implement game logic in `games/` and reuse `services/game_engine.py`.
