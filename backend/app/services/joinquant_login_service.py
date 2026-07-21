"""JoinQuant login service - password-based login via Playwright."""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from playwright.async_api import async_playwright, Browser

from app.config import JQ_LOGIN_URL, JQ_USERNAME, JQ_PASSWORD
from app.services.account_manager import AccountManager

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

    async def start_login(self, user_id: int, platform: str = "joinquant") -> dict:
        key = _session_key(user_id, platform)

        existing = _sessions.get(key)
        if existing:
            if existing.task and not existing.task.done():
                existing.task.cancel()
            await self._cleanup_session(existing)

        session = LoginSession(user_id=user_id, platform=platform)
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

            await page.fill("input[name=username]", JQ_USERNAME)
            await page.fill("input[name=pwd]", JQ_PASSWORD)
            logger.info("Filled credentials")

            await page.click("button.btnPwdSubmit")
            await page.wait_for_timeout(5000)

            if "login" in page.url.lower():
                await self._cleanup_session(session)
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


_login_services: Dict[int, JoinQuantLoginService] = {}


def get_joinquant_login_service(account_manager: AccountManager) -> JoinQuantLoginService:
    key = id(account_manager)
    if key not in _login_services:
        _login_services[key] = JoinQuantLoginService(account_manager)
    return _login_services[key]
