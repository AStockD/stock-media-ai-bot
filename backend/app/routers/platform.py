import base64
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.models.schemas import CookieImportRequest, CookieStatusResponse
from app.services.cookie_manager import CookieManager
from app.services.xueqiu_login_service import get_login_service
from app.services.xueqiu_post_service import get_post_service
from app.services.xueqiu_comment_service import get_comment_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/platform", tags=["platform"])

cookie_mgr = CookieManager()


@router.post("/cookie")
async def import_cookie(req: CookieImportRequest):
    count = cookie_mgr.import_cookies(req.cookie_string)
    return {"success": True, "cookie_count": count, "message": f"Imported {count} cookies"}


@router.get("/cookie/status", response_model=CookieStatusResponse)
async def cookie_status():
    if not cookie_mgr.has_cookies:
        return CookieStatusResponse(valid=False, cookie_count=0)
    return CookieStatusResponse(
        valid=True,
        last_updated=cookie_mgr.last_updated,
        cookie_count=cookie_mgr.cookie_count,
    )


@router.post("/xueqiu/login/start")
async def start_login():
    login_svc = get_login_service(cookie_mgr)
    result = await login_svc.start_login()

    if result.get("status") == "waiting_for_scan" and "screenshot" in result:
        screenshot_path = result["screenshot"]
        if Path(screenshot_path).exists():
            with open(screenshot_path, "rb") as f:
                img_base64 = base64.b64encode(f.read()).decode()
            return {
                "status": result["status"],
                "qr_image": f"data:image/png;base64,{img_base64}",
                "message": result.get("message", "")
            }

    return result


@router.get("/xueqiu/login/status")
async def login_status():
    login_svc = get_login_service(cookie_mgr)
    return await login_svc.get_status()


@router.post("/xueqiu/login/cancel")
async def cancel_login():
    login_svc = get_login_service(cookie_mgr)
    return await login_svc.cancel_login()


@router.post("/xueqiu/post")
async def create_post(req: dict):
    content = req.get("content")
    image_path = req.get("image_path")
    image_url = req.get("image_url")
    if not content:
        from fastapi import HTTPException
        raise HTTPException(400, "content is required")
    post_svc = get_post_service()
    return await post_svc.create_post(content, image_path=image_path, image_url=image_url)


@router.post("/xueqiu/comment")
async def create_comment(req: dict):
    post_id = req.get("post_id")
    content = req.get("content")
    user_id = req.get("user_id")
    reply_to_comment_id = req.get("reply_to_comment_id")
    if not post_id or not content:
        from fastapi import HTTPException
        raise HTTPException(400, "post_id and content are required")
    comment_svc = get_comment_service()
    return await comment_svc.create_comment(
        int(post_id), content,
        user_id=user_id,
        reply_to_comment_id=int(reply_to_comment_id) if reply_to_comment_id else None,
    )
