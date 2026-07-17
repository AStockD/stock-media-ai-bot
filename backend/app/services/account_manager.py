import json
import logging
from typing import Dict, List, Optional

from app.database import get_db

logger = logging.getLogger(__name__)


class AccountManager:
    def get_accounts(self, user_id: int) -> List[Dict]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT platform, account_name, cookies_json, is_valid, last_updated "
                    "FROM platform_accounts WHERE user_id = %s",
                    (user_id,),
                )
                rows = cur.fetchall()
        results = []
        for row in rows:
            cookie_count = 0
            if row["cookies_json"]:
                try:
                    cookie_count = len(json.loads(row["cookies_json"]))
                except Exception:
                    pass
            results.append({
                "platform": row["platform"],
                "account_name": row["account_name"],
                "is_valid": bool(row["is_valid"]),
                "last_updated": str(row["last_updated"]) if row["last_updated"] else None,
                "cookie_count": cookie_count,
            })
        return results

    def get_account(self, user_id: int, platform: str) -> Optional[dict]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, platform, account_name, cookies_json, storage_state_json, "
                    "is_valid, last_updated FROM platform_accounts "
                    "WHERE user_id = %s AND platform = %s",
                    (user_id, platform),
                )
                return cur.fetchone()

    def save_cookies(self, user_id: int, platform: str, cookies: dict,
                     storage_state: dict, account_name: Optional[str] = None):
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        storage_state_json = json.dumps(storage_state, ensure_ascii=False)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO platform_accounts "
                    "(user_id, platform, account_name, cookies_json, storage_state_json, is_valid) "
                    "VALUES (%s, %s, %s, %s, %s, TRUE) "
                    "ON DUPLICATE KEY UPDATE "
                    "cookies_json = VALUES(cookies_json), "
                    "storage_state_json = VALUES(storage_state_json), "
                    "is_valid = TRUE, "
                    "account_name = COALESCE(VALUES(account_name), account_name)",
                    (user_id, platform, account_name, cookies_json, storage_state_json),
                )
        logger.info(f"Saved cookies for user={user_id} platform={platform}")

    def get_cookie_string(self, user_id: int, platform: str) -> Optional[str]:
        account = self.get_account(user_id, platform)
        if not account or not account["cookies_json"]:
            return None
        try:
            cookies = json.loads(account["cookies_json"])
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
        except Exception:
            return None

    def get_storage_state_path(self, user_id: int, platform: str) -> Optional[str]:
        account = self.get_account(user_id, platform)
        if not account or not account["storage_state_json"]:
            return None
        import tempfile
        from pathlib import Path
        tmp = Path(tempfile.mkdtemp()) / f"storage_state_{user_id}_{platform}.json"
        tmp.write_text(account["storage_state_json"])
        return str(tmp)

    def invalidate(self, user_id: int, platform: str):
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE platform_accounts SET is_valid = FALSE "
                    "WHERE user_id = %s AND platform = %s",
                    (user_id, platform),
                )


account_manager = AccountManager()
