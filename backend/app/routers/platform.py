import base64
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.services.account_manager import AccountManager, account_manager
from app.services.xueqiu_login_service import get_login_service
from app.services.xueqiu_post_service import XueqiuPostService
from app.services.xueqiu_comment_service import XueqiuCommentService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/platform", tags=["platform"])

post_service = XueqiuPostService(account_manager)
comment_service = XueqiuCommentService(account_manager)


@router.get("/accounts")
async def list_accounts(user: dict = Depends(get_current_user)):
    accounts = account_manager.get_accounts(user["id"])
    return {"accounts": accounts}


@router.post("/{platform}/login/start")
async def start_login(platform: str, user: dict = Depends(get_current_user)):
    login_svc = get_login_service(account_manager)
    result = await login_svc.start_login(user["id"], platform)

    if result.get("status") == "waiting_for_scan" and "screenshot" in result:
        screenshot_path = result["screenshot"]
        if Path(screenshot_path).exists():
            with open(screenshot_path, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode()
            return {
                "status": result["status"],
                "qr_image": f"data:image/png;base64,{img_base64}",
                "message": result.get("message", ""),
            }

    return result


@router.get("/{platform}/login/status")
async def login_status(platform: str, user: dict = Depends(get_current_user)):
    login_svc = get_login_service(account_manager)
    return await login_svc.get_status(user["id"], platform)


@router.post("/{platform}/login/cancel")
async def cancel_login(platform: str, user: dict = Depends(get_current_user)):
    login_svc = get_login_service(account_manager)
    return await login_svc.cancel_login(user["id"], platform)


@router.post("/{platform}/post")
async def create_post(platform: str, req: dict, user: dict = Depends(get_current_user)):
    content = req.get("content")
    image_path = req.get("image_path")
    image_url = req.get("image_url")
    if not content:
        raise HTTPException(400, "content is required")
    return await post_service.create_post(
        user_id=user["id"],
        content=content,
        image_path=image_path,
        image_url=image_url,
        platform=platform,
    )


@router.post("/{platform}/comment")
async def create_comment(platform: str, req: dict, user: dict = Depends(get_current_user)):
    post_id = req.get("post_id")
    content = req.get("content")
    reply_to_comment_id = req.get("reply_to_comment_id")
    if not post_id or not content:
        raise HTTPException(400, "post_id and content are required")
    return await comment_service.create_comment(
        user_id=user["id"],
        post_id=int(post_id),
        content=content,
        platform=platform,
        reply_to_comment_id=int(reply_to_comment_id) if reply_to_comment_id else None,
    )
