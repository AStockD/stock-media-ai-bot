"""Playwright-based Xueqiu (雪球) login service with QR code scanning."""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, Page

from app.services.cookie_manager import CookieManager
from app.config import STORAGE_STATE_FILE

logger = logging.getLogger(__name__)

_browser: Optional[Browser] = None
_page: Optional[Page] = None
_login_task: Optional[asyncio.Task] = None
_playwright_instance = None
_last_result: Optional[dict] = None


class XueqiuLoginService:
    def __init__(self, cookie_mgr: CookieManager):
        self.cookie_mgr = cookie_mgr
        self.screenshot_path = Path("/tmp/xueqiu_qr.png")

    async def _cleanup(self):
        global _browser, _page, _playwright_instance
        try:
            if _page and not _page.is_closed():
                await _page.close()
        except Exception:
            pass
        try:
            if _browser and _browser.is_connected():
                await _browser.close()
        except Exception:
            pass
        try:
            if _playwright_instance:
                await _playwright_instance.stop()
        except Exception:
            pass
        _browser = None
        _page = None
        _playwright_instance = None

    async def start_login(self) -> dict:
        global _browser, _page, _login_task, _playwright_instance, _last_result

        _last_result = None

        if _browser and _browser.is_connected():
            logger.info("Browser already running, cleaning up first")
            await self._cleanup()

        try:
            logger.info("Launching headless Chromium for Xueqiu...")
            _playwright_instance = await async_playwright().start()
            _browser = await _playwright_instance.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
            )
            context = await _browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
            )
            _page = await context.new_page()

            logger.info("Navigating to Xueqiu...")
            await _page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
            
            # Wait for login modal to appear (it should auto-open)
            logger.info("Waiting for login modal...")
            for attempt in range(10):
                modal = await _page.query_selector('[class*="newLogin_modal"]')
                if modal:
                    is_visible = await modal.is_visible()
                    if is_visible:
                        logger.info(f"Login modal found after {attempt + 1}s")
                        break
                await _page.wait_for_timeout(1000)
            else:
                logger.warning("Login modal not found after 10s, taking screenshot")
                await _page.screenshot(path=str(self.screenshot_path))
                return {
                    "status": "error",
                    "error": "登录弹窗未出现，请检查页面加载"
                }

            # Click "二维码登录" tab via JS to avoid viewport issues
            # Retry a few times in case it's not immediately available
            qr_tab = None
            for attempt in range(5):
                qr_tab = await _page.query_selector('text=二维码登录')
                if qr_tab:
                    break
                await _page.wait_for_timeout(500)
            
            if qr_tab:
                logger.info("Clicking QR code login tab...")
                await qr_tab.evaluate('e => e.click()')
                await _page.wait_for_timeout(2000)
            else:
                logger.warning("QR tab not found after retries, taking full page screenshot")
                await _page.screenshot(path=str(self.screenshot_path))
                _login_task = asyncio.create_task(self._monitor_login())
                return {
                    "status": "waiting_for_scan",
                    "screenshot": str(self.screenshot_path),
                    "message": "未找到二维码登录选项，请查看截图"
                }

            # Capture the QR code canvas element
            qr_canvas = await _page.query_selector('canvas')
            if qr_canvas:
                await qr_canvas.screenshot(path=str(self.screenshot_path))
                logger.info("QR code canvas captured")
            else:
                # Fallback: screenshot the QR wrapper div
                qr_wrapper = await _page.query_selector('[class*="qrcode__wrapper"]')
                if qr_wrapper:
                    await qr_wrapper.screenshot(path=str(self.screenshot_path))
                else:
                    await _page.screenshot(path=str(self.screenshot_path))
                logger.warning("Canvas not found, used fallback screenshot")

            _login_task = asyncio.create_task(self._monitor_login())

            return {
                "status": "waiting_for_scan",
                "screenshot": str(self.screenshot_path),
                "message": "请使用雪球APP扫描二维码登录"
            }

        except Exception as e:
            logger.error(f"Failed to start login: {e}", exc_info=True)
            await self._cleanup()
            return {"status": "error", "error": str(e)}

    async def _monitor_login(self):
        global _browser, _page, _last_result
        try:
            max_wait = 120
            modal_was_visible = False

            for i in range(max_wait):
                try:
                    if not _page or _page.is_closed():
                        logger.warning("Page closed during monitoring")
                        result = {"status": "error", "error": "Browser page closed"}
                        _last_result = result
                        return result

                    await asyncio.sleep(1)

                    if not _page or _page.is_closed():
                        continue

                except Exception as e:
                    logger.warning(f"Browser check failed: {e}")
                    result = {"status": "error", "error": f"Browser error: {e}"}
                    _last_result = result
                    return result

                # Check if login succeeded: the "立即登录/注册" button disappears after login
                try:
                    login_btn = await _page.query_selector('text=立即登录/注册')
                    if login_btn:
                        btn_visible = await login_btn.is_visible()
                        if not btn_visible and i > 3:
                            logger.info("Login successful! '立即登录/注册' button disappeared.")
                            context = _page.context
                            cookies = await context.cookies()
                            cookie_count = self.cookie_mgr.import_cookie_dict(cookies)
                            logger.info(f"Saved {cookie_count} cookies")
                            await context.storage_state(path=str(STORAGE_STATE_FILE))
                            logger.info(f"Saved storage state to {STORAGE_STATE_FILE}")
                            await self._cleanup()
                            result = {"status": "success", "cookie_count": cookie_count}
                            _last_result = result
                            return result
                    else:
                        # Button not found at all — also indicates logged in
                        if i > 3:
                            logger.info("Login successful! '立即登录/注册' button not found.")
                            context = _page.context
                            cookies = await context.cookies()
                            cookie_count = self.cookie_mgr.import_cookie_dict(cookies)
                            logger.info(f"Saved {cookie_count} cookies")
                            await context.storage_state(path=str(STORAGE_STATE_FILE))
                            logger.info(f"Saved storage state to {STORAGE_STATE_FILE}")
                            await self._cleanup()
                            result = {"status": "success", "cookie_count": cookie_count}
                            _last_result = result
                            return result
                except Exception:
                    pass

                # Fallback: check if the login modal disappeared
                try:
                    modal = await _page.query_selector('[class*="newLogin_modal"]')
                    if modal:
                        is_visible = await modal.is_visible()
                        if is_visible:
                            modal_was_visible = True
                        elif modal_was_visible and i > 3:
                            logger.info("Login modal disappeared — login succeeded")
                            context = _page.context
                            cookies = await context.cookies()
                            cookie_count = self.cookie_mgr.import_cookie_dict(cookies)
                            logger.info(f"Saved {cookie_count} cookies")
                            await context.storage_state(path=str(STORAGE_STATE_FILE))
                            logger.info(f"Saved storage state to {STORAGE_STATE_FILE}")
                            await self._cleanup()
                            result = {"status": "success", "cookie_count": cookie_count}
                            _last_result = result
                            return result
                except Exception:
                    pass

                if i % 10 == 0:
                    logger.info(f"Still waiting for login... ({i}s)")

            logger.warning("Login timeout")
            await self._cleanup()
            result = {"status": "timeout", "message": "登录超时，请重试"}
            _last_result = result
            return result

        except asyncio.CancelledError:
            logger.info("Login monitoring cancelled")
            return {"status": "cancelled"}
        except Exception as e:
            logger.error(f"Login monitoring error: {e}", exc_info=True)
            try:
                await self._cleanup()
            except Exception:
                pass
            result = {"status": "error", "error": str(e)}
            _last_result = result
            return result

    async def get_status(self) -> dict:
        try:
            if _last_result and _last_result.get("status") in ("success", "timeout", "error"):
                return _last_result
            if _login_task and _login_task.done():
                try:
                    result = _login_task.result()
                    if result:
                        return result
                except Exception:
                    pass
            if not _browser or not _browser.is_connected():
                if _last_result:
                    return _last_result
                return {"status": "idle"}
            return {"status": "waiting_for_scan"}
        except Exception:
            return {"status": "idle"}

    async def cancel_login(self):
        global _login_task, _last_result
        if _login_task and not _login_task.done():
            _login_task.cancel()
            _login_task = None
        _last_result = None
        await self._cleanup()
        return {"status": "cancelled"}


login_service: Optional[XueqiuLoginService] = None


def get_login_service(cookie_mgr: CookieManager) -> XueqiuLoginService:
    global login_service
    if login_service is None:
        login_service = XueqiuLoginService(cookie_mgr)
    return login_service
