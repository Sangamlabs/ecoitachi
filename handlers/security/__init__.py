"""Security handlers layer for UNOITACHI Bot.

Separate module from handlers.admin - registers security-specific commands
including global ban, warnings, cases, dumps, and recovery operations.
"""

from __future__ import annotations

from pyrogram import Client, filters as pyro_filters
from pyrogram.types import Message

from database import security as sec_db
from services import security as security_service
from services import settings as settings_service
from services import economy as economy_service
from utils import messages as msgs
from utils.permissions import is_owner, is_sudo

__all__ = [
    "register_security_handlers",
]
from utils.sender import reply_html
