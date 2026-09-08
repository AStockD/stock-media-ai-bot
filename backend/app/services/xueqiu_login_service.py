"""Playwright-based Xueqiu login service with QR code scanning. Supports concurrent per-user sessions."""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from playwright.async_api import async_playwright, Browser, Page

from app.services.account_manager import AccountManager

logger = logging.getLogger(__name__)


@dataclass
class LoginSession:
    user_id: int
    platform: str
    browser: Optional[Browser] = None
    page: Optional[Page] = None
    task: Optional[asyncio.Task] = None
    pw_instance: object = None
    screenshot_path: str = ""
    last_result: Optional[dict] = field(default=None)


_sessions: Dict[str, LoginSession] = {}


def _session_key(user_id: int, platform: str) -> str:
    return f"{user_id}:{platform}"


class XueqiuLoginService:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager

    async def _cleanup_session(self, session: LoginSession):
        try:
            if session.page and not session.page.is_closed():
                await session.page.close()
        except Exception:
            pass
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
        session.page = None
        session.pw_instance = None

    async def start_login(self, user_id: int, platform: str = "xueqiu") -> dict:
        key = _session_key(user_id, platform)

        existing = _sessions.get(key)
        if existing:
            if existing.task and not existing.task.done():
                existing.task.cancel()
            await self._cleanup_session(existing)

        session = LoginSession(
            user_id=user_id,
            platform=platform,
            screenshot_path=f"/tmp/xueqiu_qr_{user_id}.png",
        )
        _sessions[key] = session
        session.last_result = None

        try:
            logger.info(f"Launching headless Chromium for user={user_id} platform={platform}")
            session.pw_instance = await async_playwright().start()
            session.browser = await session.pw_instance.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
            )
            context = await session.browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            session.page = await context.new_page()

            await session.page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)

            for attempt in range(10):
                modal = await session.page.query_selector('[class*="newLogin_modal"]')
                if modal and await modal.is_visible():
                    logger.info(f"Login modal found after {attempt + 1}s")
                    break
                await session.page.wait_for_timeout(1000)
            else:
                await session.page.screenshot(path=session.screenshot_path)
                return {"status": "error", "error": "登录弹窗未出现，请检查页面加载"}

            qr_tab = None
            for attempt in range(5):
                qr_tab = await session.page.query_selector("text=二维码登录")
                if qr_tab:
                    break
                await session.page.wait_for_timeout(500)

            if qr_tab:
                await qr_tab.evaluate("e => e.click()")
                await session.page.wait_for_timeout(2000)
            else:
                await session.page.screenshot(path=session.screenshot_path)
                session.task = asyncio.create_task(self._monitor_login(session))
                return {
                    "status": "waiting_for_scan",
                    "screenshot": session.screenshot_path,
                    "message": "未找到二维码登录选项，请查看截图",
                }

            qr_canvas = await session.page.query_selector("canvas")
            if qr_canvas:
                await qr_canvas.screenshot(path=session.screenshot_path)
            else:
                qr_wrapper = await session.page.query_selector('[class*="qrcode__wrapper"]')
                if qr_wrapper:
                    await qr_wrapper.screenshot(path=session.screenshot_path)
                else:
                    await session.page.screenshot(path=session.screenshot_path)

            session.task = asyncio.create_task(self._monitor_login(session))
            return {
                "status": "waiting_for_scan",
                "screenshot": session.screenshot_path,
                "message": "请使用雪球APP扫描二维码登录",
            }
        except Exception as e:
            logger.error(f"Failed to start login for user={user_id}: {e}", exc_info=True)
            await self._cleanup_session(session)
            return {"status": "error", "error": str(e)}

    async def _monitor_login(self, session: LoginSession):
        try:
            max_wait = 120
            modal_was_visible = False
            last_reload_check = 0

            for i in range(max_wait):
                try:
                    if not session.page or session.page.is_closed():
                        session.last_result = {"status": "error", "error": "Browser page closed"}
                        return
                    await asyncio.sleep(1)
                    if not session.page or session.page.is_closed():
                        continue
                except Exception as e:
                    session.last_result = {"status": "error", "error": f"Browser error: {e}"}
                    return

                try:
                    modal = await session.page.query_selector('[class*="newLogin_modal"]')
                    if modal:
                        is_visible = await modal.is_visible()
                        if is_visible:
                            modal_was_visible = True
                        elif modal_was_visible and i > 3:
                            logger.info(f"Login modal hidden (visible->hidden) for user={session.user_id} at {i}s")
                            await self._save_login(session)
                            return
                    elif modal_was_visible and i > 3:
                        logger.info(f"Login modal disappeared (element removed) for user={session.user_id} at {i}s")
                        await self._save_login(session)
                        return
                except Exception as e:
                    logger.debug(f"Modal check error at {i}s: {e}")

                if i > 5 and i - last_reload_check >= 15:
                    last_reload_check = i
                    try:
                        await session.page.reload(wait_until="domcontentloaded", timeout=15000)
                        await session.page.wait_for_timeout(3000)

                        logged_in = await self._check_logged_in(session)
                        if logged_in:
                            logger.info(f"Login detected after reload for user={session.user_id} at {i}s")
                            await self._save_login(session)
                            return

                        modal_after_reload = await session.page.query_selector('[class*="newLogin_modal"]')
                        if modal_after_reload:
                            is_vis = await modal_after_reload.is_visible()
                            if not is_vis and modal_was_visible:
                                logger.info(f"Login modal not visible after reload for user={session.user_id} at {i}s")
                                await self._save_login(session)
                                return
                        elif modal_was_visible:
                            logger.info(f"Login modal absent after reload for user={session.user_id} at {i}s")
                            await self._save_login(session)
                            return

                        logger.debug(f"Reload check at {i}s: still not logged in, URL={session.page.url}")
                    except Exception as e:
                        logger.debug(f"Reload check error at {i}s: {e}")

                if i % 10 == 0:
                    logger.info(f"Waiting for login... user={session.user_id} ({i}s)")

            logger.warning(f"Login timeout for user={session.user_id}")
            await self._cleanup_session(session)
            session.last_result = {"status": "timeout", "message": "登录超时，请重试"}

        except asyncio.CancelledError:
            session.last_result = {"status": "cancelled"}
        except Exception as e:
            logger.error(f"Login monitoring error: {e}", exc_info=True)
            try:
                await self._cleanup_session(session)
            except Exception:
                pass
            session.last_result = {"status": "error", "error": str(e)}

    async def _check_logged_in(self, session: LoginSession) -> bool:
        try:
            cookies = await session.page.context.cookies()
            cookie_names = {c["name"] for c in cookies}
            login_tokens = {"u", "xq_a_token", "xq_at", "xq_id_token"}
            if login_tokens & cookie_names:
                logger.info(f"Login cookie found: {login_tokens & cookie_names}")
                return True
        except Exception:
            pass

        try:
            has_user_link = await session.page.evaluate("""() => {
                const links = document.querySelectorAll('a.user-name, a[href*="/u/"]');
                for (const a of links) {
                    const text = a.textContent.trim();
                    if (text && text.length > 0 && text.length < 50) return true;
                }
                return false;
            }""")
            if has_user_link:
                logger.info("User link found in page DOM")
                return True
        except Exception:
            pass

        return False

    async def _save_login(self, session: LoginSession):
        logger.info(f"Login successful for user={session.user_id}")
        context = session.page.context
        cookies_list = await context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies_list}

        storage_state = await context.storage_state()

        account_name = None
        try:
            account_name = await session.page.evaluate("""() => {
                const links = document.querySelectorAll('a.user-name');
                for (const a of links) {
                    const text = a.textContent.trim();
                    if (text && text.length > 0 && text.length < 50) {
                        return text;
                    }
                }
                const allLinks = document.querySelectorAll('a[href*="/u/"]');
                for (const a of allLinks) {
                    const text = a.textContent.trim();
                    if (text && text.length > 0 && text.length < 50) {
                        return text;
                    }
                }
                return null;
            }""")
            if account_name:
                logger.info(f"Extracted account_name: {account_name}")
        except Exception as e:
            logger.warning(f"Failed to extract account_name: {e}")

        self.account_manager.save_cookies(
            user_id=session.user_id,
            platform=session.platform,
            cookies=cookies_dict,
            storage_state=storage_state,
            account_name=account_name,
        )

        await self._cleanup_session(session)
        session.last_result = {"status": "success", "cookie_count": len(cookies_dict)}

    async def get_status(self, user_id: int, platform: str = "xueqiu") -> dict:
        key = _session_key(user_id, platform)
        session = _sessions.get(key)
        if not session:
            return {"status": "idle"}
        try:
            if session.last_result and session.last_result.get("status") in ("success", "timeout", "error"):
                return session.last_result
            if session.task and session.task.done():
                try:
                    result = session.task.result()
                    if result:
                        return result
                except Exception:
                    pass
            if not session.browser or not session.browser.is_connected():
                if session.last_result:
                    return session.last_result
                return {"status": "idle"}
            return {"status": "waiting_for_scan"}
        except Exception:
            return {"status": "idle"}

    async def cancel_login(self, user_id: int, platform: str = "xueqiu") -> dict:
        key = _session_key(user_id, platform)
        session = _sessions.get(key)
        if not session:
            return {"status": "cancelled"}
        if session.task and not session.task.done():
            session.task.cancel()
        session.last_result = None
        await self._cleanup_session(session)
        del _sessions[key]
        return {"status": "cancelled"}


_login_services: Dict[int, XueqiuLoginService] = {}


def get_login_service(account_manager: AccountManager) -> XueqiuLoginService:
    key = id(account_manager)
    if key not in _login_services:
        _login_services[key] = XueqiuLoginService(account_manager)
    return _login_services[key]
