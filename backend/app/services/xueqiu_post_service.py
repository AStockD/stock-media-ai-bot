"""Xueqiu post creation service using Playwright with per-user storage state."""
import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser

from app.services.account_manager import AccountManager

logger = logging.getLogger(__name__)


class XueqiuPostService:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    async def _setup_browser(self, storage_state_path: str):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
        )
        context = await browser.new_context(
            storage_state=storage_state_path,
            viewport={"width": 1280, "height": 800},
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

        login_btn = await page.query_selector("text=立即登录/注册")
        if login_btn and await login_btn.is_visible():
            await self._cleanup(pw, browser)
            return {"success": False, "error": "登录已过期，请重新扫码登录"}

        try:
            close_btn = await page.query_selector('[class*="newLogin_modal"] [class*="close"]')
            if close_btn and await close_btn.is_visible():
                await close_btn.evaluate("e => e.click()")
                await page.wait_for_timeout(500)
        except Exception:
            pass

        try:
            await page.evaluate("""() => {
                const waf = document.getElementById('waf_nc_block');
                if (waf) waf.remove();
                const mask = document.querySelector('.waf-nc-mask');
                if (mask) mask.remove();
            }""")
        except Exception:
            pass

        return None

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
            return tmp.name

    async def _upload_image_to_xueqiu(self, page, image_path: str) -> Optional[str]:
        """Upload image directly via Xueqiu's upload API"""
        try:
            # Read image file
            with open(image_path, 'rb') as f:
                image_data = f.read()

            # Get upload token
            token_resp = await page.evaluate("""async () => {
                const resp = await fetch('https://xueqiu.com/upload/token.json');
                return await resp.json();
            }""")
            logger.info(f"Upload token response: {token_resp}")

            if not token_resp or 'token' not in token_resp:
                logger.error("Failed to get upload token")
                return None

            upload_token = token_resp['token']

            # Upload image
            upload_resp = await page.evaluate("""async (imageData, token) => {
                const blob = new Blob([new Uint8Array(imageData)], {type: 'image/png'});
                const formData = new FormData();
                formData.append('file', blob, 'image.png');
                formData.append('token', token);

                const resp = await fetch('https://xueqiu.com/upload/image.json', {
                    method: 'POST',
                    body: formData
                });
                return await resp.json();
            }""", list(image_data), upload_token)

            logger.info(f"Image upload response: {upload_resp}")

            if upload_resp and 'url' in upload_resp:
                return upload_resp['url']
            return None

        except Exception as e:
            logger.error(f"Failed to upload image via API: {e}")
            return None

    async def create_post(self, user_id: int, content: str,
                          image_path: str = None, image_url: str = None,
                          platform: str = "xueqiu") -> dict:
        storage_state_path = self.account_manager.get_storage_state_path(user_id, platform)
        if not storage_state_path:
            return {"success": False, "error": "未登录，请先扫码登录"}

        pw = None
        browser = None
        downloaded_image = None
        try:
            if image_url and not image_path:
                try:
                    logger.info(f"Downloading image from: {image_url}")
                    downloaded_image = await self._download_image(image_url)
                    image_path = downloaded_image
                    logger.info(f"Image downloaded to: {image_path}")
                except Exception as e:
                    logger.error(f"Failed to download image: {e}")
                    return {"success": False, "error": f"图片下载失败: {e}"}

            pw, browser, context, page = await self._setup_browser(storage_state_path)

            api_responses = []

            def on_response(response):
                url = response.url
                if any(kw in url for kw in ["statuses", "post", "create", "update", "publish"]):
                    api_responses.append({"url": url, "status": response.status})
                    logger.info(f"Relevant API response: {url} -> {response.status}")

            page.on("response", on_response)

            err = await self._navigate_and_check_login(page, browser, pw)
            if err:
                return err

            editor = await page.query_selector(".lite-editor__textarea.post_status")
            if not editor or not await editor.is_visible():
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到发帖编辑器"}

            if image_path:
                logger.info(f"Uploading image: {image_path}")
                img_path = Path(image_path)
                if not img_path.exists():
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": f"图片文件不存在: {image_path}"}

                # Focus editor first
                await editor.evaluate("e => { e.focus(); e.click(); }")
                await page.wait_for_timeout(1000)

                # Try to find and use the file input directly
                file_input = await page.query_selector('input[type="file"][accept*="image"]')
                if file_input:
                    logger.info("Found file input, using setInputFiles")
                    await file_input.set_input_files(str(img_path))
                    await page.wait_for_timeout(8000)
                else:
                    # Fallback to clicking upload button
                    upload_btn = await page.query_selector(".lite-editor__upload--img")
                    if not upload_btn:
                        logger.error("Image upload button not found")
                        await self._cleanup(pw, browser)
                        return {"success": False, "error": "未找到图片上传按钮"}

                    logger.info("Clicking upload button and selecting file")
                    async with page.expect_file_chooser(timeout=10000) as fc_info:
                        await upload_btn.evaluate("e => e.click()")
                    file_chooser = await fc_info.value
                    await file_chooser.set_files(str(img_path))
                    await page.wait_for_timeout(8000)

                logger.info("Image upload wait complete")
                await page.screenshot(path="/tmp/xq_after_image_upload.png")

                # Check if image was actually added to editor
                img_count = await page.evaluate("""() => {
                    const editor = document.querySelector('.lite-editor__textarea.post_status');
                    if (!editor) return 0;
                    return editor.querySelectorAll('img').length;
                }""")
                logger.info(f"Images in editor after upload: {img_count}")

                try:
                    await page.evaluate("""() => {
                        const waf = document.getElementById('waf_nc_block');
                        if (waf) waf.remove();
                        const mask = document.querySelector('.waf-nc-mask');
                        if (mask) mask.remove();
                    }""")
                except Exception:
                    pass

            editor = await page.query_selector(".lite-editor__textarea.post_status")
            if not editor:
                await self._cleanup(pw, browser)
                return {"success": False, "error": "编辑器丢失"}

            # Click the editor container to activate it
            await editor.evaluate("e => { e.scrollIntoView({block:'center'}); e.click(); }")
            await page.wait_for_timeout(1000)

            # Wait for contenteditable to appear (created lazily by MediumEditor framework)
            content_inserted = False
            for attempt in range(5):
                editable = await page.query_selector('.lite-editor__textarea.post_status [contenteditable="true"]')
                if editable:
                    await editable.evaluate("e => { e.focus(); }")
                    await page.wait_for_timeout(300)

                    # Use execCommand('insertText') which fires proper InputEvent for framework reactivity
                    await page.evaluate("""(text) => {
                        const ce = document.querySelector('.lite-editor__textarea.post_status [contenteditable="true"]');
                        if (!ce) return false;
                        ce.focus();
                        ce.innerHTML = '';
                        document.execCommand('insertText', false, text);
                        return true;
                    }""", content)
                    await page.wait_for_timeout(500)
                    content_inserted = True
                    break

                await editor.evaluate("e => e.click()")
                await page.wait_for_timeout(800)

            if not content_inserted:
                # Fallback: click placeholder directly to activate editor
                logger.warning("Contenteditable not found, clicking placeholder...")
                placeholder = await page.query_selector('.lite-editor__textarea.post_status .fake-placeholder')
                if placeholder:
                    await placeholder.evaluate("e => e.click()")
                    await page.wait_for_timeout(1000)

                editable = await page.query_selector('.lite-editor__textarea.post_status [contenteditable="true"]')
                if editable:
                    await editable.evaluate("e => { e.focus(); }")
                    await page.wait_for_timeout(300)
                    await page.evaluate("""(text) => {
                        const ce = document.querySelector('.lite-editor__textarea.post_status [contenteditable="true"]');
                        if (!ce) return;
                        ce.focus();
                        ce.innerHTML = '';
                        document.execCommand('insertText', false, text);
                    }""", content)
                    await page.wait_for_timeout(500)
                else:
                    # Last resort: create contenteditable with proper InputEvent
                    logger.warning("Creating contenteditable with InputEvent dispatch")
                    await page.evaluate("""(text) => {
                        const ed = document.querySelector('.lite-editor__textarea.post_status');
                        if (!ed) return false;
                        const ph = ed.querySelector('.fake-placeholder');
                        if (ph) ph.remove();
                        let ce = ed.querySelector('[contenteditable="true"]');
                        if (!ce) {
                            ce = document.createElement('div');
                            ce.setAttribute('contenteditable', 'true');
                            ce.style.cssText = 'min-height: 40px; outline: none;';
                            ed.appendChild(ce);
                        }
                        ce.focus();
                        ce.textContent = text;
                        ce.dispatchEvent(new InputEvent('input', {
                            bubbles: true, cancelable: true, inputType: 'insertText', data: text
                        }));
                        ce.dispatchEvent(new Event('change', {bubbles: true}));
                        return true;
                    }""", content)
                    await page.wait_for_timeout(500)

            await page.screenshot(path="/tmp/xq_before_submit.png")

            editor_html = await page.evaluate("""() => {
                const ed = document.querySelector('.lite-editor__textarea.post_status');
                return ed ? ed.innerHTML : 'NOT FOUND';
            }""")
            logger.info(f"Editor HTML: {editor_html[:500]}")

            submit_btn = await page.query_selector(".lite-editor__toolbar__post")
            if not submit_btn:
                submit_btn = await page.query_selector(".lite-editor__submit")
            if not submit_btn:
                await page.screenshot(path="/tmp/xq_no_submit.png")
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到发布按钮"}

            btn_classes = await submit_btn.evaluate("e => e.className")
            btn_disabled = await submit_btn.evaluate('e => e.classList.contains("disabled") || e.hasAttribute("disabled")')
            logger.info(f"Submit button classes: {btn_classes}, disabled: {btn_disabled}")

            try:
                await page.click(".lite-editor__toolbar__post", force=True, timeout=5000)
            except Exception:
                try:
                    await submit_btn.evaluate("""e => {
                        const rect = e.getBoundingClientRect();
                        const x = rect.left + rect.width / 2;
                        const y = rect.top + rect.height / 2;
                        e.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
                        e.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
                        e.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, clientX: x, clientY: y}));
                    }""")
                except Exception:
                    await submit_btn.evaluate("e => e.click()")

            await page.wait_for_timeout(5000)

            await page.screenshot(path="/tmp/xq_after_submit.png")

            error_msg = await page.evaluate("""() => {
                const toasts = document.querySelectorAll('.toast, .notice, .error-msg, [class*="toast"], [class*="notice"], [class*="error"]');
                for (const t of toasts) {
                    if (t.textContent && t.textContent.trim()) {
                        return t.textContent.trim();
                    }
                }
                return null;
            }""")
            if error_msg:
                logger.warning(f"Error message on page: {error_msg}")

            new_storage = await context.storage_state()
            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            self.account_manager.save_cookies(user_id, platform, cookies_dict, new_storage)

            current_url = page.url
            logger.info(f"After submit URL: {current_url}")
            logger.info(f"API responses captured: {api_responses}")
            await self._cleanup(pw, browser)

            post_success = any(
                r.get("status") in (200, 201) and any(kw in r.get("url", "") for kw in ["create", "update"])
                for r in api_responses
            )

            if post_success:
                return {"success": True, "message": "发帖成功", "url": current_url}
            else:
                error_detail = error_msg or "未检测到发帖API请求，可能提交失败"
                logger.warning(f"Post may have failed: {error_detail}")
                return {"success": False, "error": error_detail, "api_responses": len(api_responses)}

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
            if storage_state_path:
                try:
                    Path(storage_state_path).unlink(missing_ok=True)
                    Path(storage_state_path).parent.rmdir()
                except Exception:
                    pass
