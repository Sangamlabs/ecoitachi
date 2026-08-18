"""Central configuration loader.

Reads environment variables from a ``.env`` file (via python-dotenv) and the
process environment.  Secrets are never hardcoded here.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


class Config:
    """Application configuration exposed as a single object."""

    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "unoitachi_bot")
    OWNER_ID: int = int(os.getenv("OWNER_ID", "0"))
    CATBOX_ENABLED: bool = _get_bool("CATBOX_ENABLED", False)
    CATBOX_API_URL: str = os.getenv(
        "CATBOX_API_URL", "https://catbox.moe/user/api.php"
    )
    INTEREST_CHECK_INTERVAL_MINUTES: int = int(
        os.getenv("INTEREST_CHECK_INTERVAL_MINUTES", "5")
    )
    STOCK_UPDATE_INTERVAL_MINUTES: int = int(
        os.getenv("STOCK_UPDATE_INTERVAL_MINUTES", "2")
    )
    LOAN_MAINTENANCE_INTERVAL_MINUTES: int = int(
        os.getenv("LOAN_MAINTENANCE_INTERVAL_MINUTES", "5")
    )
    PM2_APP_NAME: str = os.getenv("PM2_APP_NAME", "testeco")
    LOG_DIR: Path = BASE_DIR / "logs"

    def validate(self) -> None:
        """Raise a clear error if mandatory configuration is missing."""
        missing: list[str] = []
        if not self.API_ID:
            missing.append("API_ID")
        if not self.API_HASH:
            missing.append("API_HASH")
        if not self.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not self.OWNER_ID:
            missing.append("OWNER_ID")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in."
            )


config = Config()
