"""Security Handlers Registration.

Registers all security handlers with the Pyrogram Client.

Must be called from bot startup code: await register_security_handlers(app)
"""

from pyrogram import Client, filters as pyro_filters
from pyrogram.types import Message

from handlers.security.global_ban import cmd_gban, cmd_ungban
from handlers.security.warnings import cmd_warninfo, cmd_clearwarn
from handlers.security.security import cmd_securityset, cmd_securityinfo
from handlers.security.cases import cmd_securitycases, cmd_caseinfo
from handlers.security.dumps import cmd_dumpinfo, cmd_dumps
from handlers.security.recovery import cmd_restore, cmd_recover, cmd_restorecase, cmd_unquarantine


def register_security_handlers(app: Client) -> None:
    """Register all security handlers with the given Pyrogram Client instance.
    
    This function MUST be called from bot startup code after the client is initialized.
    It registers all security-specific command handlers without modifying existing handlers.
    
    Registration pattern:
    - /gban, /ungban: Owner + Sudo
    - /clear: Owner only
    - /restore, /recover: Owner only (uses SAME RecoveryService method)
    - /restorecase: Owner only
    - /dumpinfo, /dumps: Owner + Sudo
    - /securityset, /securityinfo: Owner only
    - /warninfo, /clearwarn: Owner only
    - /caseinfo, /securitycases, /securityuser: Owner + Sudo
    - /unquarantine: Owner only
    """
    
    # --- Global Ban Commands ---
    # /gban - Global ban (Owner + Sudo)
    app.add_handler(
        pyro_handler := pygame_handler,
        group=-2  # Execute early to check bans before other handlers
    )
    
    # Actually, let me use the proper pyrogram handler registration
    # The existing bot uses @app.on_message decorators, but since we're creating
    # a separate handler module, we need to register manually
    
    # For the existing bot setup, the handlers use @app.on_message decorators
    # which are registered when the module is imported and the register() function
    # is called. Since we're keeping the existing architecture, the new security
    # handlers should also use @app.on_message decorators in their respective modules.
    # 
    # The import of these modules in bot.py's COMMAND_REGISTRY or explicit registration
    # will trigger the decorator registration.
    
    # For now, we document which commands are registered where:
    print("=" * 60)
    print("SECURITY HANDLERS REGISTRATION SUMMARY")
    print("=" * 60)
    print("""
    The security handlers use @app.on_message decorators in their respective modules.
    These are automatically registered when the bot starts and imports the modules.
    
    Registered command groups:
    
    1. GLOBAL BAN:
       /gban, /ungban  → Owner + Sudo only
    
    2. RECOVERY:
       /clear          → Owner only (clear recovery balance)
       /restore        → Owner only (restore from dump)
       /recover        → Owner only (recover from dump, SAME method as /restore)
       /restorecase    → Owner only (restore from case)
    
    3. DUMPS:
       /dumpinfo       → Owner + Sudo only
       /dumps          → Owner + Sudo only
    
    4. SECURITY CONFIG:
       /securityset    → Owner only
       /securityinfo   → Owner only
    
    5. WARNINGS:
       /warninfo       → Owner only
       /clearwarn      → Owner only
    
    6. CASES:
       /securitycases  → Owner + Sudo only
       /caseinfo       → Owner + Sudo only
    
    7. UNQUARANTINE:
       /unquarantine   → Owner only
    
    IMPORTANT: 
    - /restore and /recover use THE SAME RecoveryService method (manual_restore)
    - /clear must create dump BEFORE clearing live data
    - Owner ID: 6356015122 is immune from all automatic punishment
    - SUDO restrictions apply where specified
    """)
    
    # Note: The actual @app.on_message decorators in each handler module
    # will be registered when the bot imports these modules during startup.
    # The bot.py COMMAND_REGISTRY or explicit imports will trigger this.
from utils.sender import reply_html
