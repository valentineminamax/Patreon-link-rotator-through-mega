import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from patchright.async_api import async_playwright, Page
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
from mega_rotate import rotate_mega_link

# =========================== CONFIG ===========================
CONFIG = {
    "SESSION_PATH": "patreon_session.json",
    "POST_ID": os.environ.get("PATREON_POST_ID", "123456789"),
    "EDIT_URL": "https://www.patreon.com/posts/{post_id}/edit",
    "PUBLIC_URL": "https://www.patreon.com/posts/{post_id}",

    "MEGA_LINK_PATTERN": r"https://mega\.nz/folder/[A-Za-z0-9#_-]+",

    "LOCATOR_KEBAB_BUTTON": {"role": "button", "name": "Menu for additional actions"},
    "LOCATOR_DELETE_ITEM": {"role": "menuitem", "name": "Delete"},
    "LOCATOR_CONFIRM_DELETE": {"role": "button", "name": "Delete"},
    "LOCATOR_LINK_BUTTON_SELECTOR": "button:has-text('Link')",
    "LOCATOR_LINK_INPUT": {"role": "textbox", "name": "Type or paste URL"},
    "LOCATOR_UPDATE_BUTTON": {"role": "button", "name": "Update"},
    "LOCATOR_CONFIRMATION": {"role": "status", "name": re.compile(r"saved|updated", re.I)},

    "HEADLESS": True,
    "NAV_TIMEOUT_MS": 90000,
    "ACTION_TIMEOUT_MS": 60000,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("patreon_update")

def _get_proxy_config() -> Optional[dict]:
    """Build proxy dict from env vars."""
    host = os.getenv("PROXY_HOST")
    port = os.getenv("PROXY_PORT")
    username = os.getenv("PROXY_USERNAME")
    password = os.getenv("PROXY_PASSWORD")
    if host and port:
        proxy = {"server": f"http://{host}:{port}"}
        if username and password:
            proxy["username"] = username
            proxy["password"] = password
        log.info(f"Using proxy: {proxy['server']}")
        return proxy
    log.warning("No proxy configured – using direct connection.")
    return None

async def _handle_cloudflare(page: Page) -> None:
    """Detect and solve Cloudflare Turnstile or Interstitial."""
    title = await page.title()
    if "Just a moment..." in title or "Checking your browser" in title:
        log.info("Cloudflare challenge detected. Attempting bypass...")
        solver = ClickSolver(
            framework=FrameworkType.PATCHRIGHT,
            page=page,
            max_attempts=5,
            attempt_delay=5,
        )
        try:
            async with solver:
                try:
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE
                    )
                except Exception:
                    log.warning("Turnstile solver failed, trying Interstitial...")
                    await solver.solve_captcha(
                        captcha_container=page,
                        captcha_type=CaptchaType.CLOUDFLARE_INTERSTITIAL
                    )
            log.info("Cloudflare challenge solved.")
            await page.wait_for_load_state("networkidle", timeout=60000)
            await asyncio.sleep(2)
        except Exception as e:
            await page.screenshot(path="cloudflare_failure.png", full_page=True)
            with open("cloudflare_failure.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise RuntimeError(f"Cloudflare bypass failed: {e}")

async def _replace_link_in_editor(page: Page, new_link: str) -> None:
    """Execute the exact manual workflow (one Update click)."""
    await page.get_by_role(
        CONFIG["LOCATOR_KEBAB_BUTTON"]["role"],
        name=CONFIG["LOCATOR_KEBAB_BUTTON"]["name"]
    ).click()

    await page.get_by_role(
        CONFIG["LOCATOR_DELETE_ITEM"]["role"],
        name=CONFIG["LOCATOR_DELETE_ITEM"]["name"]
    ).click()

    await page.get_by_role(
        CONFIG["LOCATOR_CONFIRM_DELETE"]["role"],
        name=CONFIG["LOCATOR_CONFIRM_DELETE"]["name"]
    ).click()

    await page.locator(CONFIG["LOCATOR_LINK_BUTTON_SELECTOR"]).click()

    await page.get_by_role(
        CONFIG["LOCATOR_LINK_INPUT"]["role"],
        name=CONFIG["LOCATOR_LINK_INPUT"]["name"]
    ).fill(new_link)

    await page.get_by_role(
        CONFIG["LOCATOR_UPDATE_BUTTON"]["role"],
        name=CONFIG["LOCATOR_UPDATE_BUTTON"]["name"]
    ).click()
    await asyncio.sleep(2)

    try:
        await page.get_by_role(
            CONFIG["LOCATOR_CONFIRMATION"]["role"],
            name=CONFIG["LOCATOR_CONFIRMATION"]["name"]
        ).wait_for(state="visible", timeout=15000)
        log.info("Post update confirmed.")
    except Exception:
        log.warning("Confirmation toast not found, but proceeding.")

async def update_patreon_post(new_link: str) -> None:
    """Main async flow to edit the Patreon post."""
    session_path = Path(CONFIG["SESSION_PATH"])
    if not session_path.exists():
        raise FileNotFoundError(f"Session file not found: {session_path}")

    edit_url = CONFIG["EDIT_URL"].format(post_id=CONFIG["POST_ID"])
    public_url = CONFIG["PUBLIC_URL"].format(post_id=CONFIG["POST_ID"])
    proxy_config = _get_proxy_config()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=CONFIG["HEADLESS"],
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--window-size=1920,1080",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        context = await browser.new_context(
            storage_state=str(session_path),
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36"
            ),
            proxy=proxy_config,
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = await context.new_page()
        page.set_default_timeout(CONFIG["ACTION_TIMEOUT_MS"])
        page.set_default_navigation_timeout(CONFIG["NAV_TIMEOUT_MS"])

        try:
            log.info("Opening editor: %s", edit_url)
            await page.goto(edit_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            await _handle_cloudflare(page)
            await _replace_link_in_editor(page, new_link)

            log.info("Verifying public post: %s", public_url)
            await page.goto(public_url, wait_until="domcontentloaded")
            content = await page.content()
            if new_link not in content:
                raise RuntimeError(
                    "Verification failed: new link not found on live post. "
                    "NOT cleaning up old MEGA folder."
                )
            log.info("Verification passed.")

        except Exception:
            try:
                await page.screenshot(path="patreon_failure.png", full_page=True)
            except Exception:
                pass
            try:
                with open("patreon_failure.html", "w", encoding="utf-8") as f:
                    f.write(await page.content())
            except Exception:
                pass
            raise
        finally:
            await browser.close()

def _rollback_sync(temp_path: str) -> None:
    import subprocess
    log.info("Rolling back: removing temp folder %s", temp_path)
    subprocess.run(["mega-rm", "-r", temp_path], capture_output=True, text=True)

def _cleanup_old_sync(old_path: str) -> None:
    import subprocess
    log.info("Cleaning up old folder: %s", old_path)
    # Try to disable export (may fail if already disabled, ignore errors)
    subprocess.run(["mega-export", "-d", old_path], capture_output=True, text=True)
    subprocess.run(["mega-rm", "-r", old_path], capture_output=True, text=True)

async def main():
    log.info("=== Starting MEGA + Patreon link rotation ===")
    loop = asyncio.get_running_loop()

    try:
        result = await loop.run_in_executor(None, rotate_mega_link)
        # Unpack flexibly: handle 3 or 4 values
        if len(result) == 4:
            new_link, old_path, old_name, temp_path = result
            log.info(f"Old folder name: {old_name}")
        elif len(result) == 3:
            new_link, old_path, temp_path = result
            old_name = old_path.split('/')[-1] if '/' in old_path else old_path
            log.info(f"Old folder name (inferred): {old_name}")
        else:
            raise ValueError(f"Unexpected number of return values: {len(result)}")
    except Exception as e:
        log.error(f"Failed to rotate MEGA link: {e}")
        raise

    log.info("New MEGA link ready: %s", new_link)

    try:
        await update_patreon_post(new_link)
    except Exception:
        log.exception("Patreon update failed – rolling back temp folder")
        await loop.run_in_executor(None, _rollback_sync, temp_path)
        raise

    log.info("Verified. Cleaning up old folder %s", old_path)
    await loop.run_in_executor(None, _cleanup_old_sync, old_path)
    log.info("=== Done. Old export (%s) disabled. ===", old_name)

if __name__ == "__main__":
    asyncio.run(main())
