"""Xueqiu post creation service using Playwright with saved storage state."""
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser

from app.config import STORAGE_STATE_FILE

logger = logging.getLogger(__name__)


class XueqiuPostService:
    def __init__(self):
        self.user_agent = (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )

    async def _setup_browser(self):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
        )
        context = await browser.new_context(
            storage_state=str(STORAGE_STATE_FILE),
            viewport={'width': 1280, 'height': 800},
            user_agent=self.user_agent,
        )
        page = await context.new_page()
        return pw, browser, context, page

    async def _cleanup(self, pw, browser):
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

    async def _navigate_and_check_login(self, page, browser, pw):
        logger.info("Navigating to Xueqiu home page...")
        await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)

        login_btn = await page.query_selector('text=立即登录/注册')
        if login_btn and await login_btn.is_visible():
            await self._cleanup(pw, browser)
            return {"success": False, "error": "登录已过期，请重新扫码登录"}

        # Close login modal if it appears
        try:
            close_btn = await page.query_selector('[class*="newLogin_modal"] [class*="close"]')
            if close_btn and await close_btn.is_visible():
                await close_btn.evaluate('e => e.click()')
                await page.wait_for_timeout(500)
        except Exception:
            pass

        # Remove WAF overlay if present
        try:
            await page.evaluate('''() => {
                const waf = document.getElementById('waf_nc_block');
                if (waf) waf.remove();
                const mask = document.querySelector('.waf-nc-mask');
                if (mask) mask.remove();
            }''')
        except Exception:
            pass

        return None

    async def _log_response_body(self, response):
        try:
            body = await response.text()
            logger.info(f"Response body for {response.url[:100]}: {body[:500]}")
        except Exception as e:
            logger.warning(f"Could not read response body: {e}")

    async def _download_image(self, url: str) -> str:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": self.user_agent})
            resp.raise_for_status()
            suffix = ".png"
            ct = resp.headers.get("content-type", "")
            if "jpeg" in ct or "jpg" in ct:
                suffix = ".jpg"
            elif "gif" in ct:
                suffix = ".gif"
            elif "webp" in ct:
                suffix = ".webp"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False, dir="/tmp")
            tmp.write(resp.content)
            tmp.close()
            logger.info(f"Downloaded image from {url} to {tmp.name} ({len(resp.content)} bytes)")
            return tmp.name

    async def create_post(self, content: str, image_path: str = None, image_url: str = None) -> dict:
        if not STORAGE_STATE_FILE.exists():
            return {"success": False, "error": "未登录，请先扫码登录"}

        pw = None
        browser = None
        downloaded_image = None
        try:
            if image_url and not image_path:
                try:
                    downloaded_image = await self._download_image(image_url)
                    image_path = downloaded_image
                    logger.info(f"Using downloaded image: {downloaded_image}")
                except Exception as e:
                    return {"success": False, "error": f"图片下载失败: {e}"}

            pw, browser, context, page = await self._setup_browser()

            # Collect network responses for debugging
            api_responses = []

            def on_response(response):
                url = response.url
                if any(kw in url for kw in ['statuses', 'post', 'create', 'update', 'publish']):
                    api_responses.append({
                        "url": url,
                        "status": response.status,
                    })
                    logger.info(f"Relevant API response: {url} -> {response.status}")
                    if 'update.json' in url:
                        asyncio.ensure_future(self._log_response_body(response))

            page.on("response", on_response)

            # Navigate and check login
            err = await self._navigate_and_check_login(page, browser, pw)
            if err:
                return err

            # Find the post editor
            editor = await page.query_selector('.lite-editor__textarea.post_status')
            if not editor or not await editor.is_visible():
                await page.screenshot(path="/tmp/xq_no_editor.png")
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到发帖编辑器"}

            # Step 1: Upload image FIRST (if provided), because image upload resets the editor DOM
            if image_path:
                img_path = Path(image_path)
                if not img_path.exists():
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": f"图片文件不存在: {image_path}"}

                # Focus editor first
                await editor.evaluate('e => { e.focus(); e.click(); }')
                await page.wait_for_timeout(500)

                logger.info(f"Uploading image: {image_path}")
                upload_btn = await page.query_selector('.lite-editor__upload--img')
                if not upload_btn:
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": "未找到图片上传按钮"}

                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await upload_btn.evaluate('e => e.click()')
                file_chooser = await fc_info.value
                await file_chooser.set_files(str(img_path))
                logger.info("Image file set, waiting for upload...")
                await page.wait_for_timeout(5000)

                # Re-remove WAF overlay (may reappear after upload)
                try:
                    await page.evaluate('''() => {
                        const waf = document.getElementById('waf_nc_block');
                        if (waf) waf.remove();
                        const mask = document.querySelector('.waf-nc-mask');
                        if (mask) mask.remove();
                    }''')
                except Exception:
                    pass

            # Step 2: Type content AFTER image upload (editor DOM may have been rebuilt)
            editor = await page.query_selector('.lite-editor__textarea.post_status')
            if not editor:
                await self._cleanup(pw, browser)
                return {"success": False, "error": "编辑器丢失"}

            # Click into the contenteditable area (after the image)
            editable = await page.query_selector('.lite-editor__textarea.post_status [contenteditable="true"]')
            if editable:
                await editable.evaluate('e => { e.focus(); e.click(); }')
            else:
                await editor.evaluate('e => { e.focus(); e.click(); }')
            await page.wait_for_timeout(500)

            # Move cursor to end and type
            await page.keyboard.press('End')
            await page.keyboard.type(content, delay=30)
            await page.wait_for_timeout(500)

            await page.screenshot(path="/tmp/xq_before_submit.png")

            # Debug: log editor content and submit button state
            editor_html = await page.evaluate('''() => {
                const ed = document.querySelector('.lite-editor__textarea.post_status');
                return ed ? ed.innerHTML : 'NOT FOUND';
            }''')
            logger.info(f"Editor HTML: {editor_html[:500]}")

            # Find submit button (re-query in case DOM changed after image upload)
            submit_btn = await page.query_selector('.lite-editor__toolbar__post')
            if not submit_btn:
                submit_btn = await page.query_selector('.lite-editor__submit')
            if not submit_btn:
                submit_btn = await page.query_selector('a.lite-editor__submit')
            if not submit_btn:
                await page.screenshot(path="/tmp/xq_no_submit.png")
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到发布按钮"}

            btn_classes = await submit_btn.evaluate('e => e.className')
            btn_disabled = await submit_btn.evaluate('e => e.classList.contains("disabled") || e.hasAttribute("disabled")')
            logger.info(f"Submit button classes: {btn_classes}, disabled: {btn_disabled}")

            # Strategy 1: Try Playwright's native click with force
            try:
                await page.click('.lite-editor__toolbar__post', force=True, timeout=5000)
                logger.info("Playwright force click succeeded")
            except Exception as e1:
                logger.warning(f"Playwright force click failed: {e1}")
                # Strategy 2: Dispatch a proper MouseEvent via JS
                try:
                    await submit_btn.evaluate('''e => {
                        const rect = e.getBoundingClientRect();
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        e.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
                        e.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
                        e.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
                    }''')
                    logger.info("JS MouseEvent dispatch succeeded")
                except Exception as e2:
                    logger.warning(f"JS MouseEvent dispatch failed: {e2}")
                    # Strategy 3: Plain JS click as last resort
                    await submit_btn.evaluate('e => e.click()')
                    logger.info("Plain JS click used")

            await page.wait_for_timeout(5000)

            await page.screenshot(path="/tmp/xq_after_submit.png")

            # Check for error/toast messages
            error_msg = await page.evaluate('''() => {
                const toasts = document.querySelectorAll('.toast, .notice, .error-msg, [class*="toast"], [class*="notice"], [class*="error"]');
                for (const t of toasts) {
                    if (t.textContent && t.textContent.trim()) {
                        return t.textContent.trim();
                    }
                }
                return null;
            }''')
            if error_msg:
                logger.warning(f"Error message on page: {error_msg}")

            # Check API responses
            post_success = any(
                r.get("status") in (200, 201) and r.get("url")
                for r in api_responses
            )

            # Update storage state
            await context.storage_state(path=str(STORAGE_STATE_FILE))

            current_url = page.url
            logger.info(f"After submit URL: {current_url}")
            logger.info(f"API responses captured: {api_responses}")

            await self._cleanup(pw, browser)

            if post_success or current_url != "https://xueqiu.com/":
                return {
                    "success": True,
                    "message": "发帖成功",
                    "url": current_url,
                }
            else:
                error_detail = error_msg or "未检测到发帖API请求，可能提交失败"
                logger.warning(f"Post may have failed: {error_detail}")
                return {
                    "success": False,
                    "error": error_detail,
                    "api_responses": api_responses,
                }

        except Exception as e:
            logger.error(f"Failed to create post: {e}", exc_info=True)
            await self._cleanup(pw, browser)
            return {"success": False, "error": str(e)}
        finally:
            if downloaded_image:
                try:
                    Path(downloaded_image).unlink(missing_ok=True)
                except Exception:
                    pass


post_service: Optional[XueqiuPostService] = None


def get_post_service() -> XueqiuPostService:
    global post_service
    if post_service is None:
        post_service = XueqiuPostService()
    return post_service
