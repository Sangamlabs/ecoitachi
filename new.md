You are now working on the UNOITACHI Telegram Economy Bot.

IMPORTANT:
Do NOT start coding immediately.

First inspect the entire repository and understand the existing architecture.

PROJECT:
UNOITACHI

STACK:
- Python 3.12
- Pyrogram
- MongoDB
- Motor
- APScheduler
- PM2 deployment
- Central Economy Engine
- Central Transaction Engine
- Central Permission System
- Central Settings System
- HTML Telegram message system

CURRENT ARCHITECTURE:

Wallet
Bank
Stocks
Assets Market
Games
Transactions
Leaderboard
Profile
Manual Security
GBAN
Warnings
Quarantine
Data Dump
Recovery

==================================================
CRITICAL ARCHITECTURE RULES
==================================================

DO NOT rewrite working systems.

DO NOT create duplicate:
- economy systems
- transaction systems
- permission systems
- settings systems
- database abstractions
- schedulers

Always reuse existing services.

Handlers must remain thin.

Business logic belongs in services.

Database operations belong in the existing database layer.

All money operations must go through the existing Economy Engine.

All financial transactions must go through the existing Transaction Engine.

All admin permissions must use the existing Permission System.

All Telegram messages must use the existing HTML message/sender architecture.

Never expose secrets/tokens/API keys in logs or messages.

Never use floating point for money.

Use the existing integer/sub-unit money representation.

==================================================
SECURITY ARCHITECTURE
==================================================

IMPORTANT CHANGE:

Security enforcement is MANUAL ONLY.

There must be NO automatic:

- GBAN
- warning
- quarantine
- economy wipe
- balance reset
- user deletion
- token detection ban
- automatic security punishment

Security infrastructure remains.

Manual security actions remain available to OWNER/SUDO according to the
existing permission system.

The common gate may CHECK existing manually stored security state.

Example:

Normal command
    ↓
common gate
    ↓
manual security state check
    ↓
allowed → command continues
blocked → command rejected

Security must NOT automatically punish users.

Keep:
- security database
- security cases
- audit logs
- dumps
- recovery IDs
- manual GBAN
- manual unGBAN
- manual quarantine
- manual unquarantine
- manual clear
- manual restore/recovery

==================================================
EXISTING SYSTEMS
==================================================

Before changing anything, inspect and understand:

handlers/
services/
database/
utils/
scheduler/
bot.py

Find the actual implementations of:

Economy Engine
Transaction Engine
Permission System
Settings System
Security Service
Security Database
Asset Market
Stock Market
Game Engine
Message Builder
Telegram Sender
Scheduler
Command Registry

Do not assume function names.

Read the actual code.

==================================================
PHASE 4 EMOJI GAMES
==================================================

Existing/required games:

Single player:

/sball amount
/sarrow amount
/sbasketball amount

Duel:

/ball amount
/arrow amount
/basketball amount

Duel flow:

/ball 500
    ↓
Generate 4-digit Game ID
    ↓
Player 1 waits
    ↓
/join GAME_ID
    ↓
Player 2 joins
    ↓
Send ONE emoji animation for Player 1
    ↓
wait ~1 second
    ↓
read actual Telegram result
    ↓
send ONE emoji animation for Player 2
    ↓
wait ~1 second
    ↓
read actual Telegram result
    ↓
compare
    ↓
settle

IMPORTANT:
Do NOT send duplicate emoji animations.

Single-player uses the "s" prefix.

Use actual Telegram native dice/emoji results.

Do not fake results with random numbers.

Existing game system must remain intact.

==================================================
ASSET MARKET
==================================================

Existing Phase 3 Asset Market supports:

/assets
/asset SYMBOL
/buyasset SYMBOL quantity
/sellasset SYMBOL quantity
/myassets
/assetstats

Assets are separate from Stocks.

Assets can be admin-listed.

Every listed asset has a unique asset_id.

The asset listing also stores the user/admin/listing owner where applicable.

Do not merge Assets and Stocks.

==================================================
PROMOCODE SYSTEM
==================================================

Promocodes may contain configurable rewards such as:

- money
- stocks
- assets
- other supported economy rewards

Promo codes support:

- lifetime
- minutes
- hours
- days
- max redeemers
- unlimited redeemers

Each user can redeem a specific promo code only once.

Promo redemption happens by detecting the promo code text directly in DM/GC
where supported.

Use atomic redemption protection.

==================================================
CURRENT NEW SYSTEMS TO IMPLEMENT
==================================================

We are now planning:

1. BROADCAST SYSTEM
2. LOAN SYSTEM

Do NOT implement yet.

First inspect the repository and tell me:

1. Current project structure
2. Existing relevant services
3. Existing database collections
4. Existing command registration architecture
5. Existing scheduler architecture
6. Existing Economy Engine entry points
7. Existing Transaction Engine entry points
8. Existing permission functions
9. Existing settings functions
10. Existing security architecture
11. Potential conflicts with Broadcast/Loan
12. Exact files you expect to modify

Then wait for my confirmation before coding.

==================================================
BROADCAST REQUIREMENTS
==================================================

Future commands:

/bgc
/bdm

/bgc = group/chat broadcast
/bdm = DM broadcast

OWNER/SUDO only.

Admin should preferably reply to an existing Telegram message and use:

/bgc

or:

/bdm

The original Telegram message should be copied/preserved rather than manually
reconstructed whenever possible.

Support Telegram-supported:

- text
- photo
- video
- GIF/animation
- document
- audio
- captions
- bold
- italic
- underline
- strikethrough
- spoiler
- quote
- blockquote
- links/entities
- media spoiler

For video/media, preserve spoiler state such as:

has_spoiler=True

Handle:
- FloodWait
- blocked users
- deleted accounts
- failed deliveries
- Telegram restrictions

Use batching/throttling.

Do not block the event loop.

Store broadcast statistics.

Require confirmation before large broadcasts.

==================================================
LOAN REQUIREMENTS
==================================================

Future commands:

/loan DAYS AMOUNT
/loaninfo
/loanpay AMOUNT
/loanpay all

Default:

Maximum principal: 100000
Minimum duration: 24 hours
Maximum duration: 7 days
Overdue interest: 1% per day
Recovery: 100%

All settings configurable by OWNER/SUDO.

One active loan per user.

Loan is a LIABILITY.

Borrowed money must NOT artificially increase net worth.

Net worth:

Wallet
+
Bank
+
Stock Value
+
Asset Value
-
Outstanding Loan Debt

Loan lifecycle:

REQUEST
 ↓
VALIDATE
 ↓
ISSUE
 ↓
ACTIVE
 ↓
DUE
 ↓
OVERDUE
 ↓
PAID

Loan interest starts AFTER due date.

Automatic recovery only happens when actual wallet income is credited.

Do not duplicate recovery logic across every command.

Use the central Economy Engine.

Transaction types:

LOAN_ISSUED
LOAN_PAYMENT
LOAN_INTEREST
LOAN_RECOVERY

Every operation must be atomic and idempotent.

No negative wallet.

No negative debt.

No duplicate loan.

No duplicate interest.

No duplicate recovery.

==================================================
DEVELOPMENT PROCESS
==================================================

Follow this workflow for EVERY change:

STEP 1:
Inspect existing code.

STEP 2:
Identify reusable services.

STEP 3:
Design integration points.

STEP 4:
Implement minimum required changes.

STEP 5:
Run syntax/import checks.

STEP 6:
Run existing regression tests.

STEP 7:
Test new functionality.

STEP 8:
Inspect git diff.

STEP 9:
Only then commit.

STEP 10:
Push only after verification.

NEVER:
- git reset --hard
- force push
- delete production data
- change production MongoDB
- expose secrets
- disable working features just to make tests pass
- create fake placeholder functions
- swallow exceptions with broad except/pass
- move unrelated commands into random handlers

==================================================
ENVIRONMENT
==================================================

Production bot and test bot MUST remain separate.

Production:
~/ecoitachi

Test:
~/testeco

Production uses production BOT_TOKEN/database.

Test uses test BOT_TOKEN/database.

Never mix them.

==================================================
FIRST TASK
==================================================

DO NOT CODE.

Inspect the repository and return a concise architecture report.

Tell me:

- project structure
- how handlers are registered
- how Economy Engine works
- how transactions work
- how permissions work
- how security works
- how scheduler works
- how MongoDB is organized
- where Broadcast should integrate
- where Loan should integrate
- any existing bugs/conflicts you discover

# CURRENT BUG-FIX TASK — DO NOT SKIP

IMPORTANT:

The existing UNOITACHI bot has several regressions.

DO NOT implement new features until these existing bugs are fixed.

DO NOT rewrite the entire economy system.

DO NOT create duplicate services/database layers.

DO NOT change MongoDB URL, BOT_TOKEN, production configuration, or existing
data.

The goal is to find the ROOT CAUSE of each bug and fix it using the existing
architecture.

==================================================
BUG 1 — /bet
==================================================

Command:

/bet amount

CURRENT PROBLEM:

/bet is not working and returns:

"Something went wrong"

Investigate the complete flow:

/bet
 ↓
command handler
 ↓
common gate
 ↓
permission/security checks
 ↓
bet/game service
 ↓
wallet validation
 ↓
atomic bet deduction
 ↓
game result
 ↓
settlement
 ↓
transaction
 ↓
response

DO NOT simply replace the error with a generic success message.

Find the actual exception.

Check:

- handler registration
- command filters
- function signatures
- imports
- Economy Engine calls
- wallet methods
- game configuration
- cooldown
- transaction creation
- MongoDB operations
- async/await usage
- incorrect/missing service methods
- changed function names
- stale imports
- security/common gate integration

Run the command with a TEST USER and inspect the actual traceback.

The user must receive a useful error only if an expected validation fails.

Unexpected exceptions must be fixed, not hidden.

DO NOT use:

except Exception:
    pass

DO NOT swallow the traceback.

==================================================
BUG 2 — ALL ADMIN COMMANDS
==================================================

CURRENT PROBLEM:

Admin commands are returning:

"Something went wrong"

This affects multiple admin commands.

Do NOT assume every admin command has a separate bug.

Trace the common admin architecture first:

Telegram command
 ↓
handler
 ↓
permission check
 ↓
OWNER/SUDO resolution
 ↓
target resolution if required
 ↓
service
 ↓
database
 ↓
response

Find whether the common failure is caused by:

- permission service
- admin decorator
- common.py
- security gate
- missing service method
- incorrect imports
- stale function names
- async/await errors
- database API changes
- wrong parameter order
- handler registration
- command collision
- exception handling

Test several different categories of admin commands, for example:

- user/economy admin command
- bank/settings admin command
- game configuration admin command
- stock/asset admin command
- security/admin command

Do NOT fix only one command if the root cause is shared.

If individual commands have different root causes, fix each one properly.

Normal Telegram group admins must NOT automatically receive bot-admin
permissions.

Only the existing OWNER/SUDO permission system should control admin commands.

==================================================
BUG 3 — /remove DOES NOT PERSIST CORRECTLY
==================================================

Command:

/remove USER AMOUNT

CURRENT BEHAVIOR:

The command appears to remove money/data from the user, but the MongoDB
stored balance is not updated correctly.

This is a CRITICAL ECONOMY CONSISTENCY BUG.

Trace the COMPLETE flow:

/remove
 ↓
target resolution
 ↓
permission check
 ↓
Economy Service
 ↓
database operation
 ↓
MongoDB
 ↓
transaction/audit
 ↓
balance response

Determine exactly where the value diverges.

IMPORTANT:

Do NOT simply modify a Python dictionary/object in memory.

The actual MongoDB document must be updated.

Example:

Before:

wallet = 50000

/remove USER 10000

After:

wallet = 40000

A fresh database read must return:

40000

The change must survive:

- command completion
- bot restart
- process restart
- fresh MongoDB query

==================================================
/REMOVE ATOMICITY
==================================================

The operation must be atomic.

Never do unsafe:

1. read balance
2. calculate balance
3. write balance

without concurrency protection.

Use the existing Economy Engine/database abstraction.

Prevent:

- negative wallet
- concurrent double removal
- stale balance
- lost updates
- duplicate transactions

If balance is insufficient:

DO NOT modify the database.

Example:

wallet = 5000

/remove USER 6000

Result:

Insufficient balance

MongoDB wallet MUST remain:

5000

==================================================
DATABASE VERIFICATION
==================================================

After fixing /remove, do NOT rely only on the bot's response.

Perform a fresh database/service read after the operation.

Test:

Before:
wallet = X

Run:

/remove USER amount

Then:

fresh database read
 ↓
wallet = X - amount

Then test:

/bal
/profile
/leader
/topbank

All must reflect the updated value according to their respective logic.

Restart the bot and verify again if practical.

==================================================
IMPORTANT ECONOMY RULE
==================================================

Do NOT create a second balance source.

There must be ONE authoritative wallet balance.

Do not maintain:

- separate cached wallet
- JSON wallet
- local dictionary wallet
- duplicate MongoDB wallet collection

unless the existing architecture explicitly requires caching.

If caching already exists, invalidate/update it correctly after /remove.

==================================================
ROOT-CAUSE INVESTIGATION
==================================================

Before editing code:

1. Search for /bet handler.
2. Search for /remove handler.
3. Search for the relevant Economy Engine methods.
4. Search database balance update methods.
5. Search Transaction Engine methods.
6. Search common.py/check_gate.
7. Search permission/admin decorators.
8. Search all references to methods used by these commands.
9. Check recent git changes.
10. Check whether security integration introduced the regression.

Use repository search rather than guessing function names.

==================================================
SECURITY REGRESSION CHECK
==================================================

The current project uses MANUAL security enforcement.

Do NOT reintroduce automatic:

- GBAN
- quarantine
- warning
- economy wipe
- balance reset
- user deletion

The common gate may only CHECK manually stored security state.

Do NOT fix economy/admin bugs by bypassing the security gate.

Do NOT remove security completely.

==================================================
ERROR HANDLING
==================================================

"Something went wrong" must NOT hide programming errors during development.

For unexpected exceptions:

- log full traceback server-side
- include command name
- include user ID only where appropriate for debugging
- never log tokens/API keys/passwords/MongoDB credentials
- return a safe user-facing error

Do not use broad exception handling to make commands appear functional.

==================================================
TESTING REQUIREMENTS
==================================================

After fixes, test:

GENERAL:

/start
/profile
/bal
/leader
/topbank
/bank

GAMES:

/bet
/mines
/fly

ADMIN:

Test multiple admin command categories.

ECONOMY:

/remove
/pay
/deposit
/withdraw

SECURITY:

Test only the currently implemented MANUAL security commands.

For /remove specifically verify persistence in MongoDB.

==================================================
REGRESSION PROTECTION
==================================================

Do NOT break:

- Wallet
- Bank
- Payments
- Stocks
- Assets
- Games
- Leaderboard
- Profile
- Transactions
- Promo system
- Manual security
- Recovery system

Do NOT modify unrelated code.

==================================================
DEBUGGING WORKFLOW
==================================================

Follow this exact workflow:

STEP 1:
Read new.md completely.

STEP 2:
Inspect the repository architecture.

STEP 3:
Reproduce /bet failure.

STEP 4:
Capture the real traceback.

STEP 5:
Find and fix root cause.

STEP 6:
Reproduce admin command failure.

STEP 7:
Find whether there is a shared root cause.

STEP 8:
Fix /remove persistence.

STEP 9:
Run syntax/import checks.

STEP 10:
Run regression tests.

STEP 11:
Inspect git diff.

STEP 12:
Only after everything passes, prepare commit.

DO NOT commit partial fixes.

DO NOT push broken code.

DO NOT force push.

DO NOT reset production code.

==================================================
FINAL REPORT
==================================================

Before committing, report:

1. /bet root cause
2. /bet files changed
3. Admin command root cause
4. Admin files changed
5. /remove root cause
6. /remove database update method
7. Whether balance persists after fresh DB read
8. Tests performed
9. Existing commands verified
10. Remaining errors, if any
11. Complete git diff summary

Only after all critical tests pass:

git status
git diff
git add <relevant files>
git commit -m "Fix economy and admin command regressions"
git push origin main

Never force push.

==================================================
PRIORITY
==================================================

Priority order:

1. Fix /remove database consistency
2. Fix /bet
3. Fix common admin-command failure
4. Run regression tests
5. Only then continue with new Broadcast/Loan development
# USER REGISTRY + /DATA + DATA COMMAND RELIABILITY + CLEAR FIX

IMPORTANT:
This is a CORE architecture improvement.

Before implementing anything, inspect the existing:
- user database
- economy database
- security database
- transaction system
- user registration functions
- user lookup functions
- handlers/common.py
- services/economy.py
- services/security.py
- database/security.py
- database/users.py or equivalent
- all admin user-target resolution helpers

DO NOT create a duplicate user system if one already exists.

==================================================
1. UNIVERSAL USER AUTO-REGISTRATION
==================================================

CURRENT PROBLEM:

Many admin/data commands return:

"User not found. They must start the bot."

This is incorrect for the new architecture.

A Telegram user should NOT be required to manually /start the bot before the
system can recognize them.

Implement a CENTRAL user-registration/identity layer.

Every Telegram user interacting with ANY bot functionality should automatically
receive a permanent internal unique ID.

Registration must happen automatically when the bot sees a user in:

- group messages
- commands
- replies
- mentions where Telegram provides user identity
- callbacks
- payments/economy actions
- game actions
- asset actions
- stock actions
- promo interactions
- security-related interactions
- any other supported bot interaction

A user does NOT need to DM /start.

==================================================
2. UNIQUE USER ID
==================================================

Every registered user receives:

unique_user_id

This is NOT the Telegram user ID.

It is an internal UNOITACHI identity ID.

Example:

UID-000001
UID-000002
UID-000003

OR use another collision-safe format compatible with the existing database.

The ID must be:

- globally unique
- permanent
- never reused
- stable across username changes
- stable across display-name changes

Do NOT use username as the primary identity.

Telegram user_id should remain stored as the external Telegram identifier.

Recommended identity fields:

unique_user_id
telegram_user_id
username
first_name
last_name
display_name
created_at
updated_at
last_seen_at

Use the existing user collection/schema if available.

Do NOT create duplicate user documents.

==================================================
3. USERNAME IS NOT IDENTITY
==================================================

IMPORTANT:

Do NOT identify users permanently by:

@username

because usernames can:

- change
- disappear
- be reused
- be absent

Primary lookup order should be:

1. Telegram user ID
2. UNOITACHI unique_user_id
3. username as a lookup convenience
4. message/reply entity where available

If username changes, update the existing user document instead of creating a
new user.

==================================================
4. CENTRAL ENSURE-USER FUNCTION
==================================================

Create/reuse one central function such as:

ensure_user(...)

or the project's equivalent.

Every command/event entry point should eventually pass through this identity
layer.

The function must:

1. Detect Telegram user
2. Find existing user by telegram_user_id
3. If not found, create user
4. Assign permanent unique_user_id
5. Update username/name/last_seen
6. Return the user identity

The operation must be atomic.

Two simultaneous messages from the same new user must NOT create two users.

Use MongoDB unique indexes where appropriate.

==================================================
5. DO NOT REQUIRE /START
==================================================

Remove the assumption:

"user must start the bot"

for internal user registration.

A user can become registered through a group interaction.

Example:

User sends:

hello

in a group where the bot is present.

System:

Telegram user detected
        ↓
ensure_user()
        ↓
create UNOITACHI user
        ↓
assign unique_user_id

No /start required.

==================================================
6. ECONOMY AUTO-REGISTRATION
==================================================

If a user receives coins/money through any supported economy mechanism:

ensure_user() MUST happen before the economy operation.

Example:

User receives:

₹1000

Flow:

Telegram user
 ↓
ensure_user()
 ↓
Economy Engine
 ↓
wallet credit
 ↓
Transaction

Never create economy balances for a user without creating the corresponding
user identity record.

==================================================
7. GAMES AUTO-REGISTRATION
==================================================

Before:

/bet
/mines
/fly
emoji games
blackjack
etc.

ensure_user() must execute.

Do not require /start.

==================================================
8. ASSETS / STOCKS AUTO-REGISTRATION
==================================================

Before any user performs:

asset operations
stock operations
buy/sell
portfolio operations

ensure_user() must execute.

==================================================
9. /DATA COMMAND
==================================================

Add:

/data USER

The command is OWNER/SUDO only.

Purpose:

Allow authorized administrators to inspect the complete NON-SECRET activity
profile of a user using:

- Telegram user ID
- username
- UNOITACHI unique user ID

Examples:

/data 123456789

/data @username

/data UID-000123

Also support replying to a user's message:

reply to user
/data

==================================================
10. /DATA OUTPUT
==================================================

Show a structured admin-only user activity report.

Example:

<b>👤 USER DATA</b>

<blockquote>
<b>UNOITACHI UID:</b> <code>UID-000123</code>
<b>Telegram ID:</b> <code>123456789</code>

<b>Username:</b> @username
<b>Name:</b> User Name

<b>Registered:</b> ...
<b>Last Seen:</b> ...

<b>Wallet:</b> ₹25,000
<b>Bank:</b> ₹50,000

<b>Assets:</b> ...
<b>Stocks:</b> ...

<b>Active Loan:</b> ...
<b>Warnings:</b> ...
<b>Security Status:</b> ...

<b>Total Transactions:</b> ...
<b>Games Played:</b> ...
</blockquote>

Use the ACTUAL fields available in the repository.

Do NOT invent values.

If a subsystem does not store a particular statistic:

show:

N/A

rather than generating fake data.

==================================================
11. ACTIVITY HISTORY
==================================================

If the existing transaction/audit system supports it, /data should also show
recent activity.

Example:

Recent Transactions:
- +₹500 GAME_WIN
- -₹100 BET
- +₹1000 PAYMENT
- -₹200 PURCHASE

Limit the output to a reasonable recent number.

Do not dump thousands of records into one Telegram message.

If more history is needed, create pagination or a separate admin command.

==================================================
12. SECURITY DATA
==================================================

/data may show authorized security information to OWNER/SUDO such as:

- manual GBAN status
- warning count
- quarantine status
- security case ID
- recovery/dump IDs
- security action history

But NEVER show:

- BOT_TOKEN
- API keys
- MongoDB URI
- passwords
- session strings
- private credentials
- environment secrets

Even to administrators.

==================================================
13. /REMOVE USER RESOLUTION
==================================================

Fix:

/remove USER AMOUNT

The command must resolve users through the central identity system.

Support:

/remove 123456789 1000

/remove @username 1000

/remove UID-000123 1000

and replying to a user's message:

/remove 1000

Do not require the target user to have used /start.

If the Telegram user has interacted with the bot in a group, ensure_user()
should already have created their record.

If a username lookup fails but the user cannot be resolved through Telegram,
return a clear message asking for Telegram user ID / UNOITACHI UID or a reply.

Do NOT incorrectly say:

"They must start the bot."

==================================================
14. /REMOVE ECONOMY CONSISTENCY
==================================================

The existing economy.admin_remove() has already been verified to persist
correctly.

DO NOT rewrite it unnecessarily.

Fix only the target resolution/handler/database integration required.

Flow:

/remove
 ↓
resolve target
 ↓
ensure/verify user
 ↓
permission
 ↓
economy.admin_remove()
 ↓
atomic MongoDB update
 ↓
transaction
 ↓
response

After removal:

/bal
/profile
/leader
/topbank

must reflect the new value.

==================================================
15. FIX /CLEAR CURRENT CRASH
==================================================

CURRENT ERROR:

handler cmd_clear crashed:

module 'services.economy' has no attribute 'reset_recovery_balance'

Current call:

services/security.py

manual_clear()

currently attempts:

econ.reset_recovery_balance(...)

This method does NOT exist.

DO NOT add a fake reset_recovery_balance() method to EconomyService merely to
silence the error.

Inspect the existing recovery/security database architecture.

If the correct method already exists in:

sec_db

or the security database layer, use that.

Expected architecture:

manual_clear
    ↓
SecurityService
    ↓
Security DB
    ↓
recovery balance/reset method

EconomyService must NOT become the owner of security recovery state.

Fix the call to use the correct existing security/recovery database method.

If no correct method exists, implement it in the SECURITY DATABASE layer, not
in EconomyService.

==================================================
16. /CLEAR SAFETY
==================================================

/clear is destructive.

Before clearing user economy/recovery data:

1. Resolve user
2. Verify OWNER/SUDO permission
3. Create/verify recovery dump
4. Generate unique recovery ID
5. Store audit record
6. Perform clear atomically
7. Return recovery ID to authorized admin

Do not silently destroy data.

==================================================
17. DATABASE INDEXES
==================================================

Ensure appropriate unique indexes exist.

At minimum:

telegram_user_id UNIQUE
unique_user_id UNIQUE

If username indexing already exists, use it appropriately but DO NOT make
username unique because usernames can change/disappear.

==================================================
18. CENTRAL USER RESOLUTION
==================================================

Create/reuse one helper such as:

resolve_user()

It should support:

Telegram user ID
UNOITACHI unique ID
username
reply user
Telegram User object

All admin/data/economy target commands should reuse it.

Do NOT implement separate username lookup logic in every handler.

Potential consumers:

/data
/remove
/clear
/recover
/restore
/loaninfo admin lookup
/security
/gban
/ungban
/warn
/assets admin tools
/admin economy tools

==================================================
19. USERNAME CHANGE HANDLING
==================================================

If:

old username = @abc

becomes:

new username = @xyz

DO NOT create a new user.

Update the same user record:

telegram_user_id = same
unique_user_id = same
username = @xyz

History remains attached to the same UID.

==================================================
20. MIGRATION OF EXISTING USERS
==================================================

Existing users may not have unique_user_id.

Do NOT delete them.

Create a safe migration/backfill process.

For every existing user:

- generate unique_user_id if missing
- preserve telegram_user_id
- preserve username
- preserve wallet
- preserve bank
- preserve assets
- preserve stocks
- preserve transactions
- preserve security state

Migration must be idempotent.

Running it twice must not create new IDs.

==================================================
21. TESTING
==================================================

Create TEST users and verify:

1. New user sends group message.
   → automatically registered.

2. User never /start'ed.
   → /data can still resolve them after interaction.

3. Username lookup works.

4. Telegram ID lookup works.

5. UNOITACHI UID lookup works.

6. Reply-based lookup works.

7. Username change preserves same UID.

8. /remove works for a registered user.

9. /remove does NOT require /start.

10. /remove updates fresh MongoDB data.

11. /data shows current wallet/bank/activity.

12. /clear no longer throws:
    economy.reset_recovery_balance

13. /clear uses security DB/recovery layer correctly.

14. /recover /restore still work.

15. Normal economy commands still work.

16. No duplicate users are created during concurrent registration.

==================================================
22. REGRESSION PROTECTION
==================================================

Do NOT break:

- Wallet
- Bank
- Transactions
- Assets
- Stocks
- Games
- Leaderboard
- Profile
- Promo system
- Manual Security
- GBAN
- Recovery
- Broadcast
- existing commands

Do NOT change:

BOT_TOKEN
MONGO_URI
production database

Do not delete existing user data.

==================================================
23. DEVELOPMENT WORKFLOW
==================================================

Before coding:

1. Read new.md.
2. Inspect existing user model/database.
3. Inspect economy user creation.
4. Inspect all target-resolution helpers.
5. Inspect security recovery database.
6. Reproduce /remove failure.
7. Reproduce /clear failure.
8. Design central identity/lookup integration.

Then implement.

After implementation:

- syntax checks
- import checks
- unit tests
- MongoDB integration tests where available
- manual Telegram test

Do not hide exceptions.

Do not use:

except Exception:
    pass

Do not create fake placeholder functions.

Do not duplicate database systems.

==================================================
FINAL REPORT
==================================================

Report:

1. Existing user architecture discovered
2. New unique UID implementation
3. Auto-registration integration points
4. Central user resolver
5. /data implementation
6. /remove fix
7. /clear recovery fix
8. Database indexes
9. Migration/backfill strategy
10. Tests performed
11. Remaining errors

DO NOT COMMIT OR PUSH until runtime tests pass.

DO NOT force push.
==================================================
GOAL 7 — FIX NEW LOAN SYSTEM
==================================================

The repository also contains a newly added LOAN system, but the new loan
commands are currently not working correctly.

Do NOT assume the loan implementation is correct.

First inspect:

- loan handlers
- loan service
- loan database/model
- command registration
- Economy Engine integration
- Transaction Engine integration
- APScheduler loan jobs
- permission/admin settings
- /loaninfo
- /loan
- /loanpay

Expected commands:

/loan DAYS AMOUNT
/loaninfo
/loanpay AMOUNT
/loanpay all

Example:

/loan 7 50000

Default rules:

Maximum principal: 100000
Minimum duration: 24 hours
Maximum duration: 7 days
Overdue interest: 1% per day
One active loan per user

Admin-configurable settings should be preserved if already implemented.

IMPORTANT:

Do NOT create a second economy/transaction system.

Use the existing Economy Engine.

Use the existing Transaction Engine.

Loan must be treated as a LIABILITY.

Borrowed money must not incorrectly inflate leaderboard/net-worth values.

Investigate why the commands are not working.

Check:

- command registration
- handler imports
- handler signatures
- permission checks
- user registration
- user resolution
- loan service imports
- database methods
- MongoDB collection
- missing methods
- incorrect method names
- async/await errors
- scheduler registration
- configuration/settings
- common.py gate
- security gate

Reproduce the actual error and capture the traceback.

Do NOT hide the error with generic exception handling.

Expected architecture:

/loan
 ↓
resolve/ensure user
 ↓
validate loan
 ↓
Loan Service
 ↓
Economy Engine
 ↓
MongoDB
 ↓
Transaction Engine
 ↓
response

Verify:

/loan 7 50000

creates exactly ONE active loan.

Verify:

/loaninfo

shows the current loan.

Verify:

/loanpay 10000

reduces the outstanding debt.

Verify:

/loanpay all

fully repays the loan if the wallet has sufficient funds.

Verify overdue interest does not double-charge.

Verify automatic loan recovery does not double-charge.

==================================================
GOAL 8 — FIX NEW BROADCAST SYSTEM
==================================================

The newly added BROADCAST system is also currently not working.

Commands:

/bgc
/bdm

/bgc = group/chat broadcast
/bdm = DM broadcast

OWNER/SUDO only.

Do NOT assume the existing broadcast implementation is correct.

First inspect:

- broadcast handlers
- broadcast service
- command registration
- target/user registry
- group registry
- Telegram sender/copy functions
- FloodWait handling
- permission checks
- database/logging
- callback/confirmation system if implemented

Reproduce the actual error.

Determine whether the problem is:

- command not registered
- handler not imported
- wrong handler signature
- permission failure
- missing service
- missing database method
- incorrect Telegram API call
- incorrect media handling
- incorrect message copy logic
- async/await issue
- user/group target discovery
- FloodWait handling
- common security gate

Do NOT simply make /bgc or /bdm return "success".

The broadcast must actually send the message.

==================================================
BROADCAST MESSAGE REQUIREMENTS
==================================================

Admin should preferably reply to an existing Telegram message and use:

/bgc

or:

/bdm

The original message should be copied/preserved where possible.

Support Telegram-supported:

- text
- photo
- video
- GIF/animation
- document
- audio
- captions
- bold
- italic
- underline
- strikethrough
- spoiler
- quote
- blockquote
- links/entities
- media spoiler

For supported media, preserve the original spoiler state.

Do not manually reconstruct message entities if Telegram copy functionality
can preserve them.

Handle:

- blocked users
- deleted users
- forbidden chats
- FloodWait
- failed sends
- Telegram rate limits

Use controlled batching/throttling.

Do not block the asyncio event loop.

==================================================
BROADCAST SAFETY
==================================================

Before a large broadcast, use the existing confirmation architecture if
implemented.

Do not accidentally send a broadcast twice.

A broadcast should have a unique broadcast ID.

Log:

broadcast_id
type
sender_id
created_at
total_targets
sent
failed
blocked

Never log secrets.

==================================================
IMPORTANT — NEW COMMANDS MUST BE REGISTERED
==================================================

For BOTH LOAN and BROADCAST, verify the complete registration chain:

command
 ↓
handler module
 ↓
register(app)
 ↓
bot.py / COMMAND_REGISTRY
 ↓
Pyrogram handler
 ↓
service
 ↓
database

Do not assume that simply creating:

handlers/loan.py
handlers/broadcast.py

makes the commands active.

Explicitly verify that the modules are imported and registered during bot
startup.

Check for duplicate command registration too.

==================================================
NEW COMMAND REGRESSION RULE
==================================================

Existing commands must continue working.

Do NOT fix the new commands by disabling:

- common gate
- security
- permissions
- economy validation

Do NOT bypass the manual security system.

The security system remains MANUAL ONLY.

==================================================
DEBUGGING PRIORITY
==================================================

Current priority order:

1. Universal user registration / UID
2. Central user resolver
3. /remove user resolution + DB persistence
4. /clear recovery crash
5. /loan commands
6. /broadcast commands
7. Regression testing

Do NOT implement additional features until these are stable.
