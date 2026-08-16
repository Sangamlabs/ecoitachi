"""Media service - Catbox upload abstraction.

Future media commands call :func:`upload_media` instead of touching the Catbox
API directly.  When ``CATBOX_ENABLED=false`` the service fails gracefully.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from config import config

logger = logging.getLogger(__name__)


class MediaError(Exception):
    pass


class MediaService:
    def __init__(self, enabled: bool | None = None, url: str | None = None) -> None:
        self.enabled = config.CATBOX_ENABLED if enabled is None else enabled
        self.url = url or config.CATBOX_API_URL

    async def upload_media(self, file_path: str | Path, userhash: str | None = None) -> str:
        """Upload a local file to Catbox and return its URL.

        Raises :class:`MediaError` when disabled or on failure.
        """
        if not self.enabled:
            raise MediaError("Media uploads are disabled (CATBOX_ENABLED=false).")
        path = Path(file_path)
        if not path.exists():
            raise MediaError(f"File not found: {path.name}")
        if path.stat().st_size > 200 * 1024 * 1024:
            raise MediaError("File is larger than Catbox's 200MB limit.")

        data = {"reqtype": "fileupload"}
        if userhash:
            data["userhash"] = userhash
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with path.open("rb") as fh:
                    resp = await client.post(
                        self.url, data=data, files={"fileToUpload": (path.name, fh)}
                    )
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("catbox upload failed: %s", exc)
            raise MediaError("Upload failed. Try again later.")
        text = resp.text.strip()
        if not text.startswith("http"):
            raise MediaError("Upload failed (unexpected Catbox response).")
        return text


media_service = MediaService()
