"""Security handlers layer for UNOITACHI Bot.

Separate module from handlers.admin - registers security-specific commands
including global ban, warnings, cases, dumps, and recovery operations.

When this module is imported during bot startup (via COMMAND_REGISTRY in bot.py),
the @app.on_message decorators in each handler module are registered with the
Pyrogram Client.

The following security commands are registered:
- /gban, /ungban: Owner + Sudo
- /clear: Owner only
- /restore, /recover: Owner only (same RecoveryService method)
- /restorecase: Owner only
- /dumpinfo, /dumps: Owner + Sudo
- /securityset: Owner only
- /warninfo, /clearwarn: Owner only
- /caseinfo, /securitycases, /securityuser: Owner + Sudo
- /unquarantine: Owner only
"""

from __future__ import annotations

from pyrogram import Client

# Import all security handler modules to trigger @app.on_message decorator registration
from handlers.security import global_ban  # noqa: F401
from handlers.security import warnings  # noqa: F401
from handlers.security import security  # noqa: F401
from handlers.security import cases  # noqa: F401
from handlers.security import dumps  # noqa: F401
from handlers.security import recovery  # noqa: F401


def register(app: Client) -> None:
    """Register all security handlers with the Pyrogram Client.
    
    This function is called from bot.py's register_handlers() function.
    It ensures all security handler modules are imported, which triggers
    the @app.on_message decorator registration in each submodule.
    """
    # The @app.on_message decorators in each handler module are registered
    # when the module is imported. The import statements above trigger this.
    # Additional registration logic can be added here if needed.
    pass
