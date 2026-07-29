"""Shared JoinQuant sliding CAPTCHA service used by both login and post flows."""
import asyncio
import base64
import logging
import random
import struct

from app.services.jq_captcha_solver import generate_trajectory

logger = logging.getLogger(__name__)


class JoinQuantCaptchaService:

    @staticmethod
    async def take_screenshots(page) -> tuple[str, str]:
        """Take background and piece screenshots with piece hidden in bg.

        Returns (bg_data_uri, piece_data_uri).
        """
        bg_screenshot = ""
        piece_screenshot = ""
        try:
            await page.evaluate("""() => {
                const piece = document.querySelector('.valid-code__img, .valid-code__div img[style*="position"], .valid-code__div img');
                if (piece) piece.style.visibility = 'hidden';
                const drag = document.querySelector('.valid-code__drag, [class*="drag"]:not([class*="handle"])');
                if (drag) drag.style.visibility = 'hidden';
                const handle = document.querySelector('.valid-code__drag-handle, [class*="drag-handle"]');
                if (handle) handle.style.visibility = 'hidden';
            }""")
            await page.wait_for_timeout(100)

            bg_el = await page.query_selector('#yth_captchar, .valid-code__div')
            if bg_el:
                bg_bytes = await bg_el.screenshot()
                bg_screenshot = "data:image/png;base64," + base64.b64encode(bg_bytes).decode()
                logger.info(f"CAPTCHA bg screenshot: {len(bg_bytes)} bytes")
                if len(bg_bytes) > 30:
                    w, h = struct.unpack('>II', bg_bytes[16:24])
                    logger.info(f"CAPTCHA bg screenshot dimensions: {w}x{h}")
            else:
                logger.warning("CAPTCHA bg element not found")

            await page.evaluate("""() => {
                const piece = document.querySelector('.valid-code__img, .valid-code__div img[style*="position"], .valid-code__div img');
                if (piece) piece.style.visibility = 'visible';
                const drag = document.querySelector('.valid-code__drag, [class*="drag"]:not([class*="handle"])');
                if (drag) drag.style.visibility = 'visible';
                const handle = document.querySelector('.valid-code__drag-handle, [class*="drag-handle"]');
                if (handle) handle.style.visibility = 'visible';
            }""")
        except Exception as e:
            logger.error(f"Failed to screenshot CAPTCHA bg: {e}")

        try:
            piece_el = await page.query_selector('.valid-code__img')
            if not piece_el:
                piece_el = await page.query_selector('.valid-code__div img[style*="position"]')
            if not piece_el:
                piece_el = await page.query_selector('.valid-code__div img')
            if piece_el:
                piece_bytes = await piece_el.screenshot()
                piece_screenshot = "data:image/png;base64," + base64.b64encode(piece_bytes).decode()
                logger.info(f"CAPTCHA piece screenshot: {len(piece_bytes)} bytes")
            else:
                logger.warning("CAPTCHA piece element not found")
        except Exception as e:
            logger.error(f"Failed to screenshot CAPTCHA piece: {e}")

        return bg_screenshot, piece_screenshot

    @staticmethod
    async def simulate_drag(page, axis_x: int, expected_bg_width: int) -> dict:
        """Simulate slider drag and capture validation response.

        Args:
            axis_x: User-provided X offset (in original image coordinates).
            expected_bg_width: Expected background image width from API data.

        Returns:
            {body: dict, success: bool}
        """
        handle = await page.query_selector(
            '.valid-code__drag-handle, [class*="drag-handle"]'
        )
        if not handle:
            return {"body": {}, "success": False, "error": "找不到验证码滑块"}

        handle_box = await handle.bounding_box()
        if not handle_box:
            return {"body": {}, "success": False, "error": "滑块位置不可用"}

        captcha_el = await page.query_selector('#yth_captchar, .valid-code__div')
        actual_width = 363
        if captcha_el:
            box = await captcha_el.bounding_box()
            if box:
                actual_width = box["width"]

        scale = actual_width / expected_width if expected_width > 0 else 1
        scaled_x = axis_x * scale
        logger.info(
            f"Drag scale: {scale} (actual={actual_width}, expected={expected_width}), "
            f"axis_x={axis_x} -> scaled={scaled_x}"
        )

        start_x = handle_box["x"] + handle_box["width"] / 2
        start_y = handle_box["y"] + handle_box["height"] / 2

        validate_result = {}

        async def on_response(response):
            if "verifyCode/validate" in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    validate_result["body"] = body
                    logger.info(f"CAPTCHA validate response: {body}")
                except Exception as e:
                    logger.error(f"Failed to parse validate response: {e}")

        page.on("response", on_response)

        steps = 30
        await page.mouse.move(start_x, start_y)
        await page.wait_for_timeout(random.randint(50, 150))
        await page.mouse.down()
        await page.wait_for_timeout(random.randint(80, 200))

        for i in range(1, steps + 1):
            progress = i / steps
            curr_x = start_x + scaled_x * progress
            curr_y = start_y + random.uniform(-1.5, 1.5)
            await page.mouse.move(curr_x, curr_y)
            await page.wait_for_timeout(random.randint(8, 25))

        await page.wait_for_timeout(random.randint(50, 150))
        await page.mouse.up()

        logger.info("Drag simulation completed")
        await page.wait_for_timeout(500)

        page.remove_listener("response", on_response)

        await page.wait_for_timeout(3000)

        body = validate_result.get("body", {})
        data = body.get("data", {})
        result = data.get("result", False)

        return {"body": body, "success": bool(result)}

    @staticmethod
    async def refresh(page, refresh_fn, expected_fields: dict = None) -> dict:
        """Refresh CAPTCHA and return new data.

        Args:
            page: Playwright page.
            refresh_fn: Async callable that triggers a CAPTCHA refresh.
            expected_fields: Dict with bgImgW, bgImgH, blockW, blockH defaults.
        """
        expected_fields = expected_fields or {}
        captcha_holder = {}

        async def on_response(response):
            if "verifyCode/captchar" in response.url and response.request.method == "POST":
                try:
                    body = await response.json()
                    if body.get("code") == "00000":
                        captcha_holder["data"] = body.get("data", {})
                except Exception:
                    pass

        page.on("response", on_response)
        await refresh_fn()
        await page.wait_for_timeout(2000)
        page.remove_listener("response", on_response)

        new_data = captcha_holder.get("data")
        if not new_data:
            return {}

        bg_img_w = new_data.get("bgImgW", expected_fields.get("bgImgW", 363))
        bg_img_h = new_data.get("bgImgH", expected_fields.get("bgImgH", 142))

        bg_screenshot, piece_screenshot = await JoinQuantCaptchaService.take_screenshots(page)

        return {
            "bgImg": bg_screenshot or new_data.get("bgImg", ""),
            "hqImg": piece_screenshot or new_data.get("hqImg", ""),
            "bgImgW": bg_img_w,
            "bgImgH": bg_img_h,
            "blockW": new_data.get("blockW", expected_fields.get("blockW", 11)),
            "blockH": new_data.get("blockH", expected_fields.get("blockH", 71)),
            "point": new_data.get("point", []),
            "axisY": new_data.get("axisY", 0),
        }

    @staticmethod
    async def extract_post_captcha_data(page, api_data: dict = None) -> dict:
        """Extract CAPTCHA data from post page Vue component + API interception.

        Returns standardized captcha_data dict with data URI images.
        """
        api_data = api_data or {}

        vue_data = await page.evaluate("""() => {
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

        captcha_data = vue_data or {}

        if api_data:
            captcha_data["point"] = api_data.get("point", [])
            captcha_data["blockW"] = api_data.get("blockW", 11)
            captcha_data["blockH"] = api_data.get("blockH", 71)
            if api_data.get("bgImgW"):
                captcha_data["bgImgW"] = api_data["bgImgW"]

        bg_img = captcha_data.get("bgImg", "")
        if bg_img and not bg_img.startswith("data:"):
            captcha_data["bgImg"] = f"data:image/png;base64,{bg_img}"

        hq_img = captcha_data.get("hqImg", "")
        if hq_img and not hq_img.startswith("data:"):
            captcha_data["hqImg"] = f"data:image/png;base64,{hq_img}"

        bg_screenshot, piece_screenshot = await JoinQuantCaptchaService.take_screenshots(page)
        if bg_screenshot:
            captcha_data["bgImg"] = bg_screenshot
        if piece_screenshot:
            captcha_data["hqImg"] = piece_screenshot

        bg_img_w = captcha_data.get("bgImgW", api_data.get("bgImgW", 320))

        return {
            "bgImg": captcha_data.get("bgImg", ""),
            "hqImg": captcha_data.get("hqImg", ""),
            "bgImgW": bg_img_w,
            "bgImgH": captcha_data.get("bgImgH", api_data.get("bgImgH", 142)),
            "blockW": captcha_data.get("blockW", api_data.get("blockW", 11)),
            "blockH": captcha_data.get("blockH", api_data.get("blockH", 71)),
            "point": captcha_data.get("point", []),
            "axisY": captcha_data.get("axisY", 0),
        }
