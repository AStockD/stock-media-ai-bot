import json
import logging
from datetime import datetime
from pathlib import Path

from app.config import COOKIE_FILE

logger = logging.getLogger(__name__)


class CookieManager:
    def __init__(self):
        self._cookies: dict[str, str] = {}
        self._full_cookies: list[dict] = []
        self._last_updated: str | None = None
        self._load()

    def _load(self):
        if COOKIE_FILE.exists():
            try:
                data = json.loads(COOKIE_FILE.read_text())
                self._cookies = data.get("cookies", {})
                self._full_cookies = data.get("full_cookies", [])
                self._last_updated = data.get("last_updated")
                logger.info(f"Loaded {len(self._cookies)} cookies from disk")
            except Exception as e:
                logger.warning(f"Failed to load cookies: {e}")

    def _save(self):
        COOKIE_FILE.write_text(json.dumps({
            "cookies": self._cookies,
            "full_cookies": self._full_cookies,
            "last_updated": self._last_updated,
        }, ensure_ascii=False, indent=2))

    def import_cookies(self, cookie_string: str) -> int:
        pairs = []
        for part in cookie_string.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                pairs.append((k.strip(), v.strip()))
        self._cookies = dict(pairs)
        self._full_cookies = []
        self._last_updated = datetime.now().isoformat()
        self._save()
        logger.info(f"Imported {len(self._cookies)} cookies")
        return len(self._cookies)

    def import_cookie_dict(self, cookies: list[dict]) -> int:
        self._cookies = {c["name"]: c["value"] for c in cookies}
        self._full_cookies = cookies
        self._last_updated = datetime.now().isoformat()
        self._save()
        logger.info(f"Imported {len(self._cookies)} cookies from dict")
        return len(self._cookies)

    def get_cookie_string(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    def get_cookies_dict(self) -> dict[str, str]:
        return dict(self._cookies)

    def get_full_cookies(self) -> list[dict]:
        return list(self._full_cookies)

    @property
    def cookie_count(self) -> int:
        return len(self._cookies)

    @property
    def last_updated(self) -> str | None:
        return self._last_updated

    @property
    def has_cookies(self) -> bool:
        return len(self._cookies) > 0
