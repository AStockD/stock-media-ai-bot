import base64
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_current_user
from app.services.account_manager import AccountManager, account_manager
from app.services.xueqiu_login_service import get_login_service
from app.services.xueqiu_post_service import XueqiuPostService
from app.services.xueqiu_comment_service import XueqiuCommentService
from app.services.joinquant_login_service import get_joinquant_login_service
from app.services.joinquant_comment_service import JoinQuantCommentService
from app.services.joinquant_post_service import JoinQuantPostService, set_captcha_state, get_captcha_state

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/platform", tags=["platform"])

post_service = XueqiuPostService(account_manager)
comment_service = XueqiuCommentService(account_manager)
jq_comment_service = JoinQuantCommentService(account_manager)
jq_post_service = JoinQuantPostService(account_manager)

@router.get("/joinquant/captcha-status")
async def get_captcha_status(user: dict = Depends(get_current_user)):
    status = get_captcha_state(user["id"])
    return {"status": status}

@router.post("/joinquant/captcha-solve")
async def submit_captcha_solution(data: dict, user: dict = Depends(get_current_user)):
    axis_x = data.get("axisX")
    if axis_x is None:
        raise HTTPException(400, "axisX required")
    
    state = get_captcha_state(user["id"])
    if not state or state.get("status") != "waiting":
        raise HTTPException(400, "No CAPTCHA waiting")
    
    state["axisX"] = axis_x
    state["status"] = "solved"
    return {"status": "ok"}

@router.post("/joinquant/login/captcha-validate")
async def login_captcha_validate(data: dict, user: dict = Depends(get_current_user)):
    axis_x = data.get("axisX")
    if axis_x is None:
        raise HTTPException(400, "axisX required")
    
    jq_login_svc = get_joinquant_login_service(account_manager)
    return await jq_login_svc.validate_captcha(user["id"], "joinquant", int(axis_x))

_DATA_DIR = Path("/app/data")
_POSTS_CACHE_FILE = _DATA_DIR / "posts_cache.json"
_POSTS_CACHE_TTL = 6 * 3600


def _load_posts_cache() -> dict:
    if _POSTS_CACHE_FILE.exists():
        try:
            return json.loads(_POSTS_CACHE_FILE.read_text())
        except Exception as e:
            logger.warning(f"Failed to load posts cache: {e}")
    return {}


def _save_posts_cache(data: dict):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _POSTS_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False))


@router.get("/accounts")
async def list_accounts(user: dict = Depends(get_current_user)):
    accounts = account_manager.get_accounts(user["id"])
    return {"accounts": accounts}


@router.post("/{platform}/login/start")
async def start_login(platform: str, req: dict = {}, user: dict = Depends(get_current_user)):
    if platform == "joinquant":
        jq_login_svc = get_joinquant_login_service(account_manager)
        username = req.get("username", "")
        password = req.get("password", "")
        return await jq_login_svc.start_login(user["id"], platform, username=username, password=password)

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
    if platform == "joinquant":
        jq_login_svc = get_joinquant_login_service(account_manager)
        return await jq_login_svc.get_status(user["id"], platform)
    login_svc = get_login_service(account_manager)
    return await login_svc.get_status(user["id"], platform)


@router.post("/{platform}/login/cancel")
async def cancel_login(platform: str, user: dict = Depends(get_current_user)):
    if platform == "joinquant":
        jq_login_svc = get_joinquant_login_service(account_manager)
        return await jq_login_svc.cancel_login(user["id"], platform)
    login_svc = get_login_service(account_manager)
    return await login_svc.cancel_login(user["id"], platform)


@router.post("/{platform}/post")
async def create_post(platform: str, req: dict, user: dict = Depends(get_current_user)):
    content = req.get("content")
    image_path = req.get("image_path")
    image_url = req.get("image_url")
    title = req.get("title")
    if not content:
        raise HTTPException(400, "content is required")
    if platform == "joinquant":
        return await jq_post_service.create_post(
            user_id=user["id"],
            content=content,
            title=title,
            platform=platform,
        )
    return await post_service.create_post(
        user_id=user["id"],
        content=content,
        image_path=image_path,
        image_url=image_url,
        platform=platform,
    )


async def _fetch_xueqiu_posts(user_id: int) -> dict:
    storage_state_path = account_manager.get_storage_state_path(user_id, "xueqiu")
    if not storage_state_path:
        return {"posts": [], "error": "未登录，请先扫码登录"}

    from playwright.async_api import async_playwright

    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
        )
        context = await browser.new_context(
            storage_state=storage_state_path,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        login_btn = await page.query_selector("text=立即登录/注册")
        if login_btn and await login_btn.is_visible():
            return {"posts": [], "error": "登录已过期，请重新扫码登录"}

        profile_link = await page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/u/"]');
            for (const link of links) {
                const match = link.href.match(/\\/u\\/(\\d+)/);
                if (match) return match[1];
            }
            return null;
        }""")

        if not profile_link:
            return {"posts": [], "error": "未找到用户ID"}

        api_url = f"https://xueqiu.com/v4/statuses/user_timeline.json?user_id={profile_link}&page=1&count=20"
        resp_text = await page.evaluate("""async (url) => {
            const r = await fetch(url);
            return await r.text();
        }""", api_url)

        data = json.loads(resp_text)
        statuses = data.get("statuses", [])

        posts = []
        for s in statuses:
            post_id = str(s.get("id", ""))
            title = s.get("title") or ""
            desc = s.get("description") or ""
            text = s.get("text") or ""
            if not title:
                source = text if text else desc
                title = source[:100].replace("\n", " ")
            if title:
                import re
                title = re.sub(r'<[^>]+>', '', title)
                title = re.sub(r'^[#*\s]+', '', title).strip()
            target = s.get("target") or ""
            url = f"https://xueqiu.com{target}" if target else ""
            created_at = s.get("created_at", 0)
            created_at_str = ""
            if created_at:
                dt = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                created_at_str = dt.isoformat()

            posts.append({
                "post_id": post_id,
                "title": title,
                "url": url,
                "created_at": created_at_str,
            })

        return {"posts": posts}

    except Exception as e:
        logger.error(f"Failed to fetch posts: {e}", exc_info=True)
        return {"posts": [], "error": str(e)}
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        if pw:
            try:
                await pw.stop()
            except Exception:
                pass
        if storage_state_path:
            try:
                Path(storage_state_path).unlink(missing_ok=True)
                Path(storage_state_path).parent.rmdir()
            except Exception:
                pass


@router.get("/{platform}/posts")
async def list_posts(
    platform: str,
    refresh: int = Query(0, description="Set to 1 to force refresh"),
    user: dict = Depends(get_current_user),
):
    if platform != "xueqiu":
        raise HTTPException(400, "Only xueqiu platform is supported")

    uid = user["id"]
    now = time.time()
    cache = _load_posts_cache()

    if not refresh and str(uid) in cache:
        cached = cache[str(uid)]
        if now - cached["ts"] < _POSTS_CACHE_TTL:
            return {**cached["data"], "cached_at": datetime.fromtimestamp(cached["ts"], tz=timezone.utc).isoformat()}

    data = await _fetch_xueqiu_posts(uid)
    if "error" not in data:
        cache[str(uid)] = {"data": data, "ts": now}
        _save_posts_cache(cache)

    return {**data, "cached_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat()}


@router.post("/{platform}/comment")
async def create_comment(platform: str, req: dict, user: dict = Depends(get_current_user)):
    post_id = req.get("post_id")
    post_url = req.get("post_url")
    content = req.get("content")
    post_title = req.get("post_title")
    reply_to_comment_id = req.get("reply_to_comment_id")

    if platform == "joinquant":
        if not content:
            raise HTTPException(400, "content is required")
        if not post_url and not post_id:
            raise HTTPException(400, "post_url or post_id is required")
        return await jq_comment_service.create_comment(
            user_id=user["id"],
            post_url=post_url or "",
            content=content,
            platform=platform,
            post_id=str(post_id) if post_id else None,
            post_title=post_title,
        )

    if not content:
        raise HTTPException(400, "content is required")
    if not post_id and not post_url:
        raise HTTPException(400, "post_id or post_url is required")
    if not post_id and post_url:
        import re
        parts = post_url.rstrip("/").split("/")
        for part in reversed(parts):
            clean = part.split("?")[0]
            if clean.isdigit():
                post_id = int(clean)
                break
    return await comment_service.create_comment(
        user_id=user["id"],
        post_id=int(post_id) if post_id else None,
        post_url=post_url,
        content=content,
        platform=platform,
        reply_to_comment_id=int(reply_to_comment_id) if reply_to_comment_id else None,
        post_title=post_title,
    )
