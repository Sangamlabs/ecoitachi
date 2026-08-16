"""Application-wide logging setup.

Never log secrets (tokens, hashes, passwords).  Sensitive key=value pairs are
redacted by a filter here so a mistake elsewhere cannot leak them.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys

from config import config

# Matches e.g. BOT_TOKEN=123:abc, API_HASH=deadbeef, PASSWORD=hunter2
_SECRET_PATTERN = re.compile(
    r"(?i)(MONGO_URI|BOT_TOKEN|API_HASH|API_ID|OWNER_ID|PASSWORD|PASSWD|SECRET|TOKEN|HASH)="
    r"([^\s&\",]+)"
)


def redact(text: str) -> str:
    """Replace secret-looking ``KEY=value`` pairs with ``KEY=***``."""
    return _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***", text)


class SecretFilter(logging.Filter):
    """Redact secret values before a record is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging() -> logging.Logger:
    """Configure and return the root logger used across the project."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(SecretFilter())

    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_DIR / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(SecretFilter())

    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger."""
    return logging.getLogger(name)
