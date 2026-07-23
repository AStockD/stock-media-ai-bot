"""Shared JoinQuant sliding CAPTCHA solver using OpenCV."""
import asyncio
import base64
import json
import logging
import math
import random
import time

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def find_gap_x_with_point(bg_img_b64: str, point: list, block_w: int = 11, block_h: int = 71, hq_img_b64: str = None) -> int:
    """
    Find the gap X position using the puzzle piece for template matching.
    
    Uses the puzzle piece (hqImg) to find where it fits in the shuffled background.
    """
    def fix_b64_padding(b64str: str) -> str:
        b64str = b64str.strip()
        if b64str.startswith('data:'):
            b64str = b64str.split(',', 1)[1] if ',' in b64str else b64str
        missing_padding = len(b64str) % 4
        if missing_padding:
            b64str += '=' * (4 - missing_padding)
        return b64str
    
    bg_bytes = base64.b64decode(fix_b64_padding(bg_img_b64))
    bg_arr = np.frombuffer(bg_bytes, dtype=np.uint8)
    shuffled = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)
    
    if shuffled is None:
        logger.error("Failed to decode background image")
        return 0
    
    cv2.imwrite('/app/data/debug_shuffled.png', shuffled)
    
    img_h, img_w = shuffled.shape[:2]
    logger.info(f"Analyzing shuffled image: {img_w}x{img_h}")
    
    if hq_img_b64:
        piece_bytes = base64.b64decode(fix_b64_padding(hq_img_b64))
        piece_arr = np.frombuffer(piece_bytes, dtype=np.uint8)
        piece = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)
        
        if piece is not None:
            cv2.imwrite('/app/data/debug_piece.png', piece)
            piece_h, piece_w = piece.shape[:2]
            logger.info(f"Puzzle piece size: {piece_w}x{piece_h}")
            
            gray_bg = cv2.cvtColor(shuffled, cv2.COLOR_BGR2GRAY)
            gray_piece = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)
            
            blurred_bg = cv2.GaussianBlur(gray_bg, (5, 5), 0)
            blurred_piece = cv2.GaussianBlur(gray_piece, (5, 5), 0)
            
            result = cv2.matchTemplate(blurred_bg, blurred_piece, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            logger.info(f"Template matching: max_val={max_val:.3f}, max_loc={max_loc}")
            
            if max_val > 0.3:
                gap_x = max_loc[0]
                logger.info(f"Gap found via template matching at x={gap_x} (left edge)")
                cv2.imwrite('/app/data/debug_match_result.png', result)
                return gap_x
            
            logger.warning(f"Template matching confidence too low: {max_val:.3f}")
    
    gray = cv2.cvtColor(shuffled, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    col_edge_counts = np.sum(edges > 0, axis=0)
    window_size = block_w
    col_edge_sums = np.convolve(col_edge_counts, np.ones(window_size), mode='valid')
    
    min_edges = np.percentile(col_edge_sums, 10)
    max_edges = np.percentile(col_edge_sums, 90)
    threshold = (min_edges + max_edges) / 2
    low_edge_cols = np.where(col_edge_sums < threshold)[0]
    
    if len(low_edge_cols) > 0:
        regions = []
        start = low_edge_cols[0]
        prev = low_edge_cols[0]
        for col in low_edge_cols[1:]:
            if col == prev + 1:
                prev = col
            else:
                if prev - start >= block_w * 0.8:
                    regions.append((start, prev))
                start = col
                prev = col
        if prev - start >= block_w * 0.8:
            regions.append((start, prev))
        
        logger.info(f"Low-edge regions (fallback): {regions[:5]}")
        
        if regions:
            largest_gap = max(regions, key=lambda r: r[1] - r[0])
            gap_x = (largest_gap[0] + largest_gap[1]) // 2
            logger.info(f"Gap found via edge analysis at region {largest_gap}, center x={gap_x}")
            return gap_x
    
    logger.warning("All gap detection methods failed")
    return img_w // 2


def find_gap_x(bg_img_b64: str, piece_img_b64: str) -> int:
    """Use OpenCV to find the X offset of the puzzle gap in the background image."""
    def fix_b64_padding(b64str: str) -> str:
        b64str = b64str.strip()
        if b64str.startswith('data:'):
            b64str = b64str.split(',', 1)[1] if ',' in b64str else b64str
        missing_padding = len(b64str) % 4
        if missing_padding:
            b64str += '=' * (4 - missing_padding)
        return b64str
    
    bg_bytes = base64.b64decode(fix_b64_padding(bg_img_b64))
    piece_bytes = base64.b64decode(fix_b64_padding(piece_img_b64))

    bg_arr = np.frombuffer(bg_bytes, dtype=np.uint8)
    piece_arr = np.frombuffer(piece_bytes, dtype=np.uint8)

    bg = cv2.imdecode(bg_arr, cv2.IMREAD_COLOR)
    piece = cv2.imdecode(piece_arr, cv2.IMREAD_COLOR)

    if bg is None or piece is None:
        raise ValueError("Failed to decode CAPTCHA images")

    bg_gray = cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY)
    piece_gray = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)

    bg_h, bg_w = bg_gray.shape[:2]
    piece_h, piece_w = piece_gray.shape[:2]
    logger.info(f"Image sizes: bg={bg_w}x{bg_h}, piece={piece_w}x{piece_h}")

    bg_diff = cv2.absdiff(bg_gray, cv2.GaussianBlur(bg_gray, (21, 21), 0))
    _, thresh = cv2.threshold(bg_diff, 15, 255, cv2.THRESH_BINARY)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=3)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
    
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    gap_x = None
    best_score = 0
    
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if area < 500 or area > (bg_w * bg_h * 0.25):
            continue
        aspect = w / h if h > 0 else 0
        if 0.2 < aspect < 5.0:
            score = area / (bg_w * bg_h)
            if score > best_score:
                best_score = score
                gap_x = x
    
    if gap_x is not None:
        logger.info(f"Gap found via adaptive threshold at x={gap_x} (score={best_score:.3f})")
        return gap_x

    ph, pw = piece_gray.shape[:2]
    bg_blur = cv2.GaussianBlur(bg_gray, (3, 3), 0)
    piece_blur = cv2.GaussianBlur(piece_gray, (3, 3), 0)
    result = cv2.matchTemplate(bg_blur, piece_blur, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    gap_x = max_loc[0]
    logger.info(f"Gap found via template matching at x={gap_x} (confidence={max_val:.3f})")
    return gap_x


def generate_trajectory(distance: int) -> list[dict]:
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


async def solve_sliding_captcha(page, slider_selector: str = None) -> bool:
    """
    Solve a JoinQuant sliding CAPTCHA on the given page.

    Works for both login and post CAPTCHAs. Intercepts the API response,
    uses OpenCV to find the gap, and drags the slider.

    Returns True if the CAPTCHA was solved successfully.
    """
    captcha_data_holder = {}

    async def on_response(response):
        url = response.url
        if "verifyCode/captchar" in url and response.request.method == "POST":
            try:
                body = await response.json()
                if body.get("code") == "00000":
                    captcha_data_holder["data"] = body.get("data", {})
                    logger.info(f"CAPTCHA data captured: bgImgW={body['data'].get('bgImgW')}")
            except Exception as e:
                logger.error(f"Failed to parse CAPTCHA response: {e}")

    page.on("response", on_response)

    try:
        for attempt in range(3):
            logger.info(f"CAPTCHA solve attempt {attempt + 1}/3")
            captcha_data_holder.clear()

            if slider_selector:
                refresh = await page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (el) el.click();
                    return !!el;
                }""", slider_selector)
                if not refresh:
                    logger.warning(f"Slider refresh element not found: {slider_selector}")

            await page.wait_for_timeout(2000)

            captcha_data = captcha_data_holder.get("data")
            if not captcha_data:
                logger.warning("No CAPTCHA data from API, trying DOM extraction...")
                captcha_data = await page.evaluate("""() => {
                    const items = document.querySelectorAll('.valid-code__div-item');
                    if (items.length === 0) return null;
                    const firstBg = items[0].style.backgroundImage;
                    const match = firstBg.match(/url\("data:image\/png;base64,([^"]+)"\)/);
                    if (!match) return null;
                    const bgImgB64 = match[1];
                    const containerW = document.querySelector('#yth_captchar, .valid-code__div');
                    const bgImgW = containerW ? containerW.clientWidth : 363;
                    return { bgImg: bgImgB64, hqImg: bgImgB64, bgImgW: bgImgW };
                }""")
                if captcha_data:
                    logger.info(f"CAPTCHA data extracted from DOM: bgImgW={captcha_data.get('bgImgW')}")

            if not captcha_data:
                logger.error("Failed to get CAPTCHA data after retry")
                continue

            bg_img = captcha_data.get("bgImg", "")
            hq_img = captcha_data.get("hqImg", "")
            bg_img_w = captcha_data.get("bgImgW", 320)

            if not bg_img or not hq_img:
                logger.error("Missing CAPTCHA images")
                continue

            gap_x = find_gap_x(bg_img, hq_img)
            logger.info(f"CAPTCHA gap X: {gap_x}, bgImgW: {bg_img_w}")

            bg_bytes = base64.b64decode(bg_img)
            arr = np.frombuffer(bg_bytes, dtype=np.uint8)
            decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            actual_w = decoded.shape[1] if decoded is not None else bg_img_w

            captcha_display = await page.evaluate("""() => {
                const el = document.querySelector('#yth_captchar') ||
                           document.querySelector('.valid-code__div') ||
                           document.querySelector('[class*="validCode"]') ||
                           document.querySelector('[class*="valid-code"]');
                return el ? el.clientWidth : null;
            }""")

            display_w = captcha_display or actual_w
            scale = display_w / actual_w if actual_w > 0 else 1.0
            target_x = int(gap_x * scale)
            logger.info(f"Scale: {scale:.3f} (display={display_w}, actual={actual_w}), target_x={target_x}")

            handle = await page.query_selector(
                '.valid-code__drag-handle, '
                '[class*="slide-btn"], '
                '[class*="drag-handle"], '
                '[class*="handler"]'
            )
            if not handle:
                logger.error("Slider handle not found")
                continue

            handle_box = await handle.bounding_box()
            if not handle_box:
                logger.error("Slider handle bounding box not available")
                continue

            start_x = handle_box["x"] + handle_box["width"] / 2
            start_y = handle_box["y"] + handle_box["height"] / 2

            trajectory = generate_trajectory(target_x)

            await page.mouse.move(start_x, start_y)
            await page.wait_for_timeout(random.randint(100, 300))
            await page.mouse.down()
            await page.wait_for_timeout(random.randint(50, 150))

            for pt in trajectory:
                wait = random.uniform(0.008, 0.025)
                await asyncio.sleep(wait)
                curr_x = start_x + pt["x"]
                curr_y = start_y + random.uniform(-2, 2)
                await page.mouse.move(curr_x, curr_y)

            await page.wait_for_timeout(random.randint(50, 150))
            await page.mouse.up()

            await page.wait_for_timeout(3000)

            success = await page.evaluate("""() => {
                const modal = document.querySelector('.validCode-dialog, [class*="validCode"]');
                if (!modal) return 'no_modal';
                const display = window.getComputedStyle(modal).display;
                if (display === 'none') return 'hidden';
                const text = modal.textContent || '';
                if (text.includes('成功') || text.includes('通过')) return 'success_text';
                return 'still_visible';
            }""")
            logger.info(f"CAPTCHA modal state after solve: {success}")

            if success in ("hidden", "no_modal", "success_text"):
                logger.info("CAPTCHA solved successfully!")
                return True

            logger.warning(f"CAPTCHA attempt {attempt + 1} did not succeed (state: {success})")

        return False

    finally:
        page.remove_listener("response", on_response)


async def solve_sliding_captcha_with_data(page, captcha_data: dict) -> bool:
    """
    Solve a JoinQuant sliding CAPTCHA using pre-captured CAPTCHA data.

    Used when the CAPTCHA API response was already intercepted before calling this function.
    Returns True if the CAPTCHA was solved successfully.
    """
    if not captcha_data:
        logger.error("No CAPTCHA data provided")
        return False

    # Intercept validation requests to understand server response
    validation_results = []
    async def on_validate_response(response):
        if "verifyCode/validate" in response.url and response.request.method == "POST":
            try:
                body = await response.json()
                req_body = None
                try:
                    req_body = response.request.post_data
                except:
                    pass
                validation_results.append({"response": body, "request": req_body})
                logger.info(f"CAPTCHA validate response: {body}, request: {req_body}")
            except Exception as e:
                logger.error(f"Failed to parse validate response: {e}")

    page.on("response", on_validate_response)

    # Extract Vue component state for debugging
    try:
        vue_state = await page.evaluate("""() => {
            const result = {};
            // Find Vue component on the CAPTCHA dialog
            const dialog = document.querySelector('.validCode-dialog, [class*="validCode-dialog"]');
            if (dialog && dialog.__vue__) {
                const vue = dialog.__vue__;
                const data = vue.$data || {};
                result.dialogData = {};
                for (const key of Object.keys(data)) {
                    const val = data[key];
                    if (typeof val === 'string' && val.length > 200) {
                        result.dialogData[key] = `[string, len=${val.length}]`;
                    } else if (typeof val === 'object' && val !== null) {
                        result.dialogData[key] = JSON.stringify(val).substring(0, 200);
                    } else {
                        result.dialogData[key] = val;
                    }
                }
            }
            
            // Also check parent components
            const editEl = document.querySelector('.jq-comunity-edit, [class*="community-edit"]');
            if (editEl && editEl.__vue__) {
                const vue = editEl.__vue__;
                if (vue.$refs && vue.$refs.validCodeDiloag) {
                    const refData = vue.$refs.validCodeDiloag.$data || {};
                    result.refData = {};
                    for (const key of Object.keys(refData)) {
                        const val = refData[key];
                        if (typeof val === 'string' && val.length > 200) {
                            result.refData[key] = `[string, len=${val.length}]`;
                        } else {
                            result.refData[key] = val;
                        }
                    }
                }
            }
            
            // Get CAPTCHA element positions
            const items = document.querySelectorAll('.valid-code__div-item');
            result.items = [];
            for (const item of items) {
                const rect = item.getBoundingClientRect();
                const style = window.getComputedStyle(item);
                result.items.push({
                    x: Math.round(rect.x), y: Math.round(rect.y),
                    w: Math.round(rect.width), h: Math.round(rect.height),
                    bgPos: style.backgroundPosition || '',
                    left: style.left || '',
                    transform: style.transform || ''
                });
            }
            
            const handle = document.querySelector('.valid-code__drag-handle, [class*="drag-handle"]');
            if (handle) {
                const rect = handle.getBoundingClientRect();
                result.handle = {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)};
            }
            
            const drag = document.querySelector('.valid-code__drag, [class*="valid-code__drag"]');
            if (drag) {
                const rect = drag.getBoundingClientRect();
                result.drag = {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)};
            }
            
            return result;
        }""")
        logger.info(f"Vue dialogData: {json.dumps(vue_state.get('dialogData', {}), ensure_ascii=False, default=str)}")
        logger.info(f"Vue refData: {json.dumps(vue_state.get('refData', {}), ensure_ascii=False, default=str)}")
        logger.info(f"Vue handle: {vue_state.get('handle')}")
        logger.info(f"Vue drag: {vue_state.get('drag')}")
        logger.info(f"Vue items count: {len(vue_state.get('items', []))}")
        if vue_state.get('items'):
            logger.info(f"Vue first item: {vue_state['items'][0]}")
            logger.info(f"Vue last item: {vue_state['items'][-1]}")
    except Exception as e:
        logger.warning(f"Failed to extract Vue state: {e}")
    
    try:
        vue_answer = await page.evaluate("""() => {
            const result = {};
            
            const dialog = document.querySelector('.validCode-dialog, [class*="validCode-dialog"]');
            if (dialog && dialog.__vue__) {
                const vue = dialog.__vue__;
                
                if (vue.axisX !== undefined) result.axisX = vue.axisX;
                if (vue.axisY !== undefined) result.axisY = vue.axisY;
                if (vue.point !== undefined) result.point = vue.point;
                if (vue.gapX !== undefined) result.gapX = vue.gapX;
                if (vue.gapY !== undefined) result.gapY = vue.gapY;
                if (vue.x !== undefined) result.x = vue.x;
                if (vue.y !== undefined) result.y = vue.y;
                
                const methods = Object.keys(vue.$options.methods || {});
                result.methods = methods.slice(0, 20);
                
                const computed = Object.keys(vue.$options.computed || {});
                result.computed = computed.slice(0, 20);
                
                const data = vue.$data || {};
                for (const key of Object.keys(data)) {
                    const val = data[key];
                    if (typeof val === 'number' || typeof val === 'string') {
                        result['data_' + key] = val;
                    }
                }
            }
            
            return result;
        }""")
        logger.info(f"Vue answer inspection: {json.dumps(vue_answer, ensure_ascii=False, default=str)}")
    except Exception as e:
        logger.warning(f"Failed to inspect Vue answer: {e}")

    for attempt in range(3):
        logger.info(f"CAPTCHA solve attempt {attempt + 1}/3 (with pre-captured data)")

        bg_img = captcha_data.get("bgImg", "")
        hq_img = captcha_data.get("hqImg", "")
        bg_img_w = captcha_data.get("bgImgW", 320)
        point = captcha_data.get("point")
        block_w = captcha_data.get("blockW", 11)
        block_h = captcha_data.get("blockH", 71)

        if not bg_img or not hq_img:
            logger.error("Missing CAPTCHA images in provided data")
            return False

        if point and len(point) > 0:
            gap_x = find_gap_x_with_point(bg_img, point, block_w, block_h, hq_img)
            logger.info(f"CAPTCHA gap X (using point array): {gap_x}, bgImgW: {bg_img_w}")
        else:
            gap_x = find_gap_x(bg_img, hq_img)
            logger.info(f"CAPTCHA gap X (fallback): {gap_x}, bgImgW: {bg_img_w}")

        bg_b64_clean = bg_img.strip()
        if bg_b64_clean.startswith('data:'):
            bg_b64_clean = bg_b64_clean.split(',', 1)[1] if ',' in bg_b64_clean else bg_b64_clean
        missing = len(bg_b64_clean) % 4
        if missing:
            bg_b64_clean += '=' * (4 - missing)
        bg_bytes = base64.b64decode(bg_b64_clean)
        arr = np.frombuffer(bg_bytes, dtype=np.uint8)
        decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        actual_w = decoded.shape[1] if decoded is not None else bg_img_w

        captcha_display = await page.evaluate("""() => {
            const el = document.querySelector('#yth_captchar') ||
                       document.querySelector('.valid-code__div') ||
                       document.querySelector('[class*="validCode"]') ||
                       document.querySelector('[class*="valid-code"]');
            return el ? el.clientWidth : null;
        }""")

        display_w = captcha_display or actual_w
        scale = display_w / actual_w if actual_w > 0 else 1.0
        target_x = int(gap_x * scale)
        logger.info(f"Scale: {scale:.3f} (display={display_w}, actual={actual_w}), target_x={target_x}")

        handle = await page.query_selector(
            '.valid-code__drag-handle, '
            '[class*="slide-btn"], '
            '[class*="drag-handle"], '
            '[class*="handler"]'
        )
        if not handle:
            logger.error("Slider handle not found")
            return False

        handle_box = await handle.bounding_box()
        if not handle_box:
            logger.error("Slider handle bounding box not available")
            return False

        start_x = handle_box["x"] + handle_box["width"] / 2
        start_y = handle_box["y"] + handle_box["height"] / 2

        trajectory = generate_trajectory(target_x)

        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(random.randint(100, 300))
        await page.mouse.down()
        await page.wait_for_timeout(random.randint(50, 150))

        for pt in trajectory:
            wait = random.uniform(0.008, 0.025)
            await asyncio.sleep(wait)
            curr_x = start_x + pt["x"]
            curr_y = start_y + random.uniform(-2, 2)
            await page.mouse.move(curr_x, curr_y)

        await page.wait_for_timeout(random.randint(50, 150))
        await page.mouse.up()

        await page.wait_for_timeout(3000)

        success = await page.evaluate("""() => {
            const modal = document.querySelector('.validCode-dialog, [class*="validCode"]');
            if (!modal) return 'no_modal';
            const display = window.getComputedStyle(modal).display;
            if (display === 'none') return 'hidden';
            const text = modal.textContent || '';
            if (text.includes('成功') || text.includes('通过')) return 'success_text';
            return 'still_visible';
        }""")
        logger.info(f"CAPTCHA modal state after solve: {success}")

        if success in ("hidden", "no_modal", "success_text"):
            logger.info("CAPTCHA solved successfully!")
            return True

        logger.warning(f"CAPTCHA attempt {attempt + 1} did not succeed (state: {success})")

        if attempt < 2:
            refresh_clicked = await page.evaluate("""() => {
                const refresh = document.querySelector('.validCode-dialog .refresh, [class*="refresh"]');
                if (refresh) { refresh.click(); return true; }
                return false;
            }""")
            if refresh_clicked:
                await page.wait_for_timeout(2000)

    return False
