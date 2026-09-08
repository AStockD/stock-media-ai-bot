"""Knowledge Planet (zsxq) comment + post list via zsxq-cli."""
from __future__ import annotations

import logging
from typing import Optional

from app.services.account_manager import AccountManager
from app.services.zsxq_cli import ZsxqCliError, run_cli, unwrap_data

logger = logging.getLogger(__name__)

PLATFORM = "zsxq"


class ZsxqCommentService:
    def __init__(self, account_manager: AccountManager):
        self.am = account_manager

    async def create_comment(
        self,
        user_id: int,
        content: str,
        post_id: Optional[str] = None,
        post_url: Optional[str] = None,
        platform: str = PLATFORM,
        reply_to_comment_id: Optional[str] = None,
        post_title: Optional[str] = None,
    ) -> dict:
        acc = self.am.get_account(user_id, PLATFORM)
        if not acc or not acc.get("is_valid"):
            return {"success": False, "error": "请先授权知识星球"}

        topic_id = str(post_id or "").strip()
        if not topic_id and post_url:
            # https://wx.zsxq.com/.../topic_detail/123  or trailing digits
            parts = post_url.rstrip("/").split("/")
            for part in reversed(parts):
                clean = part.split("?")[0]
                if clean.isdigit():
                    topic_id = clean
                    break
        if not topic_id:
            return {"success": False, "error": "需要 topic_id（post_id）"}

        args = ["topic", "+reply", "--topic-id", topic_id, "--text", content.strip()]
        if reply_to_comment_id:
            args.extend(["--reply-to", str(reply_to_comment_id)])

        try:
            run_cli(user_id, args, timeout=120)
            return {"success": True, "message": "评论成功"}
        except ZsxqCliError as e:
            logger.error("zsxq comment failed: %s", e)
            if "401" in str(e) or "not logged" in str(e).lower():
                self.am.invalidate(user_id, PLATFORM)
                return {"success": False, "error": "知识星球登录已失效，请重新授权"}
            return {"success": False, "error": str(e)}

    async def list_posts(self, user_id: int, limit: int = 20) -> dict:
        acc = self.am.get_account(user_id, PLATFORM)
        if not acc or not acc.get("is_valid"):
            return {"posts": [], "error": "请先授权知识星球"}

        try:
            result = run_cli(
                user_id,
                ["user", "+footprints", "--limit", str(max(1, min(limit, 50)))],
                timeout=90,
            )
            raw = unwrap_data(result)
            items = []
            if isinstance(raw, list):
                items = raw
            elif isinstance(raw, dict):
                items = (
                    raw.get("topics")
                    or raw.get("footprints")
                    or raw.get("items")
                    or raw.get("list")
                    or []
                )

            posts = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                topic = item.get("topic") if isinstance(item.get("topic"), dict) else item
                topic_id = str(
                    topic.get("topic_id")
                    or topic.get("id")
                    or item.get("topic_id")
                    or ""
                )
                if not topic_id:
                    continue
                title = (
                    topic.get("title")
                    or topic.get("text")
                    or item.get("title")
                    or ""
                )
                if isinstance(title, str) and len(title) > 120:
                    title = title[:120]
                created = (
                    topic.get("create_time")
                    or topic.get("created_at")
                    or item.get("create_time")
                    or ""
                )
                posts.append(
                    {
                        "post_id": topic_id,
                        "title": title or f"主题 {topic_id}",
                        "url": f"https://wx.zsxq.com/dweb2/index/topic_detail/{topic_id}",
                        "created_at": created,
                    }
                )
            return {"posts": posts}
        except ZsxqCliError as e:
            logger.error("zsxq list_posts failed: %s", e)
            if "401" in str(e) or "not logged" in str(e).lower():
                self.am.invalidate(user_id, PLATFORM)
                return {"posts": [], "error": "知识星球登录已失效，请重新授权"}
            return {"posts": [], "error": str(e)}
