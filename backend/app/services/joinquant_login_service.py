"""JoinQuant login service - password-based login via Playwright."""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from playwright.async_api import async_playwright, Browser

from app.config import JQ_LOGIN_URL
from app.services.account_manager import AccountManager
from app.services.jq_captcha_service import JoinQuantCaptchaService

logger = logging.getLogger(__name__)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = { runtime: {} };
"""

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


@dataclass
class LoginSession:
    user_id: int
    platform: str
    browser: Optional[Browser] = None
    task: Optional[asyncio.Task] = None
    pw_instance: object = None
    last_result: Optional[dict] = field(default=None)
    page: object = None
    context: object = None
    captcha_data: Optional[dict] = field(default=None)
    username: str = ""
    password: str = ""


_sessions: Dict[str, LoginSession] = {}


def _session_key(user_id: int, platform: str) -> str:
    return f"{user_id}:{platform}"


class JoinQuantLoginService:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager

    async def _cleanup_session(self, session: LoginSession):
        try:
            if session.browser and session.browser.is_connected():
                await session.browser.close()
        except Exception:
            pass
        try:
            if session.pw_instance:
                await session.pw_instance.stop()
        except Exception:
            pass
        session.browser = None
        session.pw_instance = None

    async def start_login(self, user_id: int, platform: str = "joinquant", username: str = "", password: str = "") -> dict:
        if not username or not password:
            return {"status": "error", "error": "用户名和密码不能为空"}

        key = _session_key(user_id, platform)

        existing = _sessions.get(key)
        if existing:
            if existing.task and not existing.task.done():
                existing.task.cancel()
            await self._cleanup_session(existing)

        session = LoginSession(user_id=user_id, platform=platform,
                               username=username, password=password)
        _sessions[key] = session

        try:
            session.pw_instance = await async_playwright().start()
            session.browser = await session.pw_instance.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
            )
            context = await session.browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=USER_AGENT,
            )
            await context.add_init_script(STEALTH_SCRIPT)
            page = await context.new_page()

            logger.info(f"Navigating to JoinQuant login for user={user_id}")
            await page.goto(JQ_LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            checkbox = await page.query_selector("input[type=checkbox]")
            if checkbox and not await checkbox.is_checked():
                await checkbox.check(force=True)
                logger.info("Checked agreement checkbox")

            await page.fill("input[name=username]", username)
            await page.fill("input[name=pwd]", password)
            logger.info("Filled credentials")

            captcha_data_holder = {}
            async def on_captcha_response(response):
                if "verifyCode/captchar" in response.url and response.request.method == "POST":
                    try:
                        body = await response.json()
                        if body.get("code") == "00000":
                            data = body.get("data", {})
                            captcha_data_holder["data"] = data
                            logger.info(f"Login CAPTCHA data captured: bgImgW={data.get('bgImgW')}, bgImgH={data.get('bgImgH')}, blockW={data.get('blockW')}, blockH={data.get('blockH')}, point={data.get('point')}, axisY={data.get('axisY')}")
                            logger.info(f"CAPTCHA data keys: {list(data.keys())}")
                    except Exception as e:
                        logger.error(f"Failed to parse login CAPTCHA response: {e}")

            page.on("response", on_captcha_response)

            await page.click("button.btnPwdSubmit")
            await page.wait_for_timeout(3000)

            captcha_visible = await page.evaluate("""() => {
                const modal = document.querySelector('.validCode-dialog, [class*="validCode"]');
                if (!modal) return false;
                const display = window.getComputedStyle(modal).display;
                return display !== 'none';
            }""")

            if captcha_visible:
                logger.info("Login CAPTCHA detected, taking screenshots")
                captcha_data = captcha_data_holder.get("data")
                if not captcha_data:
                    await self._cleanup_session(session)
                    return {"status": "error", "error": "验证码数据获取失败"}
                
                session.page = page
                session.context = context
                session.captcha_data = captcha_data

                bg_screenshot, piece_screenshot = await JoinQuantCaptchaService.take_screenshots(page)

                bg_img_w = captcha_data.get("bgImgW", 363)
                bg_img_h = captcha_data.get("bgImgH", 142)

                return {
                    "status": "captcha_required",
                    "captcha_data": {
                        "bgImg": bg_screenshot or captcha_data.get("bgImg", ""),
                        "hqImg": captcha_data.get("hqImg", ""),
                        "bgImgW": bg_img_w,
                        "bgImgH": bg_img_h,
                        "blockW": captcha_data.get("blockW", 11),
                        "blockH": captcha_data.get("blockH", 71),
                        "point": captcha_data.get("point", []),
                        "axisY": captcha_data.get("axisY", 0),
                    }
                }
            else:
                await page.wait_for_timeout(2000)

            page.remove_listener("response", on_captcha_response)

            if "login" in page.url.lower():
                error_text = await page.evaluate("""() => {
                    const el = document.querySelector('.error-msg, .tip-msg, [class*="error"], [class*="alert"]');
                    return el ? el.textContent.trim() : '';
                }""")
                await self._cleanup_session(session)
                if error_text:
                    return {"status": "error", "error": f"登录失败: {error_text}"}
                return {"status": "error", "error": "登录失败，请检查账号密码"}

            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            storage_state = await context.storage_state()

            account_name = None
            try:
                user_link = await page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="/user/"]');
                    for (const a of links) {
                        const text = a.textContent.trim();
                        if (text && text.length > 0 && text.length < 50 &&
                            !['首页', '消息', '积分中心', '账号设置', '退出'].includes(text)) {
                            return text;
                        }
                    }
                    return null;
                }""")
                account_name = user_link
            except Exception as e:
                logger.warning(f"Failed to extract account name: {e}")

            self.account_manager.save_cookies(
                user_id=user_id,
                platform=platform,
                cookies=cookies_dict,
                storage_state=storage_state,
                account_name=account_name,
                credentials={"username": username, "password": password},
            )

            await self._cleanup_session(session)
            session.last_result = {"status": "success", "cookie_count": len(cookies_dict)}
            return session.last_result

        except Exception as e:
            logger.error(f"JoinQuant login failed for user={user_id}: {e}", exc_info=True)
            await self._cleanup_session(session)
            return {"status": "error", "error": str(e)}

    async def get_status(self, user_id: int, platform: str = "joinquant") -> dict:
        key = _session_key(user_id, platform)
        session = _sessions.get(key)
        if not session:
            return {"status": "idle"}
        if session.last_result:
            return session.last_result
        return {"status": "logging_in"}

    async def cancel_login(self, user_id: int, platform: str = "joinquant") -> dict:
        key = _session_key(user_id, platform)
        session = _sessions.pop(key, None)
        if not session:
            return {"status": "cancelled"}
        if session.task and not session.task.done():
            session.task.cancel()
        await self._cleanup_session(session)
        return {"status": "cancelled"}

    async def validate_captcha(self, user_id: int, platform: str, axis_x: int) -> dict:
        """Validate CAPTCHA with user-provided axisX and continue login."""
        key = _session_key(user_id, platform)
        session = _sessions.get(key)
        if not session or not session.page:
            return {"status": "error", "error": "没有待验证的登录会话"}

        page = session.page
        context = session.context

        try:
            expected_width = session.captcha_data.get("bgImgW", 363) if session.captcha_data else 363

            drag_result = await JoinQuantCaptchaService.simulate_drag(page, axis_x, expected_width)

            if not drag_result["success"]:
                body = drag_result.get("body", {})
                data = body.get("data", {})
                message = data.get("message", "验证码验证错误")
                logger.warning(f"CAPTCHA validation failed: {message}")

                action = data.get("action", "")
                if action == "renew":
                    async def refresh_fn():
                        refresh = await page.query_selector('.valid-code__refresh, [class*="refresh"]')
                        if refresh:
                            await refresh.click()

                    new_captcha = await JoinQuantCaptchaService.refresh(page, refresh_fn, {
                        "bgImgW": 363, "bgImgH": 142, "blockW": 11, "blockH": 71,
                    })
                    if new_captcha:
                        session.captcha_data = new_captcha
                        return {
                            "status": "captcha_required",
                            "message": message,
                            "captcha_data": new_captcha,
                        }

                return {"status": "error", "error": f"验证码错误: {message}"}

            logger.info("CAPTCHA validated successfully, waiting for login to complete...")
            await page.wait_for_timeout(5000)

            if "login" in page.url.lower():
                error_text = await page.evaluate("""() => {
                    const el = document.querySelector('.error-msg, .tip-msg, [class*="error"], [class*="alert"]');
                    return el ? el.textContent.trim() : '';
                }""")
                await self._cleanup_session(session)
                _sessions.pop(key, None)
                if error_text:
                    return {"status": "error", "error": f"登录失败: {error_text}"}
                return {"status": "error", "error": "登录失败，请检查账号密码"}

            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            storage_state = await context.storage_state()

            account_name = None
            try:
                user_link = await page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="/user/"]');
                    for (const a of links) {
                        const text = a.textContent.trim();
                        if (text and text.length > 0 and text.length < 50 and
                            !['首页', '消息', '积分中心', '账号设置', '退出'].includes(text)) {
                            return text;
                        }
                    }
                    return null;
                }""")
                account_name = user_link
            except Exception as e:
                logger.warning(f"Failed to extract account name: {e}")

            self.account_manager.save_cookies(
                user_id=user_id,
                platform=platform,
                cookies=cookies_dict,
                storage_state=storage_state,
                account_name=account_name,
                credentials={"username": session.username, "password": session.password},
            )

            await self._cleanup_session(session)
            _sessions.pop(key, None)
            return {"status": "success", "cookie_count": len(cookies_dict)}

        except Exception as e:
            logger.error(f"CAPTCHA validation failed: {e}", exc_info=True)
            await self._cleanup_session(session)
            _sessions.pop(key, None)
            return {"status": "error", "error": str(e)}


_login_services: Dict[int, JoinQuantLoginService] = {}


def get_joinquant_login_service(account_manager: AccountManager) -> JoinQuantLoginService:
    key = id(account_manager)
    if key not in _login_services:
        _login_services[key] = JoinQuantLoginService(account_manager)
    return _login_services[key]
