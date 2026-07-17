"""Xueqiu comment service - per-user, uses storage state from DB."""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser

from app.services.account_manager import AccountManager

logger = logging.getLogger(__name__)


class XueqiuCommentService:
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

    async def create_comment(self, user_id: int, post_id: int, content: str,
                             platform: str = "xueqiu",
                             reply_to_comment_id: int = None) -> dict:
        storage_state_path = self.account_manager.get_storage_state_path(user_id, platform)
        if not storage_state_path:
            return {"success": False, "error": "未登录，请先扫码登录"}

        pw = None
        browser = None
        try:
            pw, browser, context, page = await self._setup_browser(storage_state_path)

            comment_api_result = {}

            def on_response(response):
                url = response.url
                method = response.request.method
                if method == "POST" and any(kw in url.lower() for kw in ["comment", "reply", "statuses"]):
                    logger.info(f"Comment API: {url} -> {response.status}")
                    comment_api_result["url"] = url
                    comment_api_result["status"] = response.status
                    try:
                        comment_api_result["body"] = response.text()
                    except Exception:
                        pass

            page.on("response", on_response)

            await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            login_btn = await page.query_selector("text=立即登录/注册")
            if login_btn and await login_btn.is_visible():
                await self._cleanup(pw, browser)
                return {"success": False, "error": "登录已过期，请重新扫码登录"}

            try:
                await page.evaluate("""() => {
                    const all = document.querySelectorAll('[id*="waf"], [class*="waf"]');
                    all.forEach(e => e.remove());
                }""")
            except Exception:
                pass

            scroll_result = await page.evaluate("""(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return {error: 'post link not found'};
                let article = link;
                while (article && article.tagName !== 'ARTICLE') {
                    article = article.parentElement;
                }
                if (!article) return {error: 'article not found'};
                const controls = article.querySelectorAll('a.timeline__item__control');
                for (const ctrl of controls) {
                    const span = ctrl.querySelector('span');
                    if (span && span.textContent.trim() === '讨论') {
                        ctrl.scrollIntoView({behavior: 'instant', block: 'center'});
                        const rect = ctrl.getBoundingClientRect();
                        return {found: true, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
                    }
                }
                return {error: '讨论 button not found in article'};
            }""", post_id)

            if not scroll_result.get("found"):
                await self._cleanup(pw, browser)
                return {"success": False, "error": f"未找到讨论按钮: {scroll_result.get('error')}"}

            await page.wait_for_timeout(500)

            article = page.locator(f'article:has(a[href*="{post_id}"])').first
            discuss_btn = article.locator('a.timeline__item__control:has(span:text("讨论"))')
            await discuss_btn.scroll_into_view_if_needed(timeout=5000)
            await page.wait_for_timeout(500)
            await discuss_btn.click(force=True, timeout=5000)
            await page.wait_for_timeout(3000)

            try:
                await page.evaluate("""() => {
                    const all = document.querySelectorAll('[id*="waf"], [class*="waf"]');
                    all.forEach(e => e.remove());
                }""")
            except Exception:
                pass

            editor_in_comment = await page.evaluate("""(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return null;
                let article = link;
                while (article && article.tagName !== 'ARTICLE') { article = article.parentElement; }
                if (!article) return null;
                const commentSection = article.querySelector('.timeline__item__comment');
                if (!commentSection) return null;
                const editor = commentSection.querySelector('[contenteditable="true"]');
                if (editor) {
                    const rect = editor.getBoundingClientRect();
                    return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                }
                const placeholder = commentSection.querySelector('.fake-placeholder');
                if (placeholder) {
                    const rect = placeholder.parentElement.getBoundingClientRect();
                    return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                }
                return {found: false};
            }""", post_id)

            if editor_in_comment and editor_in_comment.get("found"):
                await page.mouse.click(editor_in_comment["x"], editor_in_comment["y"])
                await page.wait_for_timeout(500)

            await page.keyboard.type(content, delay=30)
            await page.wait_for_timeout(500)

            submit_btn = None
            try:
                article_locator = page.locator(f'article:has(a[href*="{post_id}"])').first
                submit_locator = article_locator.locator('a.lite-editor__submit:text("发布")')
                count = await submit_locator.count()
                for i in range(count):
                    btn = submit_locator.nth(i)
                    if await btn.is_visible():
                        submit_btn = btn
                        break
            except Exception:
                pass

            mode_label = "回复" if reply_to_comment_id else "评论"

            if not submit_btn:
                submit_coords = await page.evaluate("""(postId) => {
                    const link = document.querySelector(`a[href*="${postId}"]`);
                    if (!link) return null;
                    let article = link;
                    while (article && article.tagName !== 'ARTICLE') { article = article.parentElement; }
                    if (!article) return null;
                    const commentSection = article.querySelector('.timeline__item__comment');
                    if (!commentSection) return null;
                    const btns = commentSection.querySelectorAll('a.lite-editor__submit');
                    for (const b of btns) {
                        const rect = b.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && b.textContent.trim() === '发布') {
                            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                        }
                    }
                    return null;
                }""", post_id)

                if submit_coords:
                    await page.mouse.click(submit_coords["x"], submit_coords["y"])
                    await page.wait_for_timeout(5000)

                    new_storage = await context.storage_state()
                    cookies_list = await context.cookies()
                    cookies_dict = {c["name"]: c["value"] for c in cookies_list}
                    self.account_manager.save_cookies(user_id, platform, cookies_dict, new_storage)

                    await self._cleanup(pw, browser)
                    if comment_api_result.get("status") in (200, 201):
                        return {"success": True, "message": f"{mode_label}成功", "post_id": post_id}
                    elif comment_api_result:
                        return {"success": False, "error": f"评论API: {comment_api_result.get('status')}"}
                    else:
                        return {"success": False, "error": "未检测到评论API请求"}
                else:
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": "未找到评论提交按钮"}

            if submit_btn:
                try:
                    await submit_btn.click(force=True, timeout=5000)
                except Exception:
                    await submit_btn.evaluate("e => e.click()")

            await page.wait_for_timeout(5000)

            new_storage = await context.storage_state()
            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            self.account_manager.save_cookies(user_id, platform, cookies_dict, new_storage)

            await self._cleanup(pw, browser)

            if comment_api_result.get("status") in (200, 201):
                return {"success": True, "message": f"{mode_label}成功", "post_id": post_id}
            elif comment_api_result:
                return {"success": False, "error": f"评论API: {comment_api_result.get('status')}"}
            else:
                return {"success": False, "error": "未检测到评论API请求"}

        except Exception as e:
            logger.error(f"Failed to create comment: {e}", exc_info=True)
            await self._cleanup(pw, browser)
            return {"success": False, "error": str(e)}
        finally:
            if storage_state_path:
                try:
                    Path(storage_state_path).unlink(missing_ok=True)
                    Path(storage_state_path).parent.rmdir()
                except Exception:
                    pass
