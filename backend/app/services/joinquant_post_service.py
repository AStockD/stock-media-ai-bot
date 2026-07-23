"""JoinQuant post creation service with OpenCV-based sliding CAPTCHA solving."""
import asyncio
import base64
import json
import logging
import math
import random
import time
from pathlib import Path

import cv2
import numpy as np
from playwright.async_api import async_playwright

from app.services.account_manager import AccountManager
from app.services.jq_captcha_solver import find_gap_x as shared_find_gap_x, generate_trajectory as shared_generate_trajectory

_captcha_state = {}

def set_captcha_state(user_id: int, state: dict):
    _captcha_state[user_id] = state

def get_captcha_state(user_id: int) -> dict:
    return _captcha_state.get(user_id, {})

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


def _fix_b64_padding(b64str: str) -> str:
    """Add missing padding to base64 string and strip data URI prefix."""
    b64str = b64str.strip()
    # Strip data URI prefix if present
    if b64str.startswith('data:'):
        b64str = b64str.split(',', 1)[1] if ',' in b64str else b64str
    missing_padding = len(b64str) % 4
    if missing_padding:
        b64str += '=' * (4 - missing_padding)
    return b64str


def _find_gap_x(bg_img_b64: str, piece_img_b64: str) -> int:
    """Use OpenCV to find the X offset of the puzzle gap in the background image."""
    bg_bytes = base64.b64decode(_fix_b64_padding(bg_img_b64))
    piece_bytes = base64.b64decode(_fix_b64_padding(piece_img_b64))

    bg_arr = np.frombuffer(bg_bytes, dtype=np.uint8)
    piece_arr = np.frombuffer(piece_bytes, dtype=np.uint8)

    bg = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)
    piece = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)

    if bg is None or piece is None:
        raise ValueError("Failed to decode CAPTCHA images")

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)

    ph, pw = piece_gray.shape[:2]
    
    bg_blur = cv2.GaussianBlur(bg_gray, (3, 3), 0)
    piece_blur = cv2.GaussianBlur(piece_gray, (3, 3), 0)
    
    result = cv2.matchTemplate(bg_blur, piece_blur, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    
    gap_x = max_loc[0]
    logger.info(f"Gap found via template matching at x={gap_x} (confidence={max_val:.3f}, piece_size={pw}x{ph})")
    return gap_x


def _generate_trajectory(distance: int) -> list[dict]:
    """Generate a human-like mouse drag trajectory."""
    points = []
    total_steps = random.randint(25, 40)
    overshoot = random.randint(5, 15) if distance > 50 else 0

    for i in range(total_steps):
        t = i / (total_steps - 1)
        progress = 1 - math.pow(1 - t, 3)
        x = distance * progress
        jitter = random.uniform(-1.5, 1.5) if 0.1 < t < 0.9 else 0
        points.append({"x": max(0, x + jitter), "t": t})

    if overshoot:
        for j in range(5):
            t = 1.0 + (j + 1) * 0.03
            x = distance + overshoot * (1 - j / 4)
            points.append({"x": x, "t": t})
        for j in range(5):
            t = 1.15 + (j + 1) * 0.03
            x = distance - overshoot * (j / 4) * 0.3
            points.append({"x": x, "t": t})

    points.append({"x": distance, "t": 1.3})
    return points


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

    async def _solve_captcha(self, page, pre_captured_data: dict = None, user_id: int = None) -> str | None:
        """Solve the sliding CAPTCHA and return the token, or None on failure."""
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
                    return {
                        bgImg: data.bgImg,
                        hqImg: data.hqImg,
                        bgImgW: data.bgImgW || 320
                    };
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

                    if (!editVm.$axios) {
                        reject(new Error('No axios available'));
                        return;
                    }

                    editVm.$axios.post('/common/verifyCode/captchar').then(resp => {
                        if (resp.data && resp.data.code === '00000') {
                            resolve(resp.data.data);
                        } else {
                            const msg = resp.data ? (resp.data.msg || JSON.stringify(resp.data)) : 'no response data';
                            reject(new Error('CAPTCHA request failed: ' + msg));
                        }
                    }).catch(err => {
                        const errMsg = err.message || err.toString() || 'unknown error';
                        reject(new Error('CAPTCHA axios error: ' + errMsg));
                    });
                });
            }""")

        await page.wait_for_timeout(1000)
        page.remove_listener("response", on_captcha_response)

        if api_response_data:
            logger.info(f"CAPTCHA API intercepted: bgImgW={api_response_data.get('bgImgW')}, blockW={api_response_data.get('blockW')}, point_count={len(api_response_data.get('point', []))}")
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

        logger.info(f"CAPTCHA data keys: {list(captcha_data.keys())}")
        non_img_data = {k: v for k, v in captcha_data.items() if k not in ('bgImg', 'hqImg')}
        logger.info(f"CAPTCHA non-image fields: {non_img_data}")

        bg_img = captcha_data.get("bgImg", "")
        hq_img = captcha_data.get("hqImg", "")
        bg_img_w = captcha_data.get("bgImgW", 320)

        if not bg_img or not hq_img:
            logger.error("Missing CAPTCHA images")
            return None

        try:
            import base64 as b64
            with open("/tmp/jq_captcha_bg.png", "wb") as f:
                f.write(b64.b64decode(_fix_b64_padding(bg_img)))
            with open("/tmp/jq_captcha_piece.png", "wb") as f:
                f.write(b64.b64decode(_fix_b64_padding(hq_img)))
            logger.info("Saved CAPTCHA images to /tmp/jq_captcha_*.png")
        except Exception as e:
            logger.warning(f"Failed to save CAPTCHA images: {e}")

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
                    logger.info(f"Snapped gap X from {gap_x} to grid point {closest} (grid: {abs_grid_x[:10]}...)")
                    gap_x = closest

        scale = 1.0
        display_info = await page.evaluate("""() => {
            const dragContainer = document.querySelector('.valid-code__drag, [class*="drag"]:not([class*="handle"]):not([class*="bg"]):not([class*="text"])');
            if (dragContainer) {
                return {type: 'drag', width: dragContainer.clientWidth};
            }
            const dialog = document.querySelector('.valid-code-dialog, [class*="validCode"], [class*="captcha"], .el-dialog');
            if (dialog) {
                const imgs = dialog.querySelectorAll('img');
                for (const img of imgs) {
                    if (img.clientWidth > 100) {
                        return {type: 'img', width: img.clientWidth};
                    }
                }
                const canvas = dialog.querySelector('canvas');
                if (canvas) {
                    return {type: 'canvas', width: canvas.clientWidth};
                }
            }
            return null;
        }""")
        logger.info(f"CAPTCHA display_info: {display_info}")
        
        if display_info and display_info.get('width'):
            display_w = display_info['width']
            actual_w = bg_img_w
            if actual_w and actual_w > 0:
                scale = display_w / actual_w
                logger.info(f"CAPTCHA scale: {scale:.3f} (display={display_w}, bgImgW={actual_w})")

        target_x = int(gap_x * scale)

        await page.screenshot(path="/tmp/jq_captcha_before_slider.png")

        slider_sel = '.valid-code__drag-handle, .handler, [class*="drag-handle"], [class*="slide-handle"]'
        slider = await page.query_selector(slider_sel)
        if not slider:
            slider_info = await page.evaluate("""() => {
                const dialog = document.querySelector('.valid-code-dialog, [class*="validCode"], [class*="captcha"]');
                if (!dialog) return {error: 'no_dialog'};
                
                const allEls = dialog.querySelectorAll('*');
                const slideEls = [];
                for (const el of allEls) {
                    const cls = el.className || '';
                    const tag = el.tagName.toLowerCase();
                    if (cls.includes('slide') || cls.includes('drag') || cls.includes('handler')) {
                        slideEls.push({
                            tag: tag,
                            class: cls,
                            id: el.id,
                            rect: el.getBoundingClientRect()
                        });
                    }
                }
                return {slide_elements: slideEls};
            }""")
            logger.info(f"Slider search result: {slider_info}")
            
            if slider_info and slider_info.get('slide_elements'):
                for el_info in slider_info['slide_elements']:
                    cls = el_info.get('class', '')
                    if 'handler' in cls or 'drag-handle' in cls or 'handle' in cls:
                        selector = el_info['tag']
                        if cls:
                            selector += '.' + cls.split()[0]
                        slider = await page.query_selector(selector)
                        if slider:
                            logger.info(f"Found slider with selector: {selector}")
                            break

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
        logger.info(f"Starting drag from x={start_x:.1f}, target_x={target_x}, trajectory points={len(trajectory)}")

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

        async def on_validation_request(request):
            if "verifyCode/validate" in request.url and request.method == "POST":
                try:
                    post_data = request.post_data
                    logger.info(f"CAPTCHA validation request: {post_data}")
                except Exception as e:
                    logger.error(f"Failed to get validation request data: {e}")

        page.on("response", on_validation_response)
        page.on("request", on_validation_request)

        logger.info(f"Starting mouse drag sequence")
        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(random.randint(100, 300))
        logger.info(f"Mouse down at ({start_x:.1f}, {start_y:.1f})")
        await page.mouse.down()
        await page.wait_for_timeout(random.randint(50, 150))

        prev_time = time.time()
        for i, pt in enumerate(trajectory):
            curr_time = time.time()
            dt = curr_time - prev_time
            wait = random.uniform(0.008, 0.025)
            await asyncio.sleep(wait)
            curr_x = start_x + pt["x"]
            curr_y = start_y + random.uniform(-2, 2)
            await page.mouse.move(curr_x, curr_y)
            prev_time = curr_time
            if i % 10 == 0:
                logger.info(f"Drag progress: {i}/{len(trajectory)}, x={curr_x:.1f}")

        logger.info(f"Drag complete, mouse up")
        await page.wait_for_timeout(random.randint(50, 150))
        await page.mouse.up()

        await page.wait_for_timeout(3000)

        page.remove_listener("response", on_validation_response)
        page.remove_listener("request", on_validation_request)

        logger.info(f"Validation result: {validation_result}")

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

    async def _solve_captcha_manual(self, page, captcha_data: dict, user_id: int) -> str | None:
        """Wait for manual CAPTCHA solving from frontend."""
        bg_img = captcha_data.get("bgImg", "")
        hq_img = captcha_data.get("hqImg", "")
        bg_img_w = captcha_data.get("bgImgW", 363)

        set_captcha_state(user_id, {
            "status": "waiting",
            "bgImg": bg_img,
            "hqImg": hq_img,
            "bgImgW": bg_img_w,
            "axisX": None,
        })
        logger.info(f"Waiting for manual CAPTCHA input from user {user_id}")

        for _ in range(120):
            await asyncio.sleep(1)
            state = get_captcha_state(user_id)
            if state.get("status") == "solved":
                break
        else:
            logger.error("Manual CAPTCHA timeout (120s)")
            set_captcha_state(user_id, {"status": "timeout"})
            return None

        state = get_captcha_state(user_id)
        manual_x = state.get("axisX")
        if manual_x is None:
            logger.error("No axisX received from manual input")
            return None

        logger.info(f"Manual CAPTCHA axisX: {manual_x}")

        slider_sel = '.valid-code__drag-handle, .handler'
        slider = await page.query_selector(slider_sel)
        if not slider:
            logger.error("Slider not found for manual solve")
            return None

        slider_box = await slider.bounding_box()
        if not slider_box:
            logger.error("Slider bounding box not available")
            return None

        start_x = slider_box["x"] + slider_box["width"] / 2
        start_y = slider_box["y"] + slider_box["height"] / 2
        target_x = int(manual_x)

        validation_result = {}
        async def on_validation_response(response):
            if "verifyCode/validate" in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    validation_result["body"] = body
                    logger.info(f"Manual CAPTCHA validation: {body}")
                except Exception:
                    pass

        page.on("response", on_validation_response)

        trajectory = shared_generate_trajectory(target_x)
        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(random.randint(100, 300))
        await page.mouse.down()
        await page.wait_for_timeout(random.randint(50, 150))

        for pt in trajectory:
            await asyncio.sleep(random.uniform(0.01, 0.025))
            curr_x = start_x + pt["x"]
            curr_y = start_y + random.uniform(-1, 1)
            await page.mouse.move(curr_x, curr_y)

        await page.wait_for_timeout(random.randint(50, 100))
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
            logger.info(f"Manual CAPTCHA solved, token: {token[:20]}...")
            return token

        logger.warning("Manual CAPTCHA failed, no token")
        return None

    async def create_post(
        self,
        user_id: int,
        content: str,
        title: str = None,
        platform: str = "joinquant",
    ) -> dict:
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

            await page.screenshot(path="/tmp/jq_post_editor.png")

            is_logged_in = await page.evaluate("""() => {
                const loginBtn = document.querySelector('.jq-header-login') ||
                                 document.querySelector('a[href*="login"]');
                return !loginBtn || loginBtn.offsetParent === null;
            }""")

            if not is_logged_in:
                await self._cleanup(pw, browser)
                return {"success": False, "error": "登录已过期，请重新登录聚宽"}

            if not title:
                lines = content.strip().split("\n")
                first_line = lines[0].strip().lstrip("#").strip()
                title = first_line[:50] if first_line else f"股票分析 {time.strftime('%Y-%m-%d')}"
                if len(lines) > 1:
                    content = "\n".join(lines[1:]).strip()
                else:
                    content = content

            title_filled = await page.evaluate("""(title) => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const editVm = editEl && editEl.__vue__;
                if (!editVm) return 'no vue';
                if (!editVm.$data || !('articleTitle' in editVm.$data)) return 'no vue';

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
                if (!editVm.$data || !('markDwonContent' in editVm.$data)) return 'no vue';

                const textarea = document.querySelector('textarea') ||
                                 document.querySelector('.edit-content textarea') ||
                                 document.querySelector('[class*="markdown"] textarea');
                if (textarea) {
                    const nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLTextAreaElement.prototype, 'value'
                    ).set;
                    nativeSetter.call(textarea, content);
                    textarea.dispatchEvent(new Event('input', { bubbles: true }));
                    textarea.dispatchEvent(new Event('change', { bubbles: true }));
                }
                editVm.$data.markDwonContent = content;
                return 'ok';
            }""", content)
            logger.info(f"Content fill result: {content_filled}")

            await page.wait_for_timeout(1000)

            tag_selected = await page.evaluate("""() => {
                const editEl = document.querySelector('.jq-comunity-edit');
                const editVm = editEl && editEl.__vue__;
                if (!editVm) return 'no vue';

                if (editVm.$data.userChosedTags && editVm.$data.userChosedTags.length > 0) {
                    return 'already_set:' + JSON.stringify(editVm.$data.userChosedTags);
                }

                const tagSpans = document.querySelectorAll('.jq-comunity-edit span');
                const targetTags = ['心得', '讨论', '分享'];
                for (const span of tagSpans) {
                    const text = span.textContent.trim();
                    if (targetTags.indexOf(text) >= 0) {
                        span.click();
                        return 'clicked:' + text;
                    }
                }

                const allSpans = document.querySelectorAll('span');
                for (const span of allSpans) {
                    const text = span.textContent.trim();
                    if (text === '心得' || text === '讨论') {
                        span.click();
                        return 'clicked_global:' + text;
                    }
                }
                
                const sampleSpans = [];
                for (let i = 0; i < Math.min(20, allSpans.length); i++) {
                    const text = allSpans[i].textContent.trim();
                    if (text && text.length < 20) {
                        sampleSpans.push(text);
                    }
                }
                return 'no_tags_found, sample: ' + JSON.stringify(sampleSpans);
            }""")
            logger.info(f"Tag selection: {tag_selected}")

            await page.wait_for_timeout(500)
            await page.screenshot(path="/tmp/jq_post_before_submit.png")

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

                    if (typeof editVm.getNeedSubmitCode === 'function') {
                        try {
                            editVm.getNeedSubmitCode(function(needCode) {
                                if (needCode === 0) {
                                    editVm.postArticle();
                                    resolve({status: 'posted_directly', needCode: needCode});
                                } else {
                                    if (editVm.$refs && editVm.$refs.validCodeDiloag && editVm.$refs.validCodeDiloag.getCaptchar) {
                                        editVm.$refs.validCodeDiloag.getCaptchar();
                                    }
                                    editVm.$data.validCodeDialogVisible = true;
                                    resolve({status: 'captcha_needed', needCode: needCode});
                                }
                            });
                        } catch(e) {
                            editVm.postArticle();
                            resolve({status: 'posted_fallback', error: e.message});
                        }
                    } else {
                        editVm.postArticle();
                        resolve({status: 'posted_no_check'});
                    }
                });
            }""")
            logger.info(f"Post submission result: {post_result}")

            await page.wait_for_timeout(3000)

            captcha_needed = post_result.get("status") == "captcha_needed"
            if not captcha_needed:
                captcha_needed = await page.evaluate("""() => {
                    const editEl = document.querySelector('.jq-comunity-edit');
                    const editVm = editEl && editEl.__vue__;
                    if (!editVm) return false;
                    return !!(editVm.$data && editVm.$data.validCodeDialogVisible);
                }""")

            if captcha_needed:
                logger.info("CAPTCHA dialog detected, attempting to solve...")
                await page.wait_for_timeout(2000)

                if first_captcha_data:
                    logger.info(f"Using pre-captured CAPTCHA data: bgImgW={first_captcha_data.get('bgImgW')}, blockW={first_captcha_data.get('blockW')}, points={len(first_captcha_data.get('point', []))}")

                captcha_api_data = {}
                async def on_captcha_api_response(response):
                    if "verifyCode/captchar" in response.url and response.request.method == "POST":
                        try:
                            body = await response.json()
                            if body.get("code") == "00000":
                                data = body.get("data", {})
                                captcha_api_data["data"] = data
                                non_img = {k: v for k, v in data.items() if k not in ('bgImg', 'hqImg')}
                                logger.info(f"CAPTCHA API raw response fields: {non_img}")
                                logger.info(f"CAPTCHA API all keys: {list(data.keys())}")
                        except Exception as e:
                            logger.error(f"Failed to parse CAPTCHA API response: {e}")

                page.on("response", on_captcha_api_response)

                for attempt in range(3):
                    logger.info(f"CAPTCHA solve attempt {attempt + 1}/3")

                    token = await self._solve_captcha(page, pre_captured_data=first_captcha_data if attempt == 0 else None, user_id=user_id)

                    if token:
                        logger.info(f"Got CAPTCHA token: {token[:20]}...")

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
                                    editVm.postArticle();
                                    return 'postArticle recalled';
                                } catch(e2) {
                                    return 'error: ' + e2.message;
                                }
                            }
                        }""", token)
                        logger.info(f"Resubmit result: {resubmit}")

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
                page.remove_listener("response", on_captcha_api_response)

                if not token:
                    logger.info("Automatic CAPTCHA failed, switching to manual mode...")
                    manual_data = first_captcha_data or captcha_api_data.get("data", {})
                    if manual_data:
                        token = await self._solve_captcha_manual(page, manual_data, user_id)
                        if token:
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
                                        editVm.postArticle();
                                        return 'postArticle recalled';
                                    } catch(e2) {
                                        return 'error: ' + e2.message;
                                    }
                                }
                            }""", token)
                            logger.info(f"Manual resubmit result: {resubmit}")
                            await page.wait_for_timeout(5000)
            else:
                logger.info("No CAPTCHA dialog, checking if post was submitted directly")
                await page.wait_for_timeout(3000)

            await page.screenshot(path="/tmp/jq_post_after_submit.png")

            await self._save_cookies(user_id, platform, context)
            await self._cleanup(pw, browser)

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

        except Exception as e:
            logger.error(f"JoinQuant post failed: {e}", exc_info=True)
            await self._cleanup(pw, browser)
            return {"success": False, "error": str(e)}
        finally:
            if storage_state_path:
                try:
                    Path(storage_state_path).unlink(missing_ok=True)
                    Path(storage_state_path).parent.rmdir()
                except Exception:
                    pass
