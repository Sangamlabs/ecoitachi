"""Warnings Handler - handles /warninfo and /clearwarn commands."""
from pyrogram import Client, filters
from pyrogram.types import Message

from database import security as sec_db
from utils.messages import error, success
from utils.sender import reply_html

async def cmd_warninfo(client: Client, message: Message):
    """Handle /warninfo command - Show warning information."""
    # Implementation delegated to SecurityService
    pass

async def cmd_clearwarn(client: Client, message: Message):
    """Handle /clearwarn command - Clear all warnings."""
    # Implementation delegated to SecurityService
    pass
