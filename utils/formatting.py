"""User-facing formatting helpers (no HTML building here - see messages.py)."""

from __future__ import annotations

import time


def tg_link(user_id: int, name: str) -> str:
    """Inline Telegram user link ``<a href="tg://user?id=..">name</a>``."""
    return f'<a href="tg://user?id={user_id}">{name}</a>'


def escape_markup(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def humanize_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0s"
    parts: list[str] = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count}{unit}")
    return " ".join(parts)


def utc_now() -> int:
    return int(time.time())


def format_datetime(ts: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts))


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or singular + "s")
