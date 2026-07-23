"""Xueqiu comment service - uses API from homepage context."""
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright

from app.services.account_manager import AccountManager

logger = logging.getLogger(__name__)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = { runtime: {} };
"""


class XueqiuCommentService:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

    async def _setup_browser(self, storage_state_path: str):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            storage_state=storage_state_path,
            viewport={"width": 1280, "height": 800},
            user_agent=self.user_agent,
        )
        await context.add_init_script(STEALTH_SCRIPT)
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

    async def _save_cookies(self, user_id, platform, context):
        new_storage = await context.storage_state()
        cookies_list = await context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies_list}
        self.account_manager.save_cookies(user_id, platform, cookies_dict, new_storage)

    async def _try_api_comment(self, page, context, post_url, content, post_id):
        xsrf = ""
        cookies_list = await context.cookies()
        for c in cookies_list:
            if c["name"] == "xq_a_token":
                xsrf = c["value"]
                break

        # Try form-encoded format
        form_endpoints = [
            {"url": "https://xueqiu.com/statuses/comment.json",
             "data": {"status_id": post_id, "text": content, "reply_comment_id": ""}},
            {"url": "https://xueqiu.com/v4/statuses/comment.json",
             "data": {"status_id": post_id, "text": content, "reply_comment_id": ""}},
            {"url": "https://xueqiu.com/statuses/update.json",
             "data": {"content": content, "status_id": post_id, "type": "comment"}},
        ]

        for ep in form_endpoints:
            try:
                result = await page.evaluate("""async ({url, data, xsrf}) => {
                    const headers = {'Content-Type': 'application/x-www-form-urlencoded'};
                    if (xsrf) headers['X-XSRF-TOKEN'] = xsrf;
                    const body = new URLSearchParams(data).toString();
                    try {
                        const resp = await fetch(url, {method: 'POST', headers, body, credentials: 'include'});
                        return {status: resp.status, body: await resp.text()};
                    } catch (e) { return {error: e.message}; }
                }""", {"url": ep["url"], "data": ep["data"], "xsrf": xsrf})
                logger.info(f"Form API {ep['url']}: {result}")
                if result.get("error"):
                    continue
                status = result.get("status", 0)
                body_text = result.get("body", "")
                if status in (200, 201):
                    try:
                        bj = json.loads(body_text)
                        if bj.get("error_code") and bj["error_code"] != 0:
                            logger.warning(f"API error_code: {bj.get('error_code')} - {bj.get('error_description', '')}")
                            continue
                    except Exception:
                        pass
                    return {"success": True, "message": "评论成功", "post_id": post_id}
            except Exception as e:
                logger.warning(f"API {ep['url']} failed: {e}")

        # Try JSON format
        json_endpoints = [
            {"url": "https://xueqiu.com/statuses/comment.json",
             "data": {"status_id": post_id, "text": content}},
            {"url": "https://xueqiu.com/v4/statuses/comment.json",
             "data": {"status_id": post_id, "text": content}},
            {"url": "https://xueqiu.com/statuses/update.json",
             "data": {"content": content, "status_id": post_id}},
        ]

        for ep in json_endpoints:
            try:
                result = await page.evaluate("""async ({url, data, xsrf}) => {
                    const headers = {'Content-Type': 'application/json'};
                    if (xsrf) headers['X-XSRF-TOKEN'] = xsrf;
                    try {
                        const resp = await fetch(url, {
                            method: 'POST',
                            headers,
                            body: JSON.stringify(data),
                            credentials: 'include'
                        });
                        return {status: resp.status, body: await resp.text()};
                    } catch (e) { return {error: e.message}; }
                }""", {"url": ep["url"], "data": ep["data"], "xsrf": xsrf})
                logger.info(f"JSON API {ep['url']}: {result}")
                if result.get("error"):
                    continue
                status = result.get("status", 0)
                body_text = result.get("body", "")
                if status in (200, 201):
                    try:
                        bj = json.loads(body_text)
                        if bj.get("error_code") and bj["error_code"] != 0:
                            logger.warning(f"API error_code: {bj.get('error_code')} - {bj.get('error_description', '')}")
                            continue
                    except Exception:
                        pass
                    return {"success": True, "message": "评论成功", "post_id": post_id}
            except Exception as e:
                logger.warning(f"API {ep['url']} failed: {e}")

        return None

    async def _remove_waf(self, page):
        waf_present = await page.evaluate("""() => {
            return !!(document.querySelector('[id*="waf"]') ||
                      document.querySelector('[class*="waf"]') ||
                      document.querySelector('[class*="nc-mask"]'));
        }""")
        if waf_present:
            logger.info("WAF detected, removing...")
            await page.evaluate("""() => {
                const waf = document.querySelector('#waf_nc_block, [class*="waf-nc-mask"], [class*="nc-mask"]');
                if (waf) waf.remove();
                const overlays = document.querySelectorAll('[style*="position: fixed"][style*="z-index"]');
                overlays.forEach(el => {
                    if (el.textContent.includes('verify') || el.textContent.includes('验证')) {
                        el.remove();
                    }
                });
            }""")
            await page.wait_for_timeout(2000)
        return waf_present

    async def create_comment(self, user_id: int, post_id: int = None,
                             post_url: str = None, content: str = "",
                             platform: str = "xueqiu",
                             reply_to_comment_id: int = None,
                             post_title: str = None) -> dict:
        storage_state_path = self.account_manager.get_storage_state_path(user_id, platform)
        if not storage_state_path:
            return {"success": False, "error": "未登录，请先扫码登录"}

        if not post_url and post_id:
            post_url = f"https://xueqiu.com/a/{post_id}"
        if not post_url:
            return {"success": False, "error": "post_url or post_id is required"}

        pw = None
        browser = None
        try:
            pw, browser, context, page = await self._setup_browser(storage_state_path)

            comment_api_result = {}

            async def on_response(response):
                url = response.url
                method = response.request.method
                if method == "POST" and any(kw in url.lower() for kw in ["comment", "reply"]):
                    logger.info(f"Comment API: {url} -> {response.status}")
                    comment_api_result["url"] = url
                    comment_api_result["status"] = response.status
                    try:
                        comment_api_result["body"] = await response.text()
                    except Exception:
                        pass

            page.on("response", on_response)

            logger.info("Step 1: Navigate to homepage")
            await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)
            await self._remove_waf(page)

            login_btn = await page.query_selector("text=立即登录/注册")
            if login_btn and await login_btn.is_visible():
                await self._cleanup(pw, browser)
                return {"success": False, "error": "登录已过期，请重新扫码登录"}

            search_query = post_title if post_title else str(post_id)
            if search_query and search_query != str(post_id):
                import re
                search_query = re.sub(r'<[^>]+>', '', search_query)
                search_query = re.sub(r'^[#*\s]+', '', search_query)
                search_query = search_query.strip()[:50]
            logger.info(f"Step 2: Search for '{search_query}'")

            search_box = await page.query_selector('input[placeholder*="搜索"]')
            if not search_box:
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到搜索框"}

            await search_box.click()
            await page.wait_for_timeout(500)
            await search_box.fill(search_query)
            await page.wait_for_timeout(1000)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(5000)
            await self._remove_waf(page)

            await page.screenshot(path="/tmp/xq_comment_search_results.png", full_page=True)

            logger.info(f"Step 3: Find post {post_id} in search results")
            found = await page.evaluate("""(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                return !!link;
            }""", post_id)

            if not found:
                logger.info("Post not in initial view, scrolling to load more...")
                for i in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)

                    found = await page.evaluate("""(postId) => {
                        const link = document.querySelector(`a[href*="${postId}"]`);
                        return !!link;
                    }""", post_id)

                    if found:
                        logger.info(f"Post found after {i+1} scrolls")
                        break

            if not found:
                await page.screenshot(path="/tmp/xq_comment_post_not_found.png", full_page=True)
                await self._cleanup(pw, browser)
                return {"success": False, "error": f"未找到帖子 {post_id}"}

            logger.info("Step 4: Click 讨论 button")
            scroll_result = await page.evaluate("""(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return {error: 'link not found'};
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
                return {error: '讨论 button not found'};
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

            logger.info("Step 5: Find comment editor")
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

            if not editor_in_comment or not editor_in_comment.get("found"):
                await page.screenshot(path="/tmp/xq_comment_no_editor.png", full_page=True)
                await self._cleanup(pw, browser)
                return {"success": False, "error": "未找到评论编辑器"}

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
                else:
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": "未找到评论提交按钮"}
            else:
                try:
                    await submit_btn.click(force=True, timeout=5000)
                except Exception:
                    await submit_btn.evaluate("e => e.click()")

            await page.wait_for_timeout(5000)

            await self._save_cookies(user_id, platform, context)
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
