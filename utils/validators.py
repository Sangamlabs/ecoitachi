"""Input validation helpers shared by all handlers."""

from __future__ import annotations

import math
import re

from utils.money import MoneyError, parse_amount


def resolve_target(user, target_text: str | None):
    """Resolve a target user from command text or the replied-to message.

    Returns ``(user_id, username, first_name)`` or ``(None, None, None)``.
    """
    if target_text and re.fullmatch(r"@[\w]{3,32}", target_text.strip()):
        return target_text.strip(), target_text.strip()[1:], None
    if getattr(user, "reply_to_message", None) and user.reply_to_message.from_user:
        u = user.reply_to_message.from_user
        return u.id, u.username, u.first_name
    return None, None, None


def target_from_message(message) -> int | None:
    """Return the replied-to user's numeric id, or None when not replying to a user.

    The reply target is the most reliable identity — usernames can change, numeric
    Telegram user ids do not.
    """
    reply = getattr(message, "reply_to_message", None)
    if reply and getattr(reply, "from_user", None):
        return reply.from_user.id
    return None


def parse_target_arg(arg: str | None) -> tuple[int, str | None] | None:
    """Parse an explicit target argument.

    Returns ``(user_id, None)`` for a numeric id, ``(-1, username)`` for an
    @username reference (resolved separately via the database), or None when
    the argument is not a target reference.
    """
    if not arg:
        return None
    arg = arg.strip()
    if arg.isdigit():
        return int(arg), None
    if re.fullmatch(r"@[\w]{3,32}", arg):
        return -1, arg[1:]
    return None


def parse_user_id(raw: str) -> int | None:
    """Parse a numeric user id or @username reference.

    Returns an int for numeric ids, -1 sentinel for usernames (resolved
    separately via the database), or None when unparseable.
    """
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if re.fullmatch(r"@[\w]{3,32}", raw):
        return -1
    return None


def parse_amount_or_error(raw: str) -> tuple[int | None, str | None]:
    """Returns ``(amount_subunits, error_message)``."""
    try:
        amount = parse_amount(raw)
    except MoneyError as exc:
        return None, str(exc)
    if amount <= 0:
        return None, "Amount must be greater than 0."
    return amount, None


def is_safe_multiplier(value: float) -> bool:
    """Multipliers must be finite, non-negative, and within sane bounds."""
    return math.isfinite(value) and 0.0 <= value <= 1000.0


def is_safe_percent(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= 100.0


def is_safe_probability(value: float) -> bool:
    return math.isfinite(value) and 0.0 <= value <= 1.0


def validate_min_max(minimum: float, maximum: float) -> bool:
    return math.isfinite(minimum) and math.isfinite(maximum) and 0 <= minimum <= maximum
