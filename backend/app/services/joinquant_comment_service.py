"""JoinQuant comment service - submits comments via Playwright + API."""
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

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class JoinQuantCommentService:
    def __init__(self, account_manager: AccountManager):
        self.account_manager = account_manager

    async def _setup_browser(self, storage_state_path: str):
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
        )
        context = await browser.new_context(
            storage_state=storage_state_path,
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
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

    async def create_comment(
        self,
        user_id: int,
        post_url: str = "",
        content: str = "",
        platform: str = "joinquant",
        post_id: str = None,
        post_title: str = None,
        reply_to_comment_id: int = None,
    ) -> dict:
        storage_state_path = self.account_manager.get_storage_state_path(user_id, platform)
        if not storage_state_path:
            return {"success": False, "error": "未登录，请先登录聚宽"}

        if not post_url and not post_id:
            return {"success": False, "error": "post_url or post_id is required"}

        if not post_url and post_id:
            post_url = f"https://www.joinquant.com/view/community/detail/{post_id}"

        pw = None
        browser = None
        try:
            pw, browser, context, page = await self._setup_browser(storage_state_path)

            api_result = {}

            async def on_response(response):
                url = response.url
                if "reply/submit" in url and response.request.method == "POST":
                    try:
                        body = await response.text()
                        api_result["status"] = response.status
                        api_result["body"] = body
                    except Exception:
                        pass

            page.on("response", on_response)

            logger.info(f"Navigating to post: {post_url}")
            await page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            got_it = await page.query_selector("button.jq-transition-notice-button")
            if got_it and await got_it.is_visible():
                await got_it.click()
                await page.wait_for_timeout(1000)

            textarea = await page.query_selector("textarea#inputer")
            if not textarea or not await textarea.is_visible():
                return {"success": False, "error": "未找到评论输入框"}

            await textarea.click()
            await textarea.fill(content)
            await page.wait_for_timeout(500)

            send_btn = await page.query_selector(".jq-c-reply_reply-post")
            if not send_btn:
                return {"success": False, "error": "未找到发送按钮"}

            has_disable = await send_btn.evaluate(
                'e => e.classList.contains("jq-c-reply_reply-post-disable")'
            )
            if has_disable:
                await send_btn.evaluate(
                    'e => e.classList.remove("jq-c-reply_reply-post-disable")'
                )

            await send_btn.click()
            await page.wait_for_timeout(3000)

            await self._save_cookies(user_id, platform, context)
            await self._cleanup(pw, browser)

            if api_result.get("status") == 200:
                try:
                    body = json.loads(api_result.get("body", "{}"))
                    if body.get("status") == "0":
                        return {"success": True, "message": "评论成功"}
                    else:
                        return {"success": False, "error": body.get("msg", "评论失败")}
                except Exception:
                    return {"success": True, "message": "评论已提交"}
            elif api_result:
                return {"success": False, "error": f"API status: {api_result.get('status')}"}
            else:
                return {"success": False, "error": "未检测到评论提交请求"}

        except Exception as e:
            logger.error(f"JoinQuant comment failed: {e}", exc_info=True)
            await self._cleanup(pw, browser)
            return {"success": False, "error": str(e)}
        finally:
            if storage_state_path:
                try:
                    Path(storage_state_path).unlink(missing_ok=True)
                    Path(storage_state_path).parent.rmdir()
                except Exception:
                    pass
