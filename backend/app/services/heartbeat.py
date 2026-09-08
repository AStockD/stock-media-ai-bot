"""Periodic heartbeat: check and renew platform login sessions every 2 hours."""
import asyncio
import json
import logging
import threading
import time

import httpx

from app.database import get_db

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 7200

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

XUEQIU_CHECK_URL = "https://stock.xueqiu.com/v5/stock/portfolio/stock/list.json?size=1&category=1"
XUEQIU_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://xueqiu.com/",
    "Origin": "https://xueqiu.com",
}

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});
window.chrome = { runtime: {} };
"""


def _check_zsxq(user_id: int) -> bool:
    try:
        from app.services.zsxq_cli import ZsxqCliError, run_cli, unwrap_data

        result = run_cli(user_id, ["auth", "status"], timeout=30)
        data = unwrap_data(result) or {}
        if isinstance(data, dict) and data.get("loggedIn") is False:
            return False
        return True
    except Exception as e:
        logger.warning(f"Zsxq heartbeat failed: user={user_id}: {e}")
        return False


def _check_xueqiu(cookies: dict) -> bool:
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        resp = httpx.get(
            XUEQIU_CHECK_URL,
            headers={**XUEQIU_HEADERS, "Cookie": cookie_str},
            timeout=15,
        )
        data = resp.json()
        return data.get("error_code", -1) == 0
    except Exception as e:
        logger.warning(f"Xueqiu heartbeat failed: {e}")
        return False


async def _check_and_renew_joinquant(user_id: int, account_id: int,
                                     cookies: dict, storage_state: dict,
                                     credentials: dict | None) -> bool:
    """Check JoinQuant session validity. If valid, save refreshed cookies.
    If expired and credentials available, attempt auto re-login."""
    from playwright.async_api import async_playwright

    pw = None
    browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
        )

        context = await browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()

        await page.goto(
            "https://www.joinquant.com/view/community/list",
            wait_until="networkidle",
            timeout=30000,
        )
        await page.wait_for_timeout(3000)

        has_login = await page.evaluate("""() => {
            const text = document.body.innerText;
            return text.includes('登录') || text.includes('立即登录');
        }""")

        if not has_login:
            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            new_storage = await context.storage_state()
            await context.close()

            from app.services.account_manager import account_manager
            account_manager.update_cookies(user_id, "joinquant", cookies_dict, new_storage)
            logger.info(f"JoinQuant session renewed: user={user_id}")
            return True

        await context.close()
        logger.warning(f"JoinQuant session expired: user={user_id}")

        if credentials:
            return await _auto_relogin_joinquant(
                pw, browser, user_id, credentials["username"], credentials["password"],
            )

        return False

    except Exception as e:
        logger.error(f"JoinQuant heartbeat check failed: user={user_id}: {e}")
        return False
    finally:
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


async def _auto_relogin_joinquant(pw, browser, user_id: int,
                                  username: str, password: str) -> bool:
    """Attempt automatic re-login. Returns True on success."""
    from app.config import JQ_LOGIN_URL
    from app.services.account_manager import account_manager

    try:
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=USER_AGENT,
        )
        await context.add_init_script(STEALTH_SCRIPT)
        page = await context.new_page()

        await page.goto(JQ_LOGIN_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        checkbox = await page.query_selector("input[type=checkbox]")
        if checkbox and not await checkbox.is_checked():
            await checkbox.check(force=True)

        await page.fill("input[name=username]", username)
        await page.fill("input[name=pwd]", password)
        await page.click("button.btnPwdSubmit")
        await page.wait_for_timeout(5000)

        captcha_visible = await page.evaluate("""() => {
            const modal = document.querySelector('.validCode-dialog, [class*="validCode"]');
            if (!modal) return false;
            return window.getComputedStyle(modal).display !== 'none';
        }""")

        if captcha_visible:
            logger.warning(f"Auto re-login blocked by CAPTCHA: user={user_id}")
            await context.close()
            return False

        if "login" in page.url.lower():
            logger.warning(f"Auto re-login failed (still on login page): user={user_id}")
            await context.close()
            return False

        cookies_list = await context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies_list}
        storage_state = await context.storage_state()
        await context.close()

        account_manager.save_cookies(
            user_id=user_id,
            platform="joinquant",
            cookies=cookies_dict,
            storage_state=storage_state,
            credentials={"username": username, "password": password},
        )
        logger.info(f"JoinQuant auto re-login succeeded: user={user_id}")
        return True

    except Exception as e:
        logger.error(f"Auto re-login failed: user={user_id}: {e}")
        return False


def _run_joinquant_check(user_id, account_id, cookies, storage_state, credentials):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            _check_and_renew_joinquant(user_id, account_id, cookies, storage_state, credentials)
        )
    finally:
        loop.close()


def _run_check():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, platform, cookies_json, storage_state_json, credentials_json "
                "FROM platform_accounts WHERE is_valid = 1"
            )
            accounts = cur.fetchall()

    for acc in accounts:
        platform = acc["platform"]

        if not acc["cookies_json"]:
            continue

        cookies = json.loads(acc["cookies_json"])

        if platform == "xueqiu":
            valid = _check_xueqiu(cookies)
            if not valid:
                logger.warning(f"Heartbeat FAILED: user={acc['user_id']} platform={platform}")
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE platform_accounts SET is_valid = 0 WHERE id = %s",
                            (acc["id"],),
                        )
            else:
                logger.info(f"Heartbeat OK: user={acc['user_id']} platform={platform}")

        elif platform == "joinquant":
            storage_state = json.loads(acc["storage_state_json"]) if acc.get("storage_state_json") else None
            credentials = json.loads(acc["credentials_json"]) if acc.get("credentials_json") else None

            if not storage_state:
                logger.warning(f"No storage_state for JoinQuant user={acc['user_id']}, skipping")
                continue

            valid = _run_joinquant_check(
                acc["user_id"], acc["id"], cookies, storage_state, credentials,
            )
            if not valid:
                logger.warning(f"Heartbeat FAILED: user={acc['user_id']} platform={platform}")
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE platform_accounts SET is_valid = 0 WHERE id = %s",
                            (acc["id"],),
                        )

        elif platform == "zsxq":
            valid = _check_zsxq(acc["user_id"])
            if not valid:
                logger.warning(f"Heartbeat FAILED: user={acc['user_id']} platform={platform}")
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE platform_accounts SET is_valid = 0 WHERE id = %s",
                            (acc["id"],),
                        )
            else:
                logger.info(f"Heartbeat OK: user={acc['user_id']} platform={platform}")
        else:
            continue


def _heartbeat_loop():
    while True:
        logger.info(f"Heartbeat: next check in {CHECK_INTERVAL // 60}m")
        time.sleep(CHECK_INTERVAL)
        try:
            _run_check()
        except Exception as e:
            logger.error(f"Heartbeat check error: {e}")


def start_heartbeat():
    t = threading.Thread(target=_heartbeat_loop, daemon=True, name="heartbeat")
    t.start()
    logger.info("Heartbeat checker started (interval: 2h)")
