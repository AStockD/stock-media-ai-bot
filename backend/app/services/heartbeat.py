"""Periodic heartbeat check for platform login sessions."""
import json
import logging
import random
import threading
import time

import httpx

from app.database import get_db

logger = logging.getLogger(__name__)

CHECK_INTERVAL_MIN = 600
CHECK_INTERVAL_MAX = 1200

XUEQIU_CHECK_URL = "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json?size=1&category=1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://xueqiu.com/",
    "Origin": "https://xueqiu.com",
}


def _check_xueqiu(cookies: dict) -> bool:
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        resp = httpx.get(
            XUEQIU_CHECK_URL,
            headers={**HEADERS, "Cookie": cookie_str},
            timeout=15,
        )
        data = resp.json()
        return data.get("error_code", -1) == 0
    except Exception as e:
        logger.warning(f"Xueqiu heartbeat request failed: {e}")
        return False


def _check_joinquant(cookies: dict) -> bool:
    """Check JoinQuant login status using Playwright to load page and check for login button."""
    import asyncio
    from playwright.async_api import async_playwright
    
    async def _check():
        pw = None
        browser = None
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
            )
            
            # Build cookie list for context
            cookie_list = [{'name': k, 'value': v, 'domain': '.joinquant.com', 'path': '/'} for k, v in cookies.items()]
            
            context = await browser.new_context(
                viewport={'width': 1440, 'height': 900},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
            await context.add_cookies(cookie_list)
            
            page = await context.new_page()
            await page.goto('https://www.joinquant.com/view/community/list', wait_until='networkidle', timeout=30000)
            await page.wait_for_timeout(3000)
            
            # Check if login button is present
            has_login = await page.evaluate('''() => {
                const text = document.body.innerText;
                return text.includes('登录') || text.includes('立即登录');
            }''')
            
            await context.close()
            return not has_login  # If no login button, cookies are valid
            
        except Exception as e:
            logger.warning(f"JoinQuant heartbeat check failed: {e}")
            return False
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass
            if pw:
                try:
                    await pw.stop()
                except:
                    pass
    
    # Run async function in sync context
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_check())
    finally:
        loop.close()


def _run_check():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, platform, cookies_json FROM platform_accounts WHERE is_valid = 1"
            )
            accounts = cur.fetchall()

    for acc in accounts:
        if not acc["cookies_json"]:
            continue
        cookies = json.loads(acc["cookies_json"])
        platform = acc["platform"]

        if platform == "xueqiu":
            valid = _check_xueqiu(cookies)
        elif platform == "joinquant":
            valid = _check_joinquant(cookies)
        else:
            continue

        if not valid:
            logger.warning(f"Heartbeat FAILED: user={acc['user_id']} platform={platform} account_id={acc['id']}")
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE platform_accounts SET is_valid = 0 WHERE id = %s",
                        (acc["id"],),
                    )
        else:
            logger.info(f"Heartbeat OK: user={acc['user_id']} platform={platform}")


def _heartbeat_loop():
    while True:
        interval = random.randint(CHECK_INTERVAL_MIN, CHECK_INTERVAL_MAX)
        logger.info(f"Heartbeat: next check in {interval // 60}m{interval % 60}s")
        time.sleep(interval)
        try:
            _run_check()
        except Exception as e:
            logger.error(f"Heartbeat check error: {e}")


def start_heartbeat():
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    t.start()
    logger.info("Heartbeat checker started")
