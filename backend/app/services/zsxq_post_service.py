"""Knowledge Planet (zsxq) topic create via zsxq-cli."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import POSTER_CACHE_DIR
from app.services.account_manager import AccountManager
from app.services.zsxq_cli import ZsxqCliError, run_cli, unwrap_data

logger = logging.getLogger(__name__)

PLATFORM = "zsxq"


class ZsxqPostService:
    def __init__(self, account_manager: AccountManager):
        self.am = account_manager

    def _require_group(self, user_id: int) -> tuple[Optional[str], Optional[str]]:
        acc = self.am.get_account(user_id, PLATFORM)
        if not acc or not acc.get("is_valid"):
            return None, "请先授权知识星球"
        creds = self.am.get_credentials(user_id, PLATFORM) or {}
        group_id = creds.get("group_id") or os.getenv("ZSXQ_DEFAULT_GROUP_ID", "").strip()
        if not group_id:
            return None, "请先在设置中选择默认星球"
        return str(group_id), None

    async def _resolve_image_path(
        self, image_path: Optional[str], image_url: Optional[str]
    ) -> Optional[str]:
        if image_path and Path(image_path).exists():
            return image_path
        if not image_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(image_url)
                resp.raise_for_status()
                suffix = Path(urlparse(image_url).path).suffix or ".png"
                if suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
                    suffix = ".png"
                POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=suffix,
                    dir=str(POSTER_CACHE_DIR),
                    prefix="zsxq_",
                )
                tmp.write(resp.content)
                tmp.close()
                return tmp.name
        except Exception as e:
            logger.error("Failed to download poster for zsxq: %s", e)
            return None

    async def create_post(
        self,
        user_id: int,
        content: str,
        image_path: Optional[str] = None,
        image_url: Optional[str] = None,
        title: Optional[str] = None,
        platform: str = PLATFORM,
    ) -> dict:
        group_id, err = self._require_group(user_id)
        if err:
            return {"success": False, "error": err}

        body = content.strip()
        if title:
            title = title.strip()
            if title and not body.startswith(title):
                body = f"{title}\n\n{body}"

        local_image = await self._resolve_image_path(image_path, image_url)
        args = [
            "topic",
            "+create",
            "--group-id",
            group_id,
            "--text",
            body,
            "--markdown",
            "--ai",
        ]
        if local_image:
            args.extend(["--files", local_image])

        try:
            result = run_cli(user_id, args, timeout=180)
            data = unwrap_data(result) or {}
            topic = data.get("topic") if isinstance(data, dict) else None
            topic_id = ""
            if isinstance(topic, dict):
                topic_id = str(topic.get("topic_id") or topic.get("id") or "")
            elif isinstance(data, dict):
                topic_id = str(data.get("topic_id") or "")
            return {
                "success": True,
                "post_id": topic_id or None,
                "message": "知识星球发帖成功",
                "url": f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}"
                if topic_id
                else None,
            }
        except ZsxqCliError as e:
            logger.error("zsxq create_post failed: %s", e)
            if e.exit_code in (401, 11) or "401" in str(e) or "not logged" in str(e).lower():
                self.am.invalidate(user_id, PLATFORM)
                return {"success": False, "error": "知识星球登录已失效，请重新授权"}
            return {"success": False, "error": str(e)}
