"""Xueqiu comment service - comment on posts from the home page timeline."""
import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser

from app.config import STORAGE_STATE_FILE

logger = logging.getLogger(__name__)


class XueqiuCommentService:
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

    async def create_comment(self, post_id: int, content: str, user_id: int = None, reply_to_comment_id: int = None) -> dict:
        if not STORAGE_STATE_FILE.exists():
            return {"success": False, "error": "未登录，请先扫码登录"}

        pw = None
        browser = None
        try:
            pw, browser, context, page = await self._setup_browser()

            comment_api_result = {}
            all_post_requests = []

            def on_request(request):
                url = request.url
                if request.method == 'POST' and 'reply.json' in url:
                    logger.info(f"CAPTURED reply.json request body: {request.post_data}")

            def on_response(response):
                url = response.url
                method = response.request.method
                if method == 'POST' and 'xueqiu.com' in url:
                    all_post_requests.append({"url": url, "status": response.status})
                    logger.info(f"POST request: {url} -> {response.status}")
                if response.request.method == 'POST' and any(kw in url.lower() for kw in ['comment', 'reply', 'statuses']):
                    logger.info(f"Comment API: {url} -> {response.status}")
                    comment_api_result["url"] = url
                    comment_api_result["status"] = response.status
                    try:
                        comment_api_result["body"] = response.text()
                    except Exception:
                        pass

            page.on("request", on_request)
            page.on("response", on_response)

            # Navigate to home page
            logger.info("Navigating to Xueqiu home page...")
            await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            # Check login
            login_btn = await page.query_selector('text=立即登录/注册')
            if login_btn and await login_btn.is_visible():
                await self._cleanup(pw, browser)
                return {"success": False, "error": "登录已过期，请重新扫码登录"}

            # Remove WAF
            try:
                await page.evaluate('''() => {
                    const all = document.querySelectorAll('[id*="waf"], [class*="waf"]');
                    all.forEach(e => e.remove());
                }''')
            except Exception:
                pass

            # Find the post's article element and its "讨论" button
            logger.info(f"Looking for post {post_id} in timeline...")

            # Scroll to the post and click the "讨论" button
            scroll_result = await page.evaluate('''(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return {error: 'post link not found'};

                // Walk up to find the article
                let article = link;
                while (article && article.tagName !== 'ARTICLE') {
                    article = article.parentElement;
                }
                if (!article) return {error: 'article not found'};

                // Find the "讨论" button within this article
                const controls = article.querySelectorAll('a.timeline__item__control');
                for (const ctrl of controls) {
                    const span = ctrl.querySelector('span');
                    if (span && span.textContent.trim() === '讨论') {
                        // Scroll it into view
                        ctrl.scrollIntoView({behavior: 'instant', block: 'center'});
                        const rect = ctrl.getBoundingClientRect();
                        return {
                            found: true,
                            x: rect.x + rect.width / 2,
                            y: rect.y + rect.height / 2,
                            w: rect.width,
                            h: rect.height,
                        };
                    }
                }
                return {error: '讨论 button not found in article'};
            }''', post_id)

            logger.info(f"Scroll result: {scroll_result}")

            if not scroll_result.get('found'):
                await page.screenshot(path="/tmp/xq_comment_no_btn.png")
                await self._cleanup(pw, browser)
                return {"success": False, "error": f"未找到讨论按钮: {scroll_result.get('error')}"}

            await page.wait_for_timeout(500)

            # Click the "讨论" button using Playwright's native click (CDP-level)
            logger.info("Clicking 讨论 button via Playwright locator...")
            try:
                # Find the article containing the post link, then find the 讨论 button
                article = page.locator(f'article:has(a[href*="{post_id}"])').first
                discuss_btn = article.locator('a.timeline__item__control:has(span:text("讨论"))')
                await discuss_btn.scroll_into_view_if_needed(timeout=5000)
                await page.wait_for_timeout(500)
                await discuss_btn.click(force=True, timeout=5000)
                logger.info("Playwright click on 讨论 succeeded")
            except Exception as e:
                logger.warning(f"Playwright locator click failed: {e}")
                # Fallback: try page.click with selector
                try:
                    await page.click(f'a[href*="{post_id}"] >> .. >> .. >> a.timeline__item__control:has-text("讨论")', force=True, timeout=5000)
                    logger.info("Fallback page.click succeeded")
                except Exception as e2:
                    logger.warning(f"Fallback click also failed: {e2}")
                    await page.screenshot(path="/tmp/xq_click_failed.png")
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": f"点击讨论按钮失败: {e}"}

            await page.wait_for_timeout(3000)

            await page.screenshot(path="/tmp/xq_after_discuss_click.png")

            # Check if the comment section appeared
            comment_section = await page.evaluate('''(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return {error: 'link not found'};
                let article = link;
                while (article && article.tagName !== 'ARTICLE') {
                    article = article.parentElement;
                }
                if (!article) return {error: 'article not found'};

                const commentDiv = article.querySelector('.timeline__item__comment');
                if (!commentDiv) return {error: 'comment div not found'};

                const rect = commentDiv.getBoundingClientRect();
                const html = commentDiv.innerHTML.substring(0, 500);
                const style = window.getComputedStyle(commentDiv);
                return {
                    display: style.display,
                    visibility: style.visibility,
                    height: style.height,
                    rect: {w: rect.width, h: rect.height},
                    html: html,
                    childCount: commentDiv.children.length,
                };
            }''', post_id)
            logger.info(f"Comment section after click: {comment_section}")

            # Remove WAF if it reappeared
            try:
                await page.evaluate('''() => {
                    const all = document.querySelectorAll('[id*="waf"], [class*="waf"]');
                    all.forEach(e => e.remove());
                }''')
            except Exception:
                pass

            if reply_to_comment_id:
                # Reply to a specific comment: find the comment and click its "回复" button
                logger.info(f"Looking for comment {reply_to_comment_id} to reply to...")

                reply_btn_info = await page.evaluate('''(args) => {
                    const {postId, commentId} = args;
                    const link = document.querySelector(`a[href*="${postId}"]`);
                    if (!link) return {error: 'post link not found'};
                    let article = link;
                    while (article && article.tagName !== 'ARTICLE') {
                        article = article.parentElement;
                    }
                    if (!article) return {error: 'article not found'};

                    const commentSection = article.querySelector('.timeline__item__comment');
                    if (!commentSection) return {error: 'comment section not found'};

                    // Look for the comment element - try various selectors
                    // Xueqiu comments often have links like /comment/{commentId} or data attributes
                    let targetComment = null;

                    // Try finding by href containing the comment ID
                    const commentLinks = commentSection.querySelectorAll(`a[href*="${commentId}"]`);
                    for (const cl of commentLinks) {
                        // Walk up to find the comment container
                        let container = cl;
                        for (let i = 0; i < 10; i++) {
                            container = container.parentElement;
                            if (!container || container === commentSection) break;
                            // Check if this container has a "回复" button
                            const replyBtn = container.querySelector('a[class*="reply"], span[class*="reply"], a:has(> span)');
                            if (replyBtn) {
                                const spans = replyBtn.querySelectorAll('span');
                                for (const s of spans) {
                                    if (s.textContent.trim() === '回复') {
                                        const rect = replyBtn.getBoundingClientRect();
                                        if (rect.width > 0 && rect.height > 0) {
                                            return {
                                                found: true,
                                                x: rect.x + rect.width / 2,
                                                y: rect.y + rect.height / 2,
                                                via: 'href-match'
                                            };
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Try finding by iterating all comment items
                    const allComments = commentSection.querySelectorAll('.comment__item, [class*="comment-item"], [class*="comment__item"]');
                    for (const item of allComments) {
                        const itemHtml = item.getAttribute('data-id') || item.id || '';
                        if (itemHtml.includes(String(commentId))) {
                            const replyBtn = item.querySelector('a[class*="reply"], [class*="reply-btn"]');
                            if (replyBtn) {
                                const rect = replyBtn.getBoundingClientRect();
                                return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2, via: 'data-id'};
                            }
                        }
                    }

                    // Dump the comment section structure for debugging
                    const structure = commentSection.innerHTML.substring(0, 2000);
                    return {error: 'reply button not found', structure: structure};
                }''', {'postId': post_id, 'commentId': reply_to_comment_id})

                logger.info(f"Reply button info: {reply_btn_info}")

                if reply_btn_info.get('found'):
                    x, y = reply_btn_info['x'], reply_btn_info['y']
                    logger.info(f"Clicking reply button at ({x}, {y})")
                    await page.mouse.click(x, y)
                    await page.wait_for_timeout(1000)
                else:
                    # Try using Playwright locator to find reply buttons
                    logger.info("JS approach failed, trying Playwright locator for reply button...")
                    try:
                        article_locator = page.locator(f'article:has(a[href*="{post_id}"])').first
                        comment_section_locator = article_locator.locator('.timeline__item__comment')

                        # Find all elements with text "回复" in the comment section
                        reply_elements = comment_section_locator.locator('span:text("回复")')
                        count = await reply_elements.count()
                        logger.info(f"Found {count} '回复' spans in comment section")

                        # We need to find the right one - for now, try to find near the target comment
                        # This is a fallback - the JS approach should work better
                        if count > 0:
                            # Click the first visible reply span's parent
                            for i in range(count):
                                span = reply_elements.nth(i)
                                if await span.is_visible():
                                    parent = span.locator('..')
                                    await parent.click(force=True, timeout=3000)
                                    logger.info(f"Clicked reply span parent (index {i})")
                                    break
                    except Exception as e:
                        logger.warning(f"Playwright reply button search failed: {e}")
                        await page.screenshot(path="/tmp/xq_reply_btn_not_found.png")
                        await self._cleanup(pw, browser)
                        return {"success": False, "error": f"未找到回复按钮: {reply_btn_info.get('error', '')}"}

                await page.screenshot(path="/tmp/xq_after_reply_click.png")

            else:
                # Top-level comment: ensure editor is NOT in reply mode
                logger.info("Top-level comment mode: checking editor state...")
                placeholder_check = await page.evaluate('''(postId) => {
                    const link = document.querySelector(`a[href*="${postId}"]`);
                    if (!link) return null;
                    let article = link;
                    while (article && article.tagName !== 'ARTICLE') {
                        article = article.parentElement;
                    }
                    if (!article) return null;
                    const commentSection = article.querySelector('.timeline__item__comment');
                    if (!commentSection) return null;
                    const placeholder = commentSection.querySelector('.fake-placeholder');
                    if (placeholder) {
                        return {text: placeholder.textContent.trim()};
                    }
                    return {text: ''};
                }''', post_id)
                logger.info(f"Placeholder check: {placeholder_check}")

                if placeholder_check and placeholder_check.get('text', '').startswith('回复@'):
                    logger.info("Editor is in reply mode, reloading page to reset...")
                    await page.goto("https://xueqiu.com", wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(5000)

                    # Remove WAF
                    try:
                        await page.evaluate('''() => {
                            const all = document.querySelectorAll('[id*="waf"], [class*="waf"]');
                            all.forEach(e => e.remove());
                        }''')
                    except Exception:
                        pass

                    # Re-find and click "讨论"
                    article = page.locator(f'article:has(a[href*="{post_id}"])').first
                    discuss_btn = article.locator('a.timeline__item__control:has(span:text("讨论"))')
                    await discuss_btn.scroll_into_view_if_needed(timeout=5000)
                    await page.wait_for_timeout(500)
                    await discuss_btn.click(force=True, timeout=5000)
                    await page.wait_for_timeout(3000)
                    logger.info("Re-opened comment section after page reload")

                    # Verify placeholder again
                    placeholder_check2 = await page.evaluate('''(postId) => {
                        const link = document.querySelector(`a[href*="${postId}"]`);
                        if (!link) return null;
                        let article = link;
                        while (article && article.tagName !== 'ARTICLE') {
                            article = article.parentElement;
                        }
                        if (!article) return null;
                        const commentSection = article.querySelector('.timeline__item__comment');
                        if (!commentSection) return null;
                        const placeholder = commentSection.querySelector('.fake-placeholder');
                        return placeholder ? {text: placeholder.textContent.trim()} : {text: ''};
                    }''', post_id)
                    logger.info(f"Placeholder after reset: {placeholder_check2}")

            # Find the comment input inside the opened comment section
            comment_editor = None

            # Look specifically in the comment section for this post
            editor_in_comment = await page.evaluate('''(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return null;
                let article = link;
                while (article && article.tagName !== 'ARTICLE') {
                    article = article.parentElement;
                }
                if (!article) return null;

                const commentSection = article.querySelector('.timeline__item__comment');
                if (!commentSection) return null;

                // Find the contenteditable div inside the comment editor
                const editor = commentSection.querySelector('.lite-editor__textarea [contenteditable="true"]');
                if (editor) {
                    const rect = editor.getBoundingClientRect();
                    return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2, w: rect.width, h: rect.height};
                }

                // Also try the fake-placeholder's parent
                const placeholder = commentSection.querySelector('.fake-placeholder');
                if (placeholder) {
                    const parent = placeholder.parentElement;
                    const rect = parent.getBoundingClientRect();
                    return {found: true, x: rect.x + rect.width/2, y: rect.y + rect.height/2, w: rect.width, h: rect.height, viaPlaceholder: true};
                }

                return {found: false};
            }''', post_id)

            logger.info(f"Editor in comment section: {editor_in_comment}")

            if editor_in_comment and editor_in_comment.get('found'):
                x, y = editor_in_comment['x'], editor_in_comment['y']
                logger.info(f"Clicking comment editor at ({x}, {y})")
                await page.mouse.click(x, y)
                await page.wait_for_timeout(500)
                comment_editor = True  # Mark as found

                # Check if the contenteditable div got focus
                active_tag = await page.evaluate('''() => {
                    const ae = document.activeElement;
                    return ae ? {tag: ae.tagName, class: ae.className, isEditable: ae.isContentEditable} : null;
                }''')
                logger.info(f"Active element after click: {active_tag}")

                # If focus didn't land on the contenteditable, try clicking directly on it
                if not active_tag or not active_tag.get('isEditable'):
                    logger.info("Focus not on editable element, trying direct click on contenteditable div")
                    ce_result = await page.evaluate('''(postId) => {
                        const link = document.querySelector(`a[href*="${postId}"]`);
                        if (!link) return null;
                        let article = link;
                        while (article && article.tagName !== 'ARTICLE') {
                            article = article.parentElement;
                        }
                        if (!article) return null;
                        const commentSection = article.querySelector('.timeline__item__comment');
                        if (!commentSection) return null;
                        const ce = commentSection.querySelector('[contenteditable="true"]');
                        if (ce) {
                            const rect = ce.getBoundingClientRect();
                            return {x: rect.x + rect.width/2, y: rect.y + rect.height/2, w: rect.width, h: rect.height};
                        }
                        return null;
                    }''', post_id)
                    if ce_result:
                        logger.info(f"Direct clicking contenteditable at ({ce_result['x']}, {ce_result['y']})")
                        await page.mouse.click(ce_result['x'], ce_result['y'])
                        await page.wait_for_timeout(500)
            else:
                # Fallback: try Playwright selectors
                for sel in ['.timeline__item__comment .lite-editor__textarea [contenteditable="true"]',
                            '.lite-editor--comment [contenteditable="true"]',
                            '.timeline__item__comment textarea']:
                    el = await page.query_selector(sel)
                    if el:
                        comment_editor = el
                        logger.info(f"Found comment editor with: {sel}")
                        break

            if not comment_editor:
                await page.screenshot(path="/tmp/xq_no_comment_input.png")
                await self._cleanup(pw, browser)
                return {"success": False, "error": "点击讨论按钮后未找到评论输入框"}

            # Type the comment
            await page.keyboard.type(content, delay=30)
            await page.wait_for_timeout(500)

            # Verify text was typed
            typed_check = await page.evaluate('''(postId) => {
                const link = document.querySelector(`a[href*="${postId}"]`);
                if (!link) return {error: 'link not found'};
                let article = link;
                while (article && article.tagName !== 'ARTICLE') {
                    article = article.parentElement;
                }
                if (!article) return {error: 'article not found'};
                const commentSection = article.querySelector('.timeline__item__comment');
                if (!commentSection) return {error: 'comment section not found'};
                const ce = commentSection.querySelector('[contenteditable="true"]');
                if (!ce) return {error: 'contenteditable not found'};
                return {text: ce.textContent, html: ce.innerHTML.substring(0, 300)};
            }''', post_id)
            logger.info(f"Typed text check: {typed_check}")

            # If text wasn't typed via keyboard, try setting it via JS
            if not typed_check.get('text') or typed_check.get('text', '').strip() == '':
                logger.info("Keyboard typing didn't work, trying JS approach")
                await page.evaluate('''(args) => {
                    const {postId, text} = args;
                    const link = document.querySelector(`a[href*="${postId}"]`);
                    if (!link) return;
                    let article = link;
                    while (article && article.tagName !== 'ARTICLE') {
                        article = article.parentElement;
                    }
                    if (!article) return;
                    const commentSection = article.querySelector('.timeline__item__comment');
                    if (!commentSection) return;
                    const ce = commentSection.querySelector('[contenteditable="true"]');
                    if (!ce) return;
                    ce.focus();
                    ce.textContent = text;
                    ce.dispatchEvent(new Event('input', {bubbles: true}));
                    ce.dispatchEvent(new Event('change', {bubbles: true}));
                }''', {'postId': post_id, 'text': content})
                await page.wait_for_timeout(500)

                typed_check2 = await page.evaluate('''(postId) => {
                    const link = document.querySelector(`a[href*="${postId}"]`);
                    if (!link) return null;
                    let article = link;
                    while (article && article.tagName !== 'ARTICLE') {
                        article = article.parentElement;
                    }
                    if (!article) return null;
                    const commentSection = article.querySelector('.timeline__item__comment');
                    if (!commentSection) return null;
                    const ce = commentSection.querySelector('[contenteditable="true"]');
                    if (!ce) return null;
                    return {text: ce.textContent};
                }''', post_id)
                logger.info(f"After JS typing: {typed_check2}")

            await page.screenshot(path="/tmp/xq_comment_typed.png")

            # Find and click submit button within the comment section
            submit_btn = None

            # Use Playwright locator to find the visible submit button
            try:
                article_locator = page.locator(f'article:has(a[href*="{post_id}"])').first
                submit_locator = article_locator.locator('a.lite-editor__submit:text("发布")')
                count = await submit_locator.count()
                for i in range(count):
                    btn = submit_locator.nth(i)
                    if await btn.is_visible():
                        submit_btn = btn
                        logger.info(f"Found submit button via locator (index {i})")
                        break
            except Exception as e:
                logger.warning(f"Locator approach failed: {e}")

            mode_label = "回复" if reply_to_comment_id else "评论"

            if not submit_btn:
                # Fallback: find via JS and click by coordinates
                submit_coords = await page.evaluate('''(postId) => {
                    const link = document.querySelector(`a[href*="${postId}"]`);
                    if (!link) return null;
                    let article = link;
                    while (article && article.tagName !== 'ARTICLE') {
                        article = article.parentElement;
                    }
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
                }''', post_id)

                if submit_coords:
                    logger.info(f"Clicking submit at ({submit_coords['x']}, {submit_coords['y']})")
                    await page.mouse.click(submit_coords['x'], submit_coords['y'])
                    await page.wait_for_timeout(5000)
                    await page.screenshot(path="/tmp/xq_comment_done.png")
                    await context.storage_state(path=str(STORAGE_STATE_FILE))
                    await self._cleanup(pw, browser)
                    if comment_api_result.get("status") in (200, 201):
                        return {"success": True, "message": f"{mode_label}成功", "post_id": post_id, "reply_to_comment_id": reply_to_comment_id}
                    elif comment_api_result:
                        return {"success": False, "error": f"评论API: {comment_api_result.get('status')} {comment_api_result.get('body', '')[:200]}"}
                    else:
                        return {"success": False, "error": "未检测到评论API请求"}
                else:
                    await self._cleanup(pw, browser)
                    return {"success": False, "error": "未找到评论提交按钮"}

            if submit_btn:
                try:
                    await submit_btn.click(force=True, timeout=5000)
                    logger.info("Force click on submit succeeded")
                except Exception:
                    await submit_btn.evaluate('e => e.click()')

            await page.wait_for_timeout(5000)
            await page.screenshot(path="/tmp/xq_comment_done.png")

            await context.storage_state(path=str(STORAGE_STATE_FILE))
            await self._cleanup(pw, browser)

            if comment_api_result.get("status") in (200, 201):
                return {"success": True, "message": f"{mode_label}成功", "post_id": post_id, "reply_to_comment_id": reply_to_comment_id}
            elif comment_api_result:
                return {"success": False, "error": f"评论API: {comment_api_result.get('status')} {comment_api_result.get('body', '')[:200]}"}
            else:
                return {"success": False, "error": f"未检测到评论API请求. All POST requests: {all_post_requests}"}

        except Exception as e:
            logger.error(f"Failed to create comment: {e}", exc_info=True)
            await self._cleanup(pw, browser)
            return {"success": False, "error": str(e)}


comment_service: Optional[XueqiuCommentService] = None


def get_comment_service() -> XueqiuCommentService:
    global comment_service
    if comment_service is None:
        comment_service = XueqiuCommentService()
    return comment_service
