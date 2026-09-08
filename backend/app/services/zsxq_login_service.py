"""Knowledge Planet (zsxq) OAuth device-flow login via zsxq-cli."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

from app.services.account_manager import AccountManager
from app.services.zsxq_cli import ZsxqCliError, run_cli, unwrap_data

logger = logging.getLogger(__name__)

PLATFORM = "zsxq"


class ZsxqLoginService:
    def __init__(self, account_manager: AccountManager):
        self.am = account_manager
        self._sessions: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _get_session(self, user_id: int) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._sessions.get(user_id)

    def _set_session(self, user_id: int, session: dict[str, Any]):
        with self._lock:
            self._sessions[user_id] = session

    def _clear_session(self, user_id: int):
        with self._lock:
            self._sessions.pop(user_id, None)

    async def start_login(self, user_id: int, platform: str = PLATFORM) -> dict:
        try:
            result = run_cli(
                user_id,
                ["auth", "login", "--no-browser", "--no-wait"],
                timeout=60,
            )
        except ZsxqCliError as e:
            logger.error("zsxq login start failed: %s", e)
            return {"status": "error", "error": str(e)}

        data = unwrap_data(result) or {}
        device_code = data.get("device_code")
        user_code = data.get("user_code")
        uri = (
            data.get("verification_uri_complete")
            or data.get("verification_uri")
            or ""
        )
        if not device_code or not user_code:
            return {
                "status": "error",
                "error": f"未能解析设备码响应: {result}",
            }

        self._set_session(
            user_id,
            {
                "device_code": device_code,
                "user_code": user_code,
                "verification_uri": uri,
                "started_at": time.time(),
                "status": "waiting_for_auth",
                "error": None,
                "groups": [],
                "account_name": None,
            },
        )
        # Background poller completes OAuth and saves account
        threading.Thread(
            target=self._poll_until_done,
            args=(user_id, device_code),
            daemon=True,
            name=f"zsxq-login-{user_id}",
        ).start()

        return {
            "status": "waiting_for_auth",
            "verification_uri": uri,
            "user_code": user_code,
            "message": f"请打开链接完成授权，确认码：{user_code}",
        }

    def _poll_until_done(self, user_id: int, device_code: str):
        try:
            # Blocks until user authorizes or CLI times out
            run_cli(
                user_id,
                ["auth", "login", "--device-code", device_code, "--no-browser"],
                timeout=300,
            )
        except ZsxqCliError as e:
            logger.warning("zsxq device poll failed user=%s: %s", user_id, e)
            sess = self._get_session(user_id) or {}
            sess["status"] = "error"
            sess["error"] = str(e)
            self._set_session(user_id, sess)
            return

        try:
            status = run_cli(user_id, ["auth", "status"], timeout=30)
            status_data = unwrap_data(status) or {}
            account_name = (
                status_data.get("name")
                or status_data.get("user_name")
                or status_data.get("nickname")
                or (status_data.get("user") or {}).get("name")
            )
            if status_data.get("loggedIn") is False:
                sess = self._get_session(user_id) or {}
                sess["status"] = "error"
                sess["error"] = status_data.get("reason") or "授权未完成"
                self._set_session(user_id, sess)
                return

            groups = []
            try:
                glist = run_cli(user_id, ["group", "+list", "--limit", "50"], timeout=60)
                raw = unwrap_data(glist)
                if isinstance(raw, list):
                    groups = raw
                elif isinstance(raw, dict):
                    groups = raw.get("groups") or raw.get("items") or raw.get("list") or []
            except ZsxqCliError as e:
                logger.warning("zsxq group list after login: %s", e)

            normalized = []
            for g in groups:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("group_id") or g.get("id") or "")
                if not gid:
                    continue
                normalized.append(
                    {
                        "group_id": gid,
                        "name": g.get("name") or g.get("group_name") or gid,
                    }
                )

            creds = {
                "account_name": account_name,
                "groups": normalized,
            }
            # Preserve previously selected group if still present
            existing = self.am.get_credentials(user_id, PLATFORM) or {}
            if existing.get("group_id") and any(
                x["group_id"] == str(existing["group_id"]) for x in normalized
            ):
                creds["group_id"] = str(existing["group_id"])
                creds["group_name"] = existing.get("group_name")
            elif len(normalized) == 1:
                creds["group_id"] = normalized[0]["group_id"]
                creds["group_name"] = normalized[0]["name"]

            self.am.save_cookies(
                user_id=user_id,
                platform=PLATFORM,
                cookies={"cli": "1"},
                storage_state={"zsxq_cli": True},
                account_name=account_name or "zsxq",
                credentials=creds,
            )

            sess = self._get_session(user_id) or {}
            sess.update(
                {
                    "status": "success",
                    "groups": normalized,
                    "account_name": account_name,
                    "credentials": creds,
                }
            )
            self._set_session(user_id, sess)
            logger.info("zsxq login success user=%s groups=%d", user_id, len(normalized))
        except Exception as e:
            logger.exception("zsxq post-login setup failed user=%s", user_id)
            sess = self._get_session(user_id) or {}
            sess["status"] = "error"
            sess["error"] = str(e)
            self._set_session(user_id, sess)

    async def get_status(self, user_id: int, platform: str = PLATFORM) -> dict:
        sess = self._get_session(user_id)
        if not sess:
            acc = self.am.get_account(user_id, PLATFORM)
            if acc and acc.get("is_valid"):
                creds = self.am.get_credentials(user_id, PLATFORM) or {}
                return {
                    "status": "success",
                    "message": "已登录",
                    "cookie_count": 1,
                    "groups": creds.get("groups") or [],
                    "group_id": creds.get("group_id"),
                    "account_name": acc.get("account_name"),
                }
            return {"status": "idle", "message": "未开始登录"}

        status = sess.get("status", "waiting_for_auth")
        if status == "success":
            return {
                "status": "success",
                "message": "授权成功",
                "cookie_count": 1,
                "groups": sess.get("groups") or [],
                "group_id": (sess.get("credentials") or {}).get("group_id"),
                "account_name": sess.get("account_name"),
            }
        if status == "error":
            return {"status": "error", "error": sess.get("error") or "登录失败"}
        if time.time() - sess.get("started_at", 0) > 320:
            return {"status": "timeout", "error": "授权超时，请重试"}
        return {
            "status": "waiting_for_auth",
            "verification_uri": sess.get("verification_uri"),
            "user_code": sess.get("user_code"),
            "message": f"等待授权，确认码：{sess.get('user_code')}",
        }

    async def cancel_login(self, user_id: int, platform: str = PLATFORM) -> dict:
        self._clear_session(user_id)
        return {"status": "cancelled"}

    async def list_groups(self, user_id: int) -> dict:
        acc = self.am.get_account(user_id, PLATFORM)
        if not acc or not acc.get("is_valid"):
            return {"groups": [], "error": "请先授权知识星球"}
        try:
            glist = run_cli(user_id, ["group", "+list", "--limit", "50"], timeout=60)
            raw = unwrap_data(glist)
            groups = raw if isinstance(raw, list) else (raw or {}).get("groups") or []
            normalized = []
            for g in groups:
                if not isinstance(g, dict):
                    continue
                gid = str(g.get("group_id") or g.get("id") or "")
                if gid:
                    normalized.append(
                        {"group_id": gid, "name": g.get("name") or g.get("group_name") or gid}
                    )
            return {"groups": normalized}
        except ZsxqCliError as e:
            return {"groups": [], "error": str(e)}

    async def set_group(self, user_id: int, group_id: str, group_name: str = "") -> dict:
        acc = self.am.get_account(user_id, PLATFORM)
        if not acc or not acc.get("is_valid"):
            return {"success": False, "error": "请先授权知识星球"}
        creds = self.am.get_credentials(user_id, PLATFORM) or {}
        creds["group_id"] = str(group_id)
        if group_name:
            creds["group_name"] = group_name
        else:
            for g in creds.get("groups") or []:
                if str(g.get("group_id")) == str(group_id):
                    creds["group_name"] = g.get("name") or group_id
                    break
        self.am.save_cookies(
            user_id=user_id,
            platform=PLATFORM,
            cookies={"cli": "1"},
            storage_state={"zsxq_cli": True},
            account_name=acc.get("account_name") or creds.get("account_name") or "zsxq",
            credentials=creds,
        )
        return {
            "success": True,
            "group_id": creds["group_id"],
            "group_name": creds.get("group_name"),
        }


_login_service: Optional[ZsxqLoginService] = None


def get_zsxq_login_service(account_manager: AccountManager) -> ZsxqLoginService:
    global _login_service
    if _login_service is None:
        _login_service = ZsxqLoginService(account_manager)
    return _login_service
