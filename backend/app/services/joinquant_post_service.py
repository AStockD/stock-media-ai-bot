"""JoinQuant post creation service with two-phase CAPTCHA support."""
import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from playwright.async_api import async_playwright, Browser

from app.services.account_manager import AccountManager
from app.services.jq_captcha_service import JoinQuantCaptchaService
from app.services.jq_captcha_solver import find_gap_x as shared_find_gap_x, generate_trajectory as shared_generate_trajectory

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

JQ_EDIT_URL = "https://www.joinquant.com/view/community/edit?postType=edit"
SESSION_TIMEOUT = 120


@dataclass
class PostSession:
    user_id: int
    platform: str
    pw_instance: object = None
    browser: Optional[Browser] = None
    context: object = None
    page: object = None
    captcha_data: Optional[dict] = field(default=None)
    created_at: float = field(default_factory=time.time)


_sessions: Dict[int, PostSession] = {}


def _cleanup_expired_sessions():
    now = time.time()
    expired = [uid for uid, s in _sessions.items() if now - s.created_at > SESSION_TIMEOUT]
    for uid in expired:
        session = _sessions.pop(uid)
        logger.info(f"Cleaning up expired post session for user {uid}")
        asyncio.ensure_future(_cleanup_post_session(session))


async def _cleanup_post_session(session: PostSession):
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


class JoinQuantPostService:
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

    async def _save_cookies(self, user_id, platform, context):
        new_storage = await context.storage_state()
        cookies_list = await context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies_list}
        self.account_manager.save_cookies(user_id, platform, cookies_dict, new_storage)

    async def _upload_image(self, page, image_path: str = None, image_url: str = None) -> dict:
        """Upload image to JoinQuant editor via MarkKook insert image dialog.

        Supports local file path or URL (downloaded to temp file first).
        Returns upload result dict from the API.
        """
        import tempfile
        import httpx

        local_path = image_path
        tmp_path = None

        if image_url and not image_path:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.get(image_url)
                    resp.raise_for_status()
                suffix = ".png"
                if ".jpg" in image_url or ".jpeg" in image_url:
                    suffix = ".jpg"
                elif ".gif" in image_url:
                    suffix = ".gif"
                elif ".webp" in image_url:
                    suffix = ".webp"
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(resp.content)
                tmp.close()
                tmp_path = tmp.name
                local_path = tmp_path
                logger.info(f"Downloaded image from URL to {local_path} ({len(resp.content)} bytes)")
            except Exception as e:
                logger.error(f"Failed to download image from URL: {e}")
                return {"success": False, "error": str(e)}

        if not local_path:
            return {"success": False, "error": "No image path or URL provided"}

        if not Path(local_path).exists():
            logger.warning(f"Image file not found: {local_path}")
            return {"success": False, "error": f"File not found: {local_path}"}

        upload_result = {}

        async def on_upload_response(response):
            if "common/img/upload" in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    upload_result.update(body)
                    logger.info(f"Image upload API: {response.status} -> {body.get('url', '')[:100]}")
                except Exception:
                    pass

        page.on("response", on_upload_response)

        try:
            debug_info = await page.evaluate("""() => {
                const inputs = document.querySelectorAll('input[type="file"]');
                return Array.from(inputs).map(i => ({
                    accept: i.accept,
                    name: i.name,
                    class: i.className,
                    parentClass: i.parentElement?.className || '',
                    hidden: i.closest('.ck-hidden') !== null || getComputedStyle(i).display === 'none',
                }));
            }""")
            logger.info(f"File inputs on page: {json.dumps(debug_info)}")

            opened = await page.evaluate("""() => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const vm = editEl && editEl.__vue__;
                const mk = vm?.$refs?.MarkKookComponent;
                if (mk) {
                    mk.showInsertImgDialog();
                    return 'markkook_dialog_opened';
                }
                return 'no_markkook';
            }""")
            logger.info(f"Dialog open result: {opened}")
            await page.wait_for_timeout(1500)

            dialog_file = page.locator('.el-dialog__wrapper:visible input[type="file"]')
            count = await dialog_file.count()
            logger.info(f"Visible dialog file inputs: {count}")

            if count > 0:
                await dialog_file.first.set_input_files(local_path)
                logger.info("File set on dialog input, waiting for upload...")
                await page.wait_for_timeout(3000)

                if upload_result:
                    confirm_clicked = await page.evaluate("""() => {
                        const wrappers = document.querySelectorAll('.el-dialog__wrapper');
                        for (const w of wrappers) {
                            if (w.style.display === 'none') continue;
                            const title = w.querySelector('.el-dialog__title')?.textContent || '';
                            if (title.includes('图片')) {
                                const confirmBtn = w.querySelector('.dialog-footer .jq-c-button_primary');
                                if (confirmBtn) { confirmBtn.click(); return 'clicked'; }
                            }
                        }
                        return 'no_confirm_btn';
                    }""")
                    logger.info(f"Confirm button: {confirm_clicked}")
                    await page.wait_for_timeout(1000)
                    return {"success": True, **upload_result}

            ck_file = page.locator('.ck-file-dialog-button input[type="file"]')
            ck_count = await ck_file.count()
            logger.info(f"CKEditor file inputs: {ck_count}")
            if ck_count > 0:
                await ck_file.first.set_input_files(local_path)
                await page.wait_for_timeout(3000)
                if upload_result:
                    return {"success": True, **upload_result}

            logger.warning("No file input found for image upload")
            return {"success": False, "error": "No file input found in editor"}
        except Exception as e:
            logger.error(f"Image upload failed: {e}")
            return {"success": False, "error": str(e)}
        finally:
            page.remove_listener("response", on_upload_response)
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    async def _solve_captcha_auto(self, page, pre_captured_data: dict = None) -> str | None:
        """Auto-solve CAPTCHA using OpenCV. Returns token or None."""
        import random

        api_response_data = dict(pre_captured_data) if pre_captured_data else {}

        async def on_captcha_response(response):
            if "verifyCode/captchar" in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    if body.get("code") == "00000":
                        api_response_data.update(body.get("data", {}))
                except Exception:
                    pass

        page.on("response", on_captcha_response)

        captcha_data = await page.evaluate("""() => {
            const editEl = document.querySelector('.jq-comunity-edit');
            const editVm = editEl && editEl.__vue__;
            if (!editVm) return null;
            const captchaDialog = editVm.$refs && editVm.$refs.validCodeDiloag;
            if (captchaDialog && captchaDialog.$data) {
                const data = captchaDialog.$data;
                if (data.bgImg && data.hqImg) {
                    return { bgImg: data.bgImg, hqImg: data.hqImg, bgImgW: data.bgImgW || 320 };
                }
            }
            return null;
        }""")

        if not captcha_data:
            logger.warning("No CAPTCHA data in Vue component, trying API call...")
            captcha_data = await page.evaluate("""async () => {
                return new Promise((resolve, reject) => {
                    const editEl = document.querySelector('.jq-comunity-edit');
                    const editVm = editEl && editEl.__vue__;
                    if (!editVm) { reject(new Error('Vue instance not found')); return; }
                    if (!editVm.$axios) { reject(new Error('No axios available')); return; }
                    editVm.$axios.post('/common/verifyCode/captchar').then(resp => {
                        if (resp.data && resp.data.code === '00000') {
                            resolve(resp.data.data);
                        } else {
                            reject(new Error('CAPTCHA request failed'));
                        }
                    }).catch(err => reject(new Error(err.message || 'unknown')));
                });
            }""")

        await page.wait_for_timeout(1000)
        page.remove_listener("response", on_captcha_response)

        if api_response_data:
            logger.info(f"CAPTCHA API intercepted: bgImgW={api_response_data.get('bgImgW')}, blockW={api_response_data.get('blockW')}")
            if not captcha_data:
                captcha_data = api_response_data
            else:
                captcha_data["point"] = api_response_data.get("point", [])
                captcha_data["blockW"] = api_response_data.get("blockW", 11)
                captcha_data["blockH"] = api_response_data.get("blockH", 71)
                if api_response_data.get("bgImgW"):
                    captcha_data["bgImgW"] = api_response_data["bgImgW"]

        if not captcha_data:
            logger.error("No CAPTCHA data received")
            return None

        bg_img = captcha_data.get("bgImg", "")
        hq_img = captcha_data.get("hqImg", "")
        bg_img_w = captcha_data.get("bgImgW", 320)

        if not bg_img or not hq_img:
            logger.error("Missing CAPTCHA images")
            return None

        gap_x = shared_find_gap_x(bg_img, hq_img)
        logger.info(f"CAPTCHA raw gap X: {gap_x}, bgImgW: {bg_img_w}")

        point_grid = captcha_data.get("point", [])
        block_w = captcha_data.get("blockW", 11)
        if point_grid and block_w > 0:
            grid_x_values = sorted(set(int(p[0]) for p in point_grid))
            if grid_x_values:
                abs_grid_x = sorted(set(abs(x) for x in grid_x_values))
                if abs_gap_x := [x for x in abs_grid_x if x > 0]:
                    closest = min(abs_gap_x, key=lambda gx: abs(gx - gap_x))
                    logger.info(f"Snapped gap X from {gap_x} to grid point {closest}")
                    gap_x = closest

        scale = 1.0
        display_info = await page.evaluate("""() => {
            const dragContainer = document.querySelector('.valid-code__drag, [class*="drag"]:not([class*="handle"]):not([class*="bg"]):not([class*="text"])');
            if (dragContainer) return {type: 'drag', width: dragContainer.clientWidth};
            const dialog = document.querySelector('.valid-code-dialog, [class*="validCode"], [class*="captcha"], .el-dialog');
            if (dialog) {
                const imgs = dialog.querySelectorAll('img');
                for (const img of imgs) {
                    if (img.clientWidth > 100) return {type: 'img', width: img.clientWidth};
                }
            }
            return null;
        }""")

        if display_info and display_info.get('width'):
            display_w = display_info['width']
            if bg_img_w and bg_img_w > 0:
                scale = display_w / bg_img_w
                logger.info(f"CAPTCHA scale: {scale:.3f} (display={display_w}, bgImgW={bg_img_w})")

        target_x = int(gap_x * scale)

        slider = await page.query_selector('.valid-code__drag-handle, .handler, [class*="drag-handle"], [class*="slide-handle"]')
        if not slider:
            logger.error("Slider element not found")
            return None

        slider_box = await slider.bounding_box()
        if not slider_box:
            logger.error("Slider bounding box not available")
            return None

        start_x = slider_box["x"] + slider_box["width"] / 2
        start_y = slider_box["y"] + slider_box["height"] / 2

        trajectory = shared_generate_trajectory(target_x)
        logger.info(f"Starting drag from x={start_x:.1f}, target_x={target_x}")

        validation_result = {}
        async def on_validation_response(response):
            if "verifyCode/validate" in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    validation_result["status"] = response.status
                    validation_result["body"] = body
                    logger.info(f"CAPTCHA validation API: {response.status} -> {body}")
                except Exception as e:
                    logger.error(f"Failed to parse validation response: {e}")

        page.on("response", on_validation_response)

        import random as rnd
        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(rnd.randint(100, 300))
        await page.mouse.down()
        await page.wait_for_timeout(rnd.randint(50, 150))

        for i, pt in enumerate(trajectory):
            wait = rnd.uniform(0.008, 0.025)
            await asyncio.sleep(wait)
            curr_x = start_x + pt["x"]
            curr_y = start_y + rnd.uniform(-2, 2)
            await page.mouse.move(curr_x, curr_y)

        await page.wait_for_timeout(rnd.randint(50, 150))
        await page.mouse.up()
        await page.wait_for_timeout(3000)

        page.remove_listener("response", on_validation_response)

        token = await page.evaluate("""() => {
            const editEl = document.querySelector('.jq-comunity-edit');
            const editVm = editEl && editEl.__vue__;
            if (!editVm) return null;
            return editVm.validCodetoken || editVm.submitCode || null;
        }""")

        if token:
            logger.info(f"CAPTCHA solved, token: {token[:20]}...")
            return token

        logger.warning("CAPTCHA solve attempt failed, no token received")
        return None

    async def _resubmit_with_token(self, page, token: str) -> str:
        """Set token in Vue and resubmit the post. Returns result description."""
        resubmit = await page.evaluate("""(token) => {
            const editEl = document.querySelector('.jq-comunity-edit');
            const editVm = editEl && editEl.__vue__;
            if (!editVm) return 'no vue';
            editVm.$data.validCodetoken = token;
            editVm.$data.submitCode = token;
            if (editVm.submitCodePostInfo) {
                editVm.submitCodePostInfo.submitCode = token;
            }
            try {
                editVm.submitCodeDialog();
                return 'submitted';
            } catch(e) {
                try {
                    editVm.releaseEdit();
                    return 'releaseEdit recalled';
                } catch(e2) {
                    return 'error: ' + e2.message;
                }
            }
        }""", token)
        logger.info(f"Resubmit result: {resubmit}")
        return resubmit

    async def _parse_submit_result(self, submit_result: dict) -> dict:
        """Parse the post submit API response into a result dict."""
        if submit_result.get("status") == 200:
            try:
                body = json.loads(submit_result.get("body", "{}"))
                if body.get("status") == "0" or body.get("code") == "00000":
                    post_id = body.get("data", {}).get("postId") or body.get("data", {}).get("id", "")
                    return {"success": True, "message": "发帖成功", "post_id": str(post_id)}
                else:
                    msg = body.get("msg", "") or body.get("message", "")
                    if "验证" in msg or "验证码" in msg:
                        return {"success": False, "error": "验证码未通过"}
                    return {"success": False, "error": msg or f"发帖失败: {body}"}
            except Exception:
                return {"success": True, "message": "发帖请求已提交"}
        elif submit_result:
            return {"success": False, "error": f"API status: {submit_result.get('status')}"}
        else:
            return {"success": False, "error": "未检测到发帖提交请求"}

    async def start_post(
        self,
        user_id: int,
        content: str,
        title: str = None,
        platform: str = "joinquant",
        image_path: str = None,
        image_url: str = None,
    ) -> dict:
        """Phase 1: Fill form, submit, auto-solve CAPTCHA up to 3 times.

        Returns success result or {status: "captcha_required", captcha_data: {...}}.
        """
        _cleanup_expired_sessions()

        existing = _sessions.get(user_id)
        if existing:
            await _cleanup_post_session(existing)
            _sessions.pop(user_id, None)

        storage_state_path = self.account_manager.get_storage_state_path(user_id, platform)
        if not storage_state_path:
            return {"success": False, "error": "未登录，请先登录聚宽"}

        pw = None
        browser = None
        try:
            pw, browser, context, page = await self._setup_browser(storage_state_path)

            submit_result = {}
            async def on_response(response):
                url = response.url
                if "community/post/submit" in url and response.request.method == "POST":
                    try:
                        body = await response.text()
                        submit_result["status"] = response.status
                        submit_result["body"] = body
                        logger.info(f"Post submit API: {response.status} -> {body[:200]}")
                    except Exception:
                        pass

            page.on("response", on_response)

            logger.info(f"Opening editor page: {JQ_EDIT_URL}")
            await page.goto(JQ_EDIT_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            is_logged_in = await page.evaluate("""() => {
                const loginBtn = document.querySelector('.jq-header-login') ||
                                 document.querySelector('a[href*="login"]');
                return !loginBtn || loginBtn.offsetParent === null;
            }""")

            if not is_logged_in:
                await _cleanup_post_session(PostSession(user_id=user_id, platform=platform, pw_instance=pw, browser=browser))
                return {"success": False, "error": "登录已过期，请重新登录聚宽"}

            if not title:
                lines = content.strip().split("\n")
                first_line = lines[0].strip().lstrip("#").strip()
                title = first_line[:50] if first_line else f"股票分析 {time.strftime('%Y-%m-%d')}"
                if len(lines) > 1:
                    content = "\n".join(lines[1:]).strip()

            title_filled = await page.evaluate("""(title) => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const editVm = editEl && editEl.__vue__;
                if (!editVm) return 'no vue';
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                const titleInput = document.querySelector('input[placeholder*="标题"]') ||
                                   document.querySelector('.edit-title input') ||
                                   document.querySelector('[class*="title"] input');
                if (titleInput) {
                    nativeInputValueSetter.call(titleInput, title);
                    titleInput.dispatchEvent(new Event('input', { bubbles: true }));
                    titleInput.dispatchEvent(new Event('change', { bubbles: true }));
                }
                editVm.$data.articleTitle = title;
                return 'ok';
            }""", title)
            logger.info(f"Title fill result: {title_filled}")

            content_filled = await page.evaluate("""(content) => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const editVm = editEl && editEl.__vue__;
                if (!editVm) return 'no vue';
                const mk = editVm.$refs.MarkKookComponent;
                if (mk && mk.$el) {
                    const ta = mk.$el.querySelector('textarea');
                    if (ta) {
                        const ns = Object.getOwnPropertyDescriptor(
                            window.HTMLTextAreaElement.prototype, 'value'
                        ).set;
                        ns.call(ta, content);
                        ta.dispatchEvent(new Event('input', { bubbles: true }));
                        ta.dispatchEvent(new Event('change', { bubbles: true }));
                        return 'ok_via_markkook';
                    }
                }
                const textarea = document.querySelector('textarea');
                if (textarea) {
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    nativeSetter.call(textarea, content);
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                }
                editVm.$data.markDwenContent = content;
                return 'ok_via_fallback';
            }""", content)
            logger.info(f"Content fill result: {content_filled}")

            await page.wait_for_timeout(1000)

            if image_path or image_url:
                upload_result = await self._upload_image(page, image_path, image_url)
                logger.info(f"Image upload result: {upload_result}")

            tag_selected = await page.evaluate("""() => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const editVm = editEl && editEl.__vue__;
                if (!editVm) return 'no vue';
                if (editVm.$data.userChosedTags && editVm.$data.userChosedTags.length > 0) {
                    return 'already_set:' + JSON.stringify(editVm.$data.userChosedTags);
                }
                const allTags = editVm.$data.allUserTags || [];
                const preferred = ['讨论', '分享', '心得', '研报分享'];
                let chosen = null;
                for (const pref of preferred) {
                    for (const t of allTags) {
                        if (t.name === pref) { chosen = t; break; }
                    }
                    if (chosen) break;
                }
                if (!chosen && allTags.length > 0) chosen = allTags[0];
                if (chosen) {
                    chosen.active = true;
                    editVm.$data.userChosedTags = [chosen];
                    return 'set_via_vue:' + chosen.name + '(tagId=' + chosen.tagId + ')';
                }
                return 'no_tags_available, allUserTags count=' + allTags.length;
            }""")
            logger.info(f"Tag selection: {tag_selected}")

            await page.wait_for_timeout(500)

            submit_result.clear()

            first_captcha_data = {}
            async def on_first_captcha_response(response):
                if "verifyCode/captchar" in response.url and response.request.method == "POST":
                    try:
                        body = await response.json()
                        if body.get("code") == "00000":
                            first_captcha_data.update(body.get("data", {}))
                            non_img = {k: v for k, v in body["data"].items() if k not in ('bgImg', 'hqImg')}
                            logger.info(f"First CAPTCHA API captured: {non_img}")
                    except Exception:
                        pass

            page.on("response", on_first_captcha_response)

            post_result = await page.evaluate("""() => {
                return new Promise((resolve) => {
                    const editEl = document.querySelector('.jq-comunity-edit');
                    const editVm = editEl && editEl.__vue__;
                    if (!editVm) { resolve({error: 'no vue'}); return; }
                    try {
                        editVm.releaseEdit();
                        resolve({status: 'releaseEdit_called'});
                    } catch(e) {
                        resolve({status: 'releaseEdit_error', error: e.message});
                    }
                });
            }""")
            logger.info(f"Post submission result: {post_result}")

            await page.wait_for_timeout(3000)

            captcha_needed = await page.evaluate("""() => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const editVm = editEl && editEl.__vue__;
                if (!editVm) return false;
                return !!(editVm.$data && editVm.$data.validCodeDialogVisible);
            }""")

            if not captcha_needed:
                page.remove_listener("response", on_first_captcha_response)
                await self._save_cookies(user_id, platform, context)
                await _cleanup_post_session(PostSession(user_id=user_id, platform=platform, pw_instance=pw, browser=browser))
                return await self._parse_submit_result(submit_result)

            logger.info("CAPTCHA dialog detected, attempting auto-solve...")
            await page.wait_for_timeout(2000)

            token = None
            for attempt in range(3):
                logger.info(f"CAPTCHA solve attempt {attempt + 1}/3")
                token = await self._solve_captcha_auto(
                    page,
                    pre_captured_data=first_captcha_data if attempt == 0 else None,
                )

                if token:
                    logger.info(f"Got CAPTCHA token: {token[:20]}...")
                    await self._resubmit_with_token(page, token)
                    await page.wait_for_timeout(5000)

                    if submit_result.get("status") == 200:
                        break
                else:
                    logger.warning(f"CAPTCHA attempt {attempt + 1} failed")
                    if attempt < 2:
                        refresh = await page.evaluate("""() => {
                            const editEl = document.querySelector('.jq-comunity-edit');
                            const editVm = editEl && editEl.__vue__;
                            if (!editVm) return 'no vue';
                            const captchaVm = editVm.$refs && editVm.$refs.validCodeDiloag;
                            if (captchaVm && captchaVm.getCaptchar) {
                                captchaVm.getCaptchar();
                                return 'refreshed';
                            }
                            return 'no captcha ref';
                        }""")
                        logger.info(f"CAPTCHA refresh: {refresh}")
                        await page.wait_for_timeout(2000)

            page.remove_listener("response", on_first_captcha_response)

            if token and submit_result.get("status") == 200:
                await self._save_cookies(user_id, platform, context)
                await _cleanup_post_session(PostSession(user_id=user_id, platform=platform, pw_instance=pw, browser=browser))
                return await self._parse_submit_result(submit_result)

            logger.info("Automatic CAPTCHA failed, switching to manual mode")
            captcha_data = await JoinQuantCaptchaService.extract_post_captcha_data(page, first_captcha_data)

            session = PostSession(
                user_id=user_id,
                platform=platform,
                pw_instance=pw,
                browser=browser,
                context=context,
                page=page,
                captcha_data=captcha_data,
            )
            _sessions[user_id] = session

            return {
                "success": False,
                "status": "captcha_required",
                "captcha_data": captcha_data,
            }

        except Exception as e:
            logger.error(f"JoinQuant post failed: {e}", exc_info=True)
            if pw or browser:
                await _cleanup_post_session(PostSession(user_id=user_id, platform=platform, pw_instance=pw, browser=browser))
            return {"success": False, "error": str(e)}
        finally:
            if storage_state_path:
                try:
                    Path(storage_state_path).unlink(missing_ok=True)
                    Path(storage_state_path).parent.rmdir()
                except Exception:
                    pass

    async def validate_captcha(self, user_id: int, axis_x: int) -> dict:
        """Phase 2: Manual CAPTCHA validation for post.

        Uses shared service to drag slider, then resubmits post on success.
        On failure, refreshes CAPTCHA and returns new data for retry.
        """
        session = _sessions.get(user_id)
        if not session or not session.page:
            return {"success": False, "error": "没有待验证的发帖会话"}

        page = session.page
        context = session.context
        captcha_data = session.captcha_data or {}
        expected_width = captcha_data.get("bgImgW", 320)

        try:
            drag_result = await JoinQuantCaptchaService.simulate_drag(page, axis_x, expected_width)

            if drag_result["success"]:
                token = await page.evaluate("""() => {
                    const editEl = document.querySelector('.jq-comunity-edit');
                    const editVm = editEl && editEl.__vue__;
                    if (!editVm) return null;
                    return editVm.validCodetoken || editVm.submitCode || null;
                }""")

                if not token:
                    _sessions.pop(user_id, None)
                    await _cleanup_post_session(session)
                    return {"success": False, "error": "验证码通过但未获取到token"}

                submit_result = {}
                async def on_response(response):
                    if "community/post/submit" in response.url and response.request.method == "POST":
                        try:
                            body = await response.text()
                            submit_result["status"] = response.status
                            submit_result["body"] = body
                            logger.info(f"Post submit API (manual): {response.status} -> {body[:200]}")
                        except Exception:
                            pass

                page.on("response", on_response)
                await self._resubmit_with_token(page, token)
                await page.wait_for_timeout(5000)
                page.remove_listener("response", on_response)

                await self._save_cookies(user_id, session.platform, context)
                _sessions.pop(user_id, None)
                await _cleanup_post_session(session)

                return await self._parse_submit_result(submit_result)
            else:
                body = drag_result.get("body", {})
                data = body.get("data", {})
                message = data.get("message", "验证码验证错误")
                logger.warning(f"Manual CAPTCHA failed: {message}")

                async def refresh_fn():
                    await page.evaluate("""() => {
                        const editEl = document.querySelector('.jq-comunity-edit');
                        const editVm = editEl && editEl.__vue__;
                        if (!editVm) return;
                        const captchaVm = editVm.$refs && editVm.$refs.validCodeDiloag;
                        if (captchaVm && captchaVm.getCaptchar) {
                            captchaVm.getCaptchar();
                        }
                    }""")

                new_captcha = await JoinQuantCaptchaService.refresh(page, refresh_fn, {
                    "bgImgW": captcha_data.get("bgImgW", 320),
                    "bgImgH": captcha_data.get("bgImgH", 142),
                    "blockW": captcha_data.get("blockW", 11),
                    "blockH": captcha_data.get("blockH", 71),
                })

                if new_captcha:
                    session.captcha_data = new_captcha
                    return {
                        "success": False,
                        "status": "captcha_required",
                        "message": message,
                        "captcha_data": new_captcha,
                    }

                return {"success": False, "error": f"验证码错误: {message}"}

        except Exception as e:
            logger.error(f"Post CAPTCHA validation failed: {e}", exc_info=True)
            _sessions.pop(user_id, None)
            await _cleanup_post_session(session)
            return {"success": False, "error": str(e)}
