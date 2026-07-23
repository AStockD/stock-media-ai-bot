"""Xueqiu post creation service using Playwright with per-user storage state."""
import asyncio
import glob
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Browser

from app.services.account_manager import AccountManager

logger = logging.getLogger(__name__)

TEMP_FILE_MAX_AGE = 8 * 3600  # 8 hours


class XueqiuPostService:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    @staticmethod
    def _cleanup_temp_files():
        """Remove temp image files and screenshots older than TEMP_FILE_MAX_AGE."""
        now = time.time()
        patterns = ["/tmp/tmp*.png", "/tmp/tmp*.jpg", "/tmp/xq_*.png"]
        removed = 0
        for pattern in patterns:
            for fpath in glob.glob(pattern):
                try:
                    if now - Path(fpath).stat().st_mtime > TEMP_FILE_MAX_AGE:
                        Path(fpath).unlink()
                        removed += 1
                except Exception:
                    pass
        if removed:
            logger.info(f"Cleaned up {removed} old temp files")

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

    async def create_post(self, user_id: int, content: str,
                          image_path: str = None, image_url: str = None,
                          platform: str = "xueqiu") -> dict:
        self._cleanup_temp_files()

        storage_state_path = self.account_manager.get_storage_state_path(user_id, platform)
        if not storage_state_path:
            return {"success": False, "error": "未登录，请先扫码登录"}

        pw = None
        browser = None
        downloaded_image = None
        try:
            pw, browser, context, page = await self._setup_browser(storage_state_path)

            api_responses = []

            def on_response(response):
                url = response.url
                if any(kw in url for kw in ["statuses", "post", "create", "update", "publish"]):
                    entry = {"url": url, "status": response.status, "body": ""}
                    try:
                        entry["body"] = response.text()
                    except Exception:
                        pass
                    api_responses.append(entry)
                    logger.info(f"Relevant API response: {url} -> {response.status}")

            page.on("response", on_response)

            err = await self._navigate_and_check_login(page, browser, pw)
            if err:
                return err

            editor = await page.query_selector(".lite-editor__textarea.post_status")
            if not editor:
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到发帖编辑器"}

            # Remove WAF captcha overlay before any interaction
            try:
                await page.evaluate("""() => {
                    const waf = document.getElementById('waf_nc_block');
                    if (waf) waf.remove();
                    const mask = document.querySelector('.waf-nc-mask');
                    if (mask) mask.remove();
                }""")
            except Exception:
                pass

            # --- Step 1: Type text content first ---
            await editor.evaluate("e => { e.scrollIntoView({block:'center'}); e.click(); }")
            await page.wait_for_timeout(1000)

            content_inserted = False
            for attempt in range(5):
                editable = await page.query_selector('.lite-editor__textarea.post_status [contenteditable="true"]')
                if editable:
                    await editable.evaluate("e => { e.focus(); }")
                    await page.wait_for_timeout(300)

                    await page.evaluate("""() => {
                        const ce = document.querySelector('.lite-editor__textarea.post_status [contenteditable="true"]');
                        if (!ce) return;
                        ce.focus();
                        ce.innerHTML = '';
                        ce.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'deleteContentBackward' }));
                    }""")
                    await page.wait_for_timeout(200)

                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if line.strip():
                            await page.evaluate("""(text) => {
                                document.execCommand('insertText', false, text);
                            }""", line)
                            await page.evaluate("""() => {
                                document.execCommand('insertLineBreak');
                            }""")
                            await page.wait_for_timeout(30)
                    await page.wait_for_timeout(500)
                    content_inserted = True
                    break

                await editor.evaluate("e => e.click()")
                await page.wait_for_timeout(800)

            if not content_inserted:
                logger.warning("Contenteditable not found, clicking placeholder...")
                placeholder = await page.query_selector('.lite-editor__textarea.post_status .fake-placeholder')
                if placeholder:
                    await placeholder.evaluate("e => e.click()")
                    await page.wait_for_timeout(1000)

                editable = await page.query_selector('.lite-editor__textarea.post_status [contenteditable="true"]')
                if editable:
                    await editable.evaluate("e => { e.focus(); }")
                    await page.wait_for_timeout(300)

                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if line.strip():
                            await page.evaluate("""(text) => {
                                document.execCommand('insertText', false, text);
                            }""", line)
                            await page.evaluate("""() => {
                                document.execCommand('insertLineBreak');
                            }""")
                            await page.wait_for_timeout(30)
                    await page.wait_for_timeout(500)
                else:
                    logger.warning("Creating contenteditable for keyboard input")
                    await page.evaluate("""() => {
                        const ed = document.querySelector('.lite-editor__textarea.post_status');
                        if (!ed) return;
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
                    }""")
                    await page.wait_for_timeout(300)

                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if line.strip():
                            await page.evaluate("""(text) => {
                                document.execCommand('insertText', false, text);
                            }""", line)
                            await page.evaluate("""() => {
                                document.execCommand('insertLineBreak');
                            }""")
                            await page.wait_for_timeout(30)
                    await page.wait_for_timeout(500)

            # --- Step 2: Upload image AFTER text is typed ---
            if image_url or image_path:
                uploaded_url = None

                tmp_image = None
                target_path = image_path
                if not target_path or not Path(target_path).exists():
                    if image_url:
                        logger.info(f"Downloading image from URL: {image_url}")
                        try:
                            img_resp = await context.request.get(image_url)
                            if img_resp.ok:
                                image_data = await img_resp.body()
                                import tempfile
                                tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
                                tmp.write(image_data)
                                tmp.close()
                                tmp_image = tmp.name
                                target_path = tmp_image
                                logger.info(f"Image downloaded: {len(image_data)} bytes -> {tmp_image}")
                            else:
                                logger.error(f"Failed to download image: {img_resp.status}")
                        except Exception as e:
                            logger.error(f"Image download failed: {e}")

                if target_path:
                    img_path = Path(target_path)
                    if img_path.exists():
                        logger.info(f"Uploading image via editor image button: {target_path}")

                        await page.evaluate("""() => {
                            const waf = document.getElementById('waf_nc_block');
                            if (waf) waf.remove();
                            const mask = document.querySelector('.waf-nc-mask');
                            if (mask) mask.remove();
                        }""")

                        img_btn = await page.query_selector('a[data-analytics-data*="image"]')
                        if not img_btn:
                            logger.warning("Image upload button not found")
                        else:
                            try:
                                async with page.expect_file_chooser(timeout=10000) as fc_info:
                                    await img_btn.click(force=True)
                                fc = await fc_info.value
                                await fc.set_files(str(img_path))
                                logger.info("Image file selected via chooser")

                                img_ready = False
                                for wait_attempt in range(20):
                                    await page.wait_for_timeout(1000)
                                    img_ready = await page.evaluate("""() => {
                                        const ed = document.querySelector('.lite-editor__textarea.post_status');
                                        if (!ed) return false;
                                        const imgs = ed.querySelectorAll('img.ke_img, .img-single-upload img');
                                        if (imgs.length === 0) return false;
                                        for (const img of imgs) {
                                            if (img.naturalWidth > 0 && img.clientWidth > 0) return true;
                                        }
                                        return false;
                                    }""")
                                    if img_ready:
                                        break
                                logger.info(f"Image ready in editor: {img_ready} (waited {wait_attempt + 1}s)")
                                if img_ready:
                                    uploaded_url = "file_chooser_upload"
                            except Exception as e:
                                logger.error(f"File chooser failed: {e}")
                    else:
                        logger.error(f"Image file not found: {target_path}")

                if tmp_image:
                    try:
                        Path(tmp_image).unlink()
                    except Exception:
                        pass

                if not uploaded_url:
                    logger.error("Image upload failed")

                await page.screenshot(path="/tmp/xq_after_image_upload.png")

            await page.screenshot(path="/tmp/xq_before_submit.png")

            editor_html = await page.evaluate("""() => {
                const ed = document.querySelector('.lite-editor__textarea.post_status');
                return ed ? ed.innerHTML : 'NOT FOUND';
            }""")
            logger.info(f"Editor HTML length: {len(editor_html)}")
            logger.info(f"Editor HTML: {editor_html[:2000]}")

            img_info = await page.evaluate("""() => {
                const ed = document.querySelector('.lite-editor__textarea.post_status');
                if (!ed) return 'editor not found';
                const imgs = ed.querySelectorAll('img');
                const result = [];
                imgs.forEach((img, i) => {
                    result.push({
                        index: i,
                        src: img.src ? img.src.substring(0, 100) : 'no src',
                        className: img.className,
                        visible: img.offsetParent !== null,
                        rect: img.getBoundingClientRect ? JSON.stringify({
                            top: img.getBoundingClientRect().top,
                            left: img.getBoundingClientRect().left,
                            width: img.getBoundingClientRect().width,
                            height: img.getBoundingClientRect().height
                        }) : 'no rect'
                    });
                });
                return JSON.stringify(result);
            }""")
            logger.info(f"Image elements in editor: {img_info}")

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

            account_name = None
            try:
                account_name = await page.evaluate("""() => {
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
            except Exception:
                pass

            self.account_manager.save_cookies(user_id, platform, cookies_dict, new_storage, account_name)

            current_url = page.url
            logger.info(f"After submit URL: {current_url}")
            logger.info(f"API responses captured: {api_responses}")
            await self._cleanup(pw, browser)

            post_success = any(
                r.get("status") in (200, 201) and any(kw in r.get("url", "") for kw in ["create", "update"])
                for r in api_responses
            )

            if post_success:
                post_id = ""
                for r in api_responses:
                    if r.get("status") in (200, 201) and any(kw in r.get("url", "") for kw in ["create", "update"]):
                        try:
                            body = json.loads(r.get("body", "{}"))
                            post_id = str(body.get("id", "") or body.get("statuses_id", ""))
                        except Exception:
                            pass
                        if post_id:
                            break

                if not post_id:
                    parts = current_url.rstrip("/").split("/")
                    if parts:
                        last = parts[-1]
                        if last.isdigit():
                            post_id = last

                return {"success": True, "message": "发帖成功", "url": current_url, "post_id": post_id}
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
